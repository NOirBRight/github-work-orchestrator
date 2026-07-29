from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass, replace
from copy import deepcopy
import hashlib
import inspect
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

import pytest


SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "orchestrator" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import gwo_v8  # noqa: E402
import gwo_v8._canonical as canonical_module  # noqa: E402
import gwo_v8.runtime_gateway as gateway_module  # noqa: E402
from gwo_v8._canonical import digest_value  # noqa: E402
from gwo_v8 import (  # noqa: E402
    CampaignPlanningSubject,
    PermissionResponse,
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
    CampaignStartRuntimeOverrides,
    ProfileMapping,
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


def _event_bound_observation(
    stable_action_id: str,
    *,
    lifecycle: str = "running",
    fenced: bool = False,
) -> gateway_module._BoundRuntimeObservation:
    return gateway_module._BoundRuntimeObservation(
        stable_action_id=stable_action_id,
        binding_ref=f"binding:{stable_action_id}",
        repository="owner/repository",
        campaign_key="campaign:event",
        campaign_handle="handle:event",
        plan_revision_digest=None,
        work_run_key=None,
        subject_digest="0" * 64,
        profile_digest="1" * 64,
        agent_id=f"agent:{stable_action_id}",
        session_id=f"session:{stable_action_id}",
        workspace_id=f"workspace:{stable_action_id}",
        prompt_artifact_digest="2" * 64,
        prompt_accepted=True,
        lifecycle=lifecycle,
        permission_requests=(),
        fenced=fenced,
        authority_subtree_digest="3" * 64,
        planning_output_artifact_digest=(
            "4" * 64 if lifecycle == "completed" else None
        ),
    )


def _event_observation_read(
    adapter,
    selected_stable_action_id: str,
    observation: gateway_module._PreparedRuntimeObservation
    | gateway_module._BoundRuntimeObservation,
) -> gateway_module._RuntimeObservationRead:
    """Build one detached, causally bound synthetic Adapter read."""

    # Paseo persists its wake snapshot and validates that snapshot against the
    # action's actual durable subject.  Keep event-race fixtures synthetic
    # without fabricating an unrelated subject identity into that snapshot.
    selected_record = adapter._actions[selected_stable_action_id]
    if isinstance(adapter, _PaseoRuntimeProviderAdapter) and type(selected_record) is dict:
        subject = gateway_module._subject_from_canonical(selected_record["subject"])
        object.__setattr__(observation, "subject_digest", subject.digest)
        object.__setattr__(
            observation,
            "authority_subtree_digest",
            subject.authority_digest,
        )

    identity = gateway_module._RuntimeObservationIdentity(
        stable_action_id=observation.stable_action_id,
        repository=observation.repository,
        campaign_key=observation.campaign_key,
        campaign_handle=observation.campaign_handle,
        plan_revision_digest=observation.plan_revision_digest,
        work_run_key=observation.work_run_key,
        subject_digest=observation.subject_digest,
        profile_digest=observation.profile_digest,
        workspace_id=observation.workspace_id,
        prompt_artifact_digest=observation.prompt_artifact_digest,
        authority_subtree_digest=observation.authority_subtree_digest,
        input_artifact_digests=(),
        spec_identity_digest="5" * 64,
        binding_ref=observation.binding_ref,
        agent_id=observation.agent_id,
        session_id=observation.session_id,
    )
    output_digest = (
        None
        if type(observation) is gateway_module._PreparedRuntimeObservation
        else observation.output_artifact_digest
    )
    evidence = gateway_module._RuntimeArtifactEvidence(
        prompt=gateway_module._RuntimeArtifactReadProof(
            observation.prompt_artifact_digest,
            0,
        ),
        inputs=(),
        output=(
            None
            if output_digest is None
            else gateway_module._RuntimeOutputArtifactProof(
                artifact_digest=output_digest,
                byte_length=0,
                schema_version="gwo.runtime.output.v1",
                subject_digest=observation.subject_digest,
                stable_action_id=observation.stable_action_id,
                authority_digest=observation.authority_subtree_digest,
            )
        ),
    )
    selected_record_digest = (
        gateway_module._runtime_in_memory_selected_record_digest(
            selected_record
        )
        if isinstance(adapter, _InMemoryRuntimeProviderAdapter)
        else digest_value(dict(selected_record))
    )
    return gateway_module._RuntimeObservationRead(
        selected_stable_action_id=selected_stable_action_id,
        identity=identity,
        result=observation,
        artifact_evidence=evidence,
        token=gateway_module._RuntimeObservationReadToken(
            stable_action_id=selected_stable_action_id,
            identity_digest=(
                gateway_module._runtime_observation_identity_digest(identity)
            ),
            selected_record_digest=selected_record_digest,
            observation_digest=digest_value(
                gateway_module._json_projection(asdict(observation))
            ),
            output_artifact_digest=output_digest,
        ),
    )


def _adapter_command(adapter, stable_action_id: str, command):
    """Issue a command through the exact observe-gated four-method seam."""

    observation = adapter.observe(stable_action_id)
    assert not isinstance(observation, _RuntimeFailure)
    return adapter.command(stable_action_id, command)


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


def _put_work_subject_artifacts(
    store: ArtifactStore,
    planning_subject: CampaignPlanningSubject,
    *,
    stable_action_id: str,
    work_run_key: str = "work-run:repair",
) -> WorkRunSubject:
    unsigned = WorkRunSubject(
        repository=planning_subject.repository,
        campaign_key=planning_subject.campaign_key,
        campaign_handle=planning_subject.campaign_handle,
        plan_revision_digest=store.put_canonical({"revision": 1}).digest,
        work_run_key=work_run_key,
        ticket_key="issue:111",
        purpose=gateway_module.WorkRunPurpose.implementation(),
        prompt_artifact_digest="0" * 64,
        authority_subtree_digest=planning_subject.policy_witness_digest,
        stable_action_id=stable_action_id,
    )
    prompt = store.put_canonical(
        {
            "schema_version": "gwo.runtime.prompt.v1",
            "subject_digest": unsigned.prompt_binding_digest,
            "authority_digest": unsigned.authority_digest,
            "payload": {"complete_contract": "repair #111"},
        }
    )
    return replace(unsigned, prompt_artifact_digest=prompt.digest)


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


class _SplitViewMapping(Mapping):
    """Expose stable identity items while changing lookup after composition."""

    def __init__(self, identity, split):
        self._identity = dict(identity)
        self._split = dict(split)
        self.split = False

    def __getitem__(self, key):
        values = self._split if self.split else self._identity
        if key in values:
            return values[key]
        return self._identity[key]

    def __iter__(self):
        return iter(self._identity)

    def __len__(self):
        return len(self._identity)

    def items(self):
        return self._identity.items()

    def get(self, key, default=None):
        values = self._split if self.split else self._identity
        return values.get(key, self._identity.get(key, default))


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
    assert "from .runtime import RuntimeProfile" not in source
    from gwo_v8.runtime_profile import RuntimeProfile as NeutralRuntimeProfile
    from gwo_v8.runtime import RuntimeProfile as PredecessorRuntimeProfile

    assert gateway_module.RuntimeProfile is NeutralRuntimeProfile
    assert PredecessorRuntimeProfile is NeutralRuntimeProfile


def test_preflight_is_campaign_planning_only_and_cas_binds_subject_options_and_config(tmp_path):
    gateway, store, _adapter = _gateway(tmp_path)
    subject = _put_subject_artifacts(store, _subject())

    first = gateway.planning_preflight(subject)
    retry = gateway.planning_preflight(subject)

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
        gateway.planning_preflight(changed)
    assert stopped.value.code == "RUNTIME_PREFLIGHT_IDENTITY_MISMATCH"

    work = WorkRunSubject(
        repository=subject.repository,
        campaign_key=subject.campaign_key,
        campaign_handle=subject.campaign_handle,
        plan_revision_digest=store.put_canonical({"revision": 1}).digest,
        work_run_key="work-run:repair",
        ticket_key="issue:111",
        purpose=gateway_module.WorkRunPurpose.implementation(),
        prompt_artifact_digest=subject.planning_request_artifact_digest,
        authority_subtree_digest=subject.policy_witness_digest,
        stable_action_id="work:repair",
    )
    with pytest.raises(RuntimeGatewayError) as stopped:
        gateway.planning_preflight(work)  # type: ignore[arg-type]
    assert stopped.value.code == "RUNTIME_PREFLIGHT_SUBJECT_INVALID"

    alternate = _profile()
    alternate = replace(alternate, name="alternate")
    with pytest.raises(RuntimeGatewayError) as stopped:
        RuntimeGateway(
            store_path=tmp_path / "gateway.journal",
            _adapter=_adapter,
            configuration=RuntimeConfiguration(
                profiles={alternate.digest: alternate},
                host_mappings={"coordinator": ProfileMapping(alternate.digest)},
            ),
            _artifacts=store,
        )
    assert stopped.value.code == "RUNTIME_STORE_INVALID"


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
    native_read = adapter._reconcile_observation

    def invalid(stable_action_id):
        read = native_read(stable_action_id)
        observed = read.result
        assert not isinstance(observed, _RuntimeFailure)
        return replace(read, result=mutate(observed))

    adapter._reconcile_observation = invalid  # type: ignore[method-assign]
    with pytest.raises(RuntimeGatewayError) as stopped:
        gateway.progress(subject, receipt)
    assert stopped.value.code == "RUNTIME_PROVIDER_PROTOCOL_INVALID"


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
    adapter.command = (  # type: ignore[method-assign]
        lambda stable_action_id, command, **_kwargs: _CommandReceipt(
            stable_action_id,
            command,
        )
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
            branch = args[args.index("--new-branch") + 1]
            registry_workspace = self.workspace.parent / slug
            if not registry_workspace.exists():
                os.symlink(
                    self.workspace,
                    registry_workspace,
                    target_is_directory=True,
                )
            name = (
                args[args.index("--title") + 1]
                if "--title" in args
                else branch
            )
            self.workspaces = [
                {
                    "workspaceId": "workspace:one",
                    "name": name,
                    "isolation": "worktree",
                    "project": "project:one",
                    "cwd": str(registry_workspace),
                }
            ]
            if self.lose_workspace_ack:
                self.lose_workspace_ack = False
                raise OSError("workspace acknowledgement vanished")
            return {
                "workspace": {
                    "id": "workspace:one",
                    "path": str(registry_workspace),
                }
            }
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


def _mutating_paseo_commands(commands):
    return [
        command
        for command in commands
        if command[:2] == ["workspace", "create"]
        or command[:2] in (["permit", "allow"], ["permit", "deny"])
        or command[:2] == ["agent", "update"]
        or command[0] in {"run", "send", "stop", "archive"}
    ]


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
    started = _adapter_command(
        adapter, subject.stable_action_id, RuntimeCommand.START
    )
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
    _adapter_command(adapter, subject.stable_action_id, RuntimeCommand.START)
    client.permissions = [_paseo_permission("request:one")]
    bound = adapter.observe(subject.stable_action_id)
    assert not isinstance(bound, _RuntimeFailure)
    assert [item.request_id for item in bound.permission_requests] == ["request:one"]
    response = _adapter_command(
        adapter,
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
    assert not isinstance(
        _adapter_command(adapter, subject.stable_action_id, RuntimeCommand.START),
        _RuntimeFailure,
    )
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
    target = workspace.parent / slug
    os.symlink(workspace, target, target_is_directory=True)
    client.workspaces = [
        {
            "workspaceId": f"workspace:{index}",
            "name": slug,
            "isolation": "worktree",
            "cwd": str(target),
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
    assert not isinstance(
        _adapter_command(first, subject.stable_action_id, RuntimeCommand.START),
        _RuntimeFailure,
    )
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
    assert not isinstance(
        _adapter_command(adapter, subject.stable_action_id, RuntimeCommand.START),
        _RuntimeFailure,
    )
    running_events = adapter.events(prepared_events.next_cursor)
    assert [event.kind for event in running_events.events] == ["state:running"]
    assert not isinstance(
        _adapter_command(adapter, subject.stable_action_id, RuntimeCommand.FENCE),
        _RuntimeFailure,
    )
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
    assert not isinstance(
        _adapter_command(adapter, subject.stable_action_id, RuntimeCommand.START),
        _RuntimeFailure,
    )

    result = _adapter_command(
        adapter, subject.stable_action_id, RuntimeCommand.FENCE
    )

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
    first = _adapter_command(adapter, action_id, RuntimeCommand.FENCE)

    assert isinstance(first, _RuntimeFailure)
    assert first.code == "RUNTIME_TRANSPORT_UNAVAILABLE"
    durable_pending = json.loads(adapter._state_path.read_text(encoding="utf-8"))
    assert durable_pending["actions"][action_id]["pending_fence"] is True
    assert isinstance(
        durable_pending["actions"][action_id]["pending_fence_claim_id"], str
    )
    assert durable_pending["actions"][action_id]["pending_fence_quiesced"] is True
    assert durable_pending["actions"][action_id]["fenced"] is False
    assert durable_pending["actions"][action_id]["wake_state_digest"] is None

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
    assert cleared["wake_state_digest"] is None

    retried = _adapter_command(recovered, action_id, RuntimeCommand.FENCE)
    assert isinstance(retried, _CommandReceipt)
    assert update_attempts == 2
    assert successful_updates == 1
    final = recovered.observe(action_id)
    assert not isinstance(final, _RuntimeFailure)
    assert final.fenced is True
    assert recovered._actions[action_id]["pending_fence"] is False
    assert recovered._actions[action_id]["pending_fence_claim_id"] is None
    assert recovered._actions[action_id]["pending_fence_quiesced"] is False
    assert recovered._actions[action_id]["wake_state_digest"] is None


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
            assert release_update.wait(30)
            raise TimeoutError("in-flight label update failed before effect")
        successful_updates += 1
        return native_update_labels(agent_id, labels)

    client.update_labels = block_first_update_before_effect  # type: ignore[method-assign]
    owner_results: list[object] = []
    worker = threading.Thread(
        target=lambda: owner_results.append(
            _adapter_command(owner, action_id, RuntimeCommand.FENCE)
        )
    )
    worker.start()
    assert update_entered.wait(30)
    try:
        while_inflight = contender.observe(action_id)
        duplicate = _adapter_command(
            contender, action_id, RuntimeCommand.FENCE
        )
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

    retried = _adapter_command(recovered, action_id, RuntimeCommand.FENCE)
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
        store,
        source,
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
    accepted = _adapter_command(adapter, action_id, RuntimeCommand.FENCE)
    assert isinstance(accepted, _CommandReceipt)

    absent = adapter.observe(action_id)
    assert not isinstance(absent, _RuntimeFailure)
    assert absent.fenced is False
    assert adapter._actions[action_id]["pending_fence"] is True
    assert isinstance(adapter._actions[action_id]["pending_fence_claim_id"], str)
    assert adapter._actions[action_id]["pending_fence_quiesced"] is False
    retry = _adapter_command(adapter, action_id, RuntimeCommand.FENCE)
    assert isinstance(retry, _RuntimeFailure)
    assert retry.code == "RUNTIME_MATERIALIZATION_PENDING"
    assert update_attempts == 1


def test_legacy_pending_fence_without_quiesced_proof_fails_closed(tmp_path):
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
    commands_before = deepcopy(client.commands)
    with pytest.raises(RuntimeGatewayError) as rejected:
        _PaseoRuntimeProviderAdapter(
            client=client,  # type: ignore[arg-type]
            artifacts=store,
            repository_contexts={
                "owner/repository": RuntimeRepositoryContext(source, "main")
            },
            state_path=adapter._state_path,
        )

    assert rejected.value.code == "RUNTIME_STORE_INVALID"
    assert client.commands == commands_before


@pytest.mark.parametrize(
    ("pending", "claim_id", "quiesced"),
    (
        (False, None, True),
        (True, None, True),
        (False, "residual-fence-claim", False),
    ),
)
def test_invalid_durable_fence_quiescence_evidence_fails_before_provider_readback(
    tmp_path, pending, claim_id, quiesced
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
    commands_before = deepcopy(client.commands)

    observed = adapter.observe(action_id)

    assert isinstance(observed, _RuntimeFailure)
    assert observed.code == "RUNTIME_STORE_INVALID"
    assert client.commands == commands_before


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
        assert release_owner.wait(30)
        raise TimeoutError("label effect applied but acknowledgement lost")

    client.update_labels = apply_then_block_before_ack_loss  # type: ignore[method-assign]
    owner_results: list[object] = []
    worker = threading.Thread(
        target=lambda: owner_results.append(
            _adapter_command(owner, action_id, RuntimeCommand.FENCE)
        )
    )
    worker.start()
    effect_observed = False
    try:
        # The owner performs a real read/CAS sequence before the injected
        # label hook.  Under a saturated full suite that sequence can exceed
        # the former five-second test window; do not mistake scheduling delay
        # for a pre-effect production failure.  The finally block also keeps
        # a failed assertion from leaking a blocked worker into later tests.
        effect_observed = effect_visible.wait(30)
        if effect_observed:
            converged = observer.observe(action_id)
    finally:
        release_owner.set()
        worker.join(30)

    assert effect_observed, (
        "fence owner did not reach the label effect within 30 seconds; "
        f"worker_alive={worker.is_alive()} owner_results={owner_results!r}"
    )
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

    result = _adapter_command(
        adapter, subject.stable_action_id, RuntimeCommand.START
    )

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
    assert not isinstance(
        _adapter_command(adapter, subject.stable_action_id, RuntimeCommand.START),
        _RuntimeFailure,
    )
    assert not isinstance(
        _adapter_command(adapter, subject.stable_action_id, RuntimeCommand.PARK),
        _RuntimeFailure,
    )
    assert adapter.observe(subject.stable_action_id).lifecycle == "parked"
    client.lose_resume_ack_while_idle = True

    result = _adapter_command(
        adapter, subject.stable_action_id, RuntimeCommand.RESUME
    )

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
    assert not isinstance(
        _adapter_command(adapter, subject.stable_action_id, RuntimeCommand.START),
        _RuntimeFailure,
    )
    assert not isinstance(
        _adapter_command(adapter, subject.stable_action_id, RuntimeCommand.RETIRE),
        _RuntimeFailure,
    )

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
    assert not isinstance(
        _adapter_command(adapter, subject.stable_action_id, RuntimeCommand.START),
        _RuntimeFailure,
    )
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
    _adapter_command(adapter, subject.stable_action_id, RuntimeCommand.START)
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
    assert isinstance(
        _adapter_command(adapter, subject.stable_action_id, RuntimeCommand.PARK),
        _RuntimeFailure,
    )
    initial_events = adapter.events(None)
    assert [event.kind for event in initial_events.events] == ["state:prepared"]
    assert adapter.events(initial_events.next_cursor).events == ()

    if client is not None:
        client.permissions = [
            _paseo_permission("permit01-one"),
            _paseo_permission("permit02-final"),
        ]
    assert not isinstance(
        _adapter_command(adapter, subject.stable_action_id, RuntimeCommand.START),
        _RuntimeFailure,
    )
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
        _adapter_command(
            adapter,
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
    assert not isinstance(
        _adapter_command(
            adapter, subject.stable_action_id, RuntimeCommand.INTERRUPT
        ),
        _RuntimeFailure,
    )
    assert adapter.observe(subject.stable_action_id).lifecycle == "parked"
    assert not isinstance(
        _adapter_command(adapter, subject.stable_action_id, RuntimeCommand.RESUME),
        _RuntimeFailure,
    )
    assert adapter.observe(subject.stable_action_id).lifecycle == "running"
    assert not isinstance(
        _adapter_command(adapter, subject.stable_action_id, RuntimeCommand.PARK),
        _RuntimeFailure,
    )
    assert not isinstance(
        _adapter_command(adapter, subject.stable_action_id, RuntimeCommand.RESUME),
        _RuntimeFailure,
    )
    assert not isinstance(
        _adapter_command(adapter, subject.stable_action_id, RuntimeCommand.FENCE),
        _RuntimeFailure,
    )
    assert adapter.observe(subject.stable_action_id).fenced is True

    assert not isinstance(
        _adapter_command(
            adapter,
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
        invalid = _adapter_command(
            adapter, subject.stable_action_id, invalid_command
        )
        assert isinstance(invalid, _RuntimeFailure)
        assert invalid.code == "RUNTIME_COMMAND_INVALID"
    if client is not None:
        assert len([args for args in client.commands if args[0] == "stop"]) == stop_count

    assert not isinstance(
        _adapter_command(adapter, subject.stable_action_id, RuntimeCommand.RETIRE),
        _RuntimeFailure,
    )
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

    verdict = gateway_module._ObservationProtocol.validate(
        adapter._reconcile_observation(subject.stable_action_id),
        selected_stable_action_id=subject.stable_action_id,
    )

    assert verdict.kind == "failure"
    assert type(verdict.failure) is _RuntimeFailure
    assert verdict.failure.code == "RUNTIME_ARTIFACT_DIGEST_MISMATCH"
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
    assert not isinstance(
        _adapter_command(adapter, subject.stable_action_id, RuntimeCommand.START),
        _RuntimeFailure,
    )
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
    assert not isinstance(
        _adapter_command(adapter, subject.stable_action_id, RuntimeCommand.START),
        _RuntimeFailure,
    )
    assert client.agent is not None
    client.agent.lifecycle = "idle"
    client.permissions = [_paseo_permission("request:idle", description="repo")]
    pending = adapter.observe(subject.stable_action_id)
    assert not isinstance(pending, _RuntimeFailure)
    assert pending.lifecycle == "running"
    assert not isinstance(
        _adapter_command(
            adapter,
            subject.stable_action_id,
            PermissionResponse(request_id="request:idle", decision="allow"),
        ),
        _RuntimeFailure,
    )
    recovered = adapter.observe(subject.stable_action_id)
    assert not isinstance(recovered, _RuntimeFailure)
    assert recovered.lifecycle == "running"
    record = adapter._actions[subject.stable_action_id]
    assert record["pending_permission_response"] is None
    assert isinstance(record["completed_permission_response"], dict)

    client.permissions = [_paseo_permission("request:closed", description="repo")]
    closed_read = adapter._reconcile_observation(subject.stable_action_id)
    closed_verdict = gateway_module._ObservationProtocol.validate(
        closed_read,
        selected_stable_action_id=subject.stable_action_id,
    )
    assert closed_verdict.kind == "bound"
    assert type(closed_verdict.token) is gateway_module._RuntimeObservationReadToken
    client.agent.lifecycle = "closed"
    result = adapter.observe(subject.stable_action_id)
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
    assert not isinstance(
        _adapter_command(adapter, subject.stable_action_id, RuntimeCommand.START),
        _RuntimeFailure,
    )
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
    assert adapter._actions[subject.stable_action_id]["pending_permission_response"] is None
    assert isinstance(
        adapter._actions[subject.stable_action_id]["completed_permission_response"],
        dict,
    )

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
    assert not isinstance(
        _adapter_command(adapter, subject.stable_action_id, RuntimeCommand.START),
        _RuntimeFailure,
    )
    return store, source, workspace, client, adapter, subject, spec


def _paseo_event_adapter(tmp_path, action_ids, *, state_name="paseo-actions.json"):
    """Seed complete V5 records for event tests that replace provider reads.

    The synthetic event-read tests need durable action identities for CAS and
    cursor state, but deliberately replace private reconciliation before any
    staged Artifact or provider read occurs.  Derive every row from one real
    Prepared record so journal recovery still exercises the production closed
    schema instead of relying on partial test-only dictionaries.
    """
    store = ArtifactStore(tmp_path / "artifacts")
    source, workspace = _repository_worktree(tmp_path)
    client = _RecordingPaseoCli(workspace)
    adapter = _PaseoRuntimeProviderAdapter(
        client=client,  # type: ignore[arg-type]
        artifacts=store,
        repository_contexts={
            "owner/repository": RuntimeRepositoryContext(source, "main")
        },
        state_path=tmp_path / state_name,
    )
    subject = _put_subject_artifacts(store, _subject())
    prompt = store.get(subject.planning_request_artifact_digest)
    spec = _RuntimeActionSpec(
        subject.stable_action_id,
        subject,
        _profile(),
        prompt,
        (prompt,),
    )
    assert not isinstance(adapter.prepare(spec), _RuntimeFailure)
    template = deepcopy(adapter._actions[subject.stable_action_id])

    def action_record(action_id):
        action_subject = replace(subject, stable_action_id=action_id)
        return {
            **deepcopy(template),
            "subject": action_subject.canonical(),
            "subject_digest": action_subject.digest,
            # Event tests install a synthetic Bound read before any provider
            # operation; seed the matching durable binding so a terminal
            # event may legally persist its terminal marker.
            "bound_agent_id": f"agent:event:{action_id}",
            "binding_established": True,
            "pending_start": False,
        }

    adapter._transact(
        lambda state: state["actions"].update(
            {action_id: action_record(action_id) for action_id in action_ids}
        )
    )
    adapter._transact(
        lambda state: state["actions"].pop(subject.stable_action_id, None)
    )
    return store, source, workspace, client, adapter


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
        _adapter_command(
            adapter,
            subject.stable_action_id,
            PermissionResponse(full_id, "allow"),
        ),
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

    adapter.command = lambda *_args, **_kwargs: _RuntimeFailure("RUNTIME_COMMAND_INVALID", "permanent")  # type: ignore[method-assign]
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
        purpose=gateway_module.WorkRunPurpose.implementation(),
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
    start_failed = _adapter_command(
        adapter, subject.stable_action_id, RuntimeCommand.START
    )
    assert isinstance(start_failed, _RuntimeFailure)
    assert adapter._actions[subject.stable_action_id] == record_before_start
    assert all(command[0] != "run" for command in client.commands[len(before_start):])

    # Recreate an unfenced Bound parked action, then fail the resume intent save.
    adapter._save = _PaseoRuntimeProviderAdapter._save.__get__(adapter)  # type: ignore[method-assign]
    assert not isinstance(
        _adapter_command(adapter, subject.stable_action_id, RuntimeCommand.START),
        _RuntimeFailure,
    )
    assert not isinstance(
        _adapter_command(adapter, subject.stable_action_id, RuntimeCommand.PARK),
        _RuntimeFailure,
    )
    assert adapter.observe(subject.stable_action_id).lifecycle == "parked"
    before_resume = list(client.commands)
    record_before_resume = deepcopy(adapter._actions[subject.stable_action_id])
    adapter._save = lambda: (_ for _ in ()).throw(OSError("resume save failed"))  # type: ignore[method-assign]
    resume_failed = _adapter_command(
        adapter, subject.stable_action_id, RuntimeCommand.RESUME
    )
    assert isinstance(resume_failed, _RuntimeFailure)
    assert adapter._actions[subject.stable_action_id] == record_before_resume
    assert all(command[0] != "send" for command in client.commands[len(before_resume):])


def test_permission_response_requires_same_decision_receipt_before_removal(tmp_path):
    store, source, _workspace, client, adapter, subject, _spec = _prepared_paseo_adapter(tmp_path)
    request_id = "receipt1-full-provider-request"
    client.permissions = [_paseo_permission(request_id)]
    accepted = _adapter_command(
        adapter,
        subject.stable_action_id,
        PermissionResponse(request_id, "allow"),
    )
    assert not isinstance(accepted, _RuntimeFailure)
    pending = adapter._actions[subject.stable_action_id]["pending_permission_response"]
    assert pending["provider_receipt"] == {
        "requestId": request_id[:8],
        "agentId": "agent:one",
        "agentShortId": "agent:o",
        "name": pending["request"]["operation_id"],
        "result": "allowed",
    }
    observed = adapter.observe(subject.stable_action_id)
    assert not isinstance(observed, _RuntimeFailure)
    record = adapter._actions[subject.stable_action_id]
    assert record["pending_permission_response"] is None
    assert isinstance(record["completed_permission_response"], dict)

    restarted = _PaseoRuntimeProviderAdapter(
        client=client,  # type: ignore[arg-type]
        artifacts=store,
        repository_contexts={
            "owner/repository": RuntimeRepositoryContext(source, "main")
        },
        state_path=adapter._state_path,
    )
    recovered = restarted.observe(subject.stable_action_id)
    assert not isinstance(recovered, _RuntimeFailure)
    assert recovered.completed_permission_response is not None


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
    result = _adapter_command(
        adapter,
        subject.stable_action_id,
        PermissionResponse(request_id, "allow"),
    )
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
    command = _adapter_command(
        adapter,
        subject.stable_action_id,
        PermissionResponse(request_id, "allow"),
    )
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
    private_invalid = _adapter_command(
        adapter, subject.stable_action_id, subclass
    )
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
    assert not isinstance(
        _adapter_command(adapter, subject.stable_action_id, command),
        _RuntimeFailure,
    )
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
    assert not isinstance(
        _adapter_command(adapter, subject.stable_action_id, RuntimeCommand.PARK),
        _RuntimeFailure,
    )
    assert adapter.observe(subject.stable_action_id).lifecycle == "parked"
    record_before = deepcopy(adapter._actions[subject.stable_action_id])
    state_before = json.loads(adapter._state_path.read_text(encoding="utf-8"))
    command_count = len(client.commands)

    def fail_write(_record):
        raise OSError("local resume prompt write failed")

    monkeypatch.setattr(adapter, "_write_resume_file", fail_write)
    failed = _adapter_command(
        adapter, subject.stable_action_id, RuntimeCommand.RESUME
    )
    assert isinstance(failed, _RuntimeFailure)
    assert failed.code == "RUNTIME_TRANSPORT_UNAVAILABLE"
    assert adapter._actions[subject.stable_action_id] == record_before
    assert json.loads(adapter._state_path.read_text(encoding="utf-8")) == state_before
    assert all(args[0] != "send" for args in client.commands[command_count:])

    monkeypatch.setattr(
        adapter,
        "_write_resume_file",
        _PaseoRuntimeProviderAdapter._write_resume_file.__get__(
            adapter, _PaseoRuntimeProviderAdapter
        ),
    )
    assert not isinstance(
        _adapter_command(adapter, subject.stable_action_id, RuntimeCommand.RESUME),
        _RuntimeFailure,
    )
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
    subject = _put_subject_artifacts(store, _subject())
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

    invalid = _adapter_command(
        adapter,
        subject.stable_action_id,
        _PermissionResponseSubclass("request:one", "allow"),
    )

    assert isinstance(invalid, _RuntimeFailure)
    assert invalid.code == "RUNTIME_COMMAND_INVALID"


def test_postdispatch_start_native_error_retains_pending_and_never_reissues(tmp_path):
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
            client.commands.append(list(args))
            raise RuntimeGatewayError(
                "RUNTIME_PROVIDER_PROTOCOL_INVALID",
                "post-dispatch provider output overflow",
            )
        return native_run(args)

    client._run = reject_run  # type: ignore[method-assign]
    rejected = _adapter_command(
        adapter, subject.stable_action_id, RuntimeCommand.START
    )
    assert isinstance(rejected, _RuntimeFailure)
    assert rejected.code == "RUNTIME_PROVIDER_PROTOCOL_INVALID"
    assert adapter._actions[subject.stable_action_id] != record_before
    assert adapter._actions[subject.stable_action_id]["pending_start"] is True
    assert len([args for args in client.commands if args[0] == "run"]) == 1

    client._run = native_run  # type: ignore[method-assign]
    retry_verdict = gateway_module._ObservationProtocol.validate(
        adapter._reconcile_observation(subject.stable_action_id),
        selected_stable_action_id=subject.stable_action_id,
    )
    assert retry_verdict.kind == "fairness_advance"
    assert type(retry_verdict.token) is gateway_module._RuntimeObservationReadToken
    retried = adapter.observe(subject.stable_action_id)
    assert isinstance(retried, _RuntimeFailure)
    assert retried.code == "RUNTIME_MATERIALIZATION_PENDING"
    assert len([args for args in client.commands if args[0] == "run"]) == 1


def test_postdispatch_permission_native_error_retains_claim_and_never_reissues(tmp_path):
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
                "RUNTIME_PROVIDER_PROTOCOL_INVALID",
                "post-dispatch permission output was invalid",
            )
        return native_run(args)

    client._run = reject_permit  # type: ignore[method-assign]
    rejected = _adapter_command(
        adapter,
        subject.stable_action_id, PermissionResponse(request_id, "allow")
    )

    assert isinstance(rejected, _RuntimeFailure)
    assert rejected.code == "RUNTIME_PROVIDER_PROTOCOL_INVALID"
    assert adapter._actions[subject.stable_action_id] != record_before
    assert json.loads(adapter._state_path.read_text(encoding="utf-8")) != disk_before
    assert isinstance(
        adapter._actions[subject.stable_action_id][
            "pending_permission_response"
        ],
        dict,
    )
    assert permit_calls == [["permit", "allow", "agent:one", request_id, "--json"]]

    client._run = native_run  # type: ignore[method-assign]
    retried = _adapter_command(
        adapter,
        subject.stable_action_id,
        PermissionResponse(request_id, "allow"),
    )

    assert isinstance(retried, _RuntimeFailure)
    assert retried.code == "RUNTIME_EFFECT_AMBIGUOUS"
    assert len(permit_calls) == 1


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
    rejected = _adapter_command(
        adapter,
        subject.stable_action_id, PermissionResponse(request_id, "allow")
    )

    assert isinstance(rejected, _RuntimeFailure)
    assert rejected.code == "RUNTIME_PROVIDER_PROTOCOL_INVALID"
    record_after = adapter._actions[subject.stable_action_id]
    assert record_after != record_before
    assert record_after["pending_permission_response"] == {
        "request_id": request_id,
        "decision": "allow",
        "request": asdict(request),
        "request_digest": digest_value(asdict(request)),
        "provider_receipt": None,
    }
    observed = adapter.observe(subject.stable_action_id)
    assert isinstance(observed, _RuntimeFailure)
    assert observed.code == "RUNTIME_EFFECT_AMBIGUOUS"


def test_postdispatch_workspace_create_native_error_retains_intent_and_never_reissues(tmp_path):
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
                "RUNTIME_PROVIDER_PROTOCOL_INVALID",
                "post-dispatch create output overflowed",
            )
        return native_run(args)

    client._run = reject_create  # type: ignore[method-assign]
    rejected = adapter.prepare(spec)

    assert isinstance(rejected, _RuntimeFailure)
    assert rejected.code == "RUNTIME_PROVIDER_PROTOCOL_INVALID"
    assert adapter._workspace_intents[subject.stable_action_id]["phase"] == "create_pending"
    durable_after_reject = json.loads(adapter._state_path.read_text(encoding="utf-8"))
    assert durable_after_reject["workspace_intents"][subject.stable_action_id][
        "phase"
    ] == "create_pending"
    assert adapter._actions == {}
    assert len(create_calls) == 1

    client._run = native_run  # type: ignore[method-assign]
    retried = adapter.prepare(spec)

    assert isinstance(retried, _RuntimeFailure)
    assert retried.code == "RUNTIME_MATERIALIZATION_PENDING"
    assert len(create_calls) == 1


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
        "branch",
        "spec_identity_digest",
        "ownership_nonce",
        "layout_version",
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

    assert rejected.value.code == "RUNTIME_STORE_INVALID"
    durable = json.loads(journal.read_text(encoding="utf-8"))
    assert first._data == durable
    assert second._data != durable
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
    native_read = adapter._reconcile_observation
    observations = 0

    def malformed_second_read(stable_action_id):
        nonlocal observations
        observations += 1
        read = native_read(stable_action_id)
        if observations == 1:
            return read
        return replace(
            read,
            result=_RuntimeFailure(
                "RUNTIME_ACTION_ABSENT",
                "different detail",
                stable_action_id=stable_action_id,
                authoritative_absence=True,
            ),
        )

    adapter._reconcile_observation = malformed_second_read  # type: ignore[method-assign]
    with pytest.raises(RuntimeGatewayError) as stopped:
        gateway.progress(subject, preflight)

    assert stopped.value.code == "RUNTIME_PROVIDER_PROTOCOL_INVALID"
    assert adapter.prepare_calls == [subject.stable_action_id]
    adapter._reconcile_observation = native_read  # type: ignore[method-assign]


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
    assert re.fullmatch(r"[0-9a-f]{32}", intent["ownership_nonce"])
    assert intent["layout_version"] == "1"


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
    stale_cursor = None
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
    initial_page = adapter.events(None)
    assert not isinstance(initial_page, _RuntimeFailure)
    assert initial_page.next_cursor is not None
    live_before = (
        deepcopy(adapter._actions),
        deepcopy(adapter._events),
        adapter._next_event_cursor,
    )
    disk_before = json.loads(adapter._state_path.read_text(encoding="utf-8"))
    client.permissions = [_paseo_permission("feed0001-bounded")]
    adapter._save = lambda: (_ for _ in ()).throw(OSError("event save failed"))  # type: ignore[method-assign]

    failed = adapter.events(initial_page.next_cursor)

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
            _adapter_command(
                adapter, subject.stable_action_id, RuntimeCommand.PARK
            ),
            _RuntimeFailure,
        )
    elif reconciliation == "fence":
        assert not isinstance(
            _adapter_command(
                adapter, subject.stable_action_id, RuntimeCommand.FENCE
            ),
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
    first = _adapter_command(adapter, subject.stable_action_id, command)

    assert isinstance(first, _RuntimeFailure)
    assert first.code == "RUNTIME_TRANSPORT_UNAVAILABLE"
    assert effect_calls == 1
    restarted = _PaseoRuntimeProviderAdapter(
        client=client,  # type: ignore[arg-type]
        artifacts=store,
        repository_contexts={"owner/repository": RuntimeRepositoryContext(source, "main")},
        state_path=adapter._state_path,
    )
    retried = _adapter_command(
        restarted, subject.stable_action_id, command
    )

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


@pytest.mark.parametrize("provider_lifecycle", ("idle", "running", "busy"))
def test_valid_output_dominates_every_nonretired_provider_lifecycle_and_clears_flags(
    tmp_path, provider_lifecycle
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
    bound = adapter.observe(action_id)
    assert not isinstance(bound, _RuntimeFailure)
    assert adapter._actions[action_id]["binding_established"] is True
    record = adapter._actions[action_id]
    Path(record["result_file"]).parent.mkdir(parents=True, exist_ok=True)
    Path(record["result_file"]).write_bytes(
        gateway_module.canonical_bytes(
            {
                "schema_version": "gwo.runtime.output.v1",
                "subject_digest": subject.digest,
                "stable_action_id": action_id,
                "authority_digest": subject.authority_digest,
                "payload": {"completed": True},
            }
        )
    )
    adapter._persist_record_update(
        record,
        lambda updated: updated.update(
            {
                "pending_park": True,
                "parked": False,
                "pending_resume": False,
                "pending_stop_command": "park",
            }
        ),
    )
    adapter = _PaseoRuntimeProviderAdapter(
        client=client,  # type: ignore[arg-type]
        artifacts=store,
        repository_contexts={
            "owner/repository": RuntimeRepositoryContext(source, "main")
        },
        state_path=adapter._state_path,
    )
    client.agent.lifecycle = provider_lifecycle
    sends_before = sum(args[0] == "send" for args in client.commands)

    observed = adapter.observe(action_id)

    assert not isinstance(observed, _RuntimeFailure)
    assert observed.lifecycle == "completed"
    durable = adapter._actions[action_id]
    assert durable["pending_park"] is False
    assert durable["parked"] is False
    assert durable["pending_resume"] is False
    assert durable["pending_stop_command"] is None
    assert sum(args[0] == "send" for args in client.commands) == sends_before


@pytest.mark.parametrize(
    "tamper",
    (
        None,
        "request_digest",
        "provider_receipt_digest",
        "stable_action_id",
        "subject_digest",
        "binding_ref",
        "outstanding_request",
    ),
)
def test_in_memory_terminal_permission_replay_requires_complete_bound_evidence(
    tmp_path, tamper
):
    store = ArtifactStore(tmp_path / "artifacts")
    subject = _put_subject_artifacts(store, _subject())
    prompt = store.get(subject.planning_request_artifact_digest)
    request_id = "request:terminal-replay"
    adapter = _InMemoryRuntimeProviderAdapter(
        store,
        pending_permissions={
            subject.stable_action_id: ((request_id, "write", "repo"),)
        },
    )
    spec = _RuntimeActionSpec(
        subject.stable_action_id, subject, _profile(), prompt, (prompt,)
    )
    assert not isinstance(adapter.prepare(spec), _RuntimeFailure)
    assert not isinstance(
        _adapter_command(
            adapter, subject.stable_action_id, RuntimeCommand.START
        ),
        _RuntimeFailure,
    )
    response = PermissionResponse(request_id, "allow")
    assert not isinstance(
        _adapter_command(adapter, subject.stable_action_id, response),
        _RuntimeFailure,
    )
    action = adapter._actions[subject.stable_action_id]
    evidence = action.completed_permission_response
    assert evidence is not None
    assert action.lifecycle == "completed"
    if tamper == "request_digest":
        action.completed_permission_response = replace(
            evidence, request_digest="0" * 64
        )
    elif tamper == "provider_receipt_digest":
        action.completed_permission_response = replace(
            evidence, provider_receipt_digest="0" * 64
        )
    elif tamper == "stable_action_id":
        action.completed_permission_response = replace(
            evidence, stable_action_id="planning:other"
        )
    elif tamper == "subject_digest":
        action.completed_permission_response = replace(
            evidence, subject_digest="0" * 64
        )
    elif tamper == "binding_ref":
        action.completed_permission_response = replace(
            evidence, binding_ref="binding:other"
        )
    elif tamper == "outstanding_request":
        action.pending_permissions.append((request_id, "write", "repo"))

    if tamper is None:
        assert isinstance(
            _adapter_command(adapter, subject.stable_action_id, response),
            _CommandReceipt,
        )
    elif tamper == "outstanding_request":
        verdict = gateway_module._ObservationProtocol.validate(
            adapter._reconcile_observation(subject.stable_action_id),
            selected_stable_action_id=subject.stable_action_id,
        )
        assert verdict.kind == "bound"
        assert type(verdict.token) is gateway_module._RuntimeObservationReadToken
        replay = _adapter_command(
            adapter,
            subject.stable_action_id,
            response,
        )
        assert isinstance(replay, _RuntimeFailure)
        assert replay.code == "RUNTIME_PERMISSION_REQUEST_UNKNOWN"
    else:
        actions_before = deepcopy(adapter._actions)
        commands_before = list(adapter.command_calls)
        verdict = gateway_module._ObservationProtocol.validate(
            adapter._reconcile_observation(subject.stable_action_id),
            selected_stable_action_id=subject.stable_action_id,
        )
        assert verdict.kind == "failure"
        assert type(verdict.token) is gateway_module._RuntimeObservationReadToken
        replay = adapter.observe(subject.stable_action_id)
        assert isinstance(replay, _RuntimeFailure)
        assert replay.code == "RUNTIME_OBSERVATION_INVALID"
        assert adapter._actions == actions_before
        assert adapter.command_calls == commands_before


@pytest.mark.parametrize("terminal_lifecycle", ("completed", "retired"))
def test_in_memory_terminal_permission_is_rejected_without_lifecycle_regression(
    tmp_path, terminal_lifecycle
):
    store = ArtifactStore(tmp_path / "artifacts")
    subject = _put_subject_artifacts(store, _subject())
    prompt = store.get(subject.planning_request_artifact_digest)
    adapter = _InMemoryRuntimeProviderAdapter(
        store,
        pending_permissions={
            subject.stable_action_id: (("request:terminal", "write", "repo"),)
        },
    )
    spec = _RuntimeActionSpec(
        subject.stable_action_id, subject, _profile(), prompt, (prompt,)
    )
    assert not isinstance(adapter.prepare(spec), _RuntimeFailure)
    assert not isinstance(
        _adapter_command(
            adapter, subject.stable_action_id, RuntimeCommand.START
        ),
        _RuntimeFailure,
    )
    action = adapter._actions[subject.stable_action_id]
    if terminal_lifecycle == "completed":
        adapter._complete_action(action)
    else:
        action.lifecycle = terminal_lifecycle
    pending_before = list(action.pending_permissions)

    rejected = _adapter_command(
        adapter,
        subject.stable_action_id,
        PermissionResponse("request:terminal", "allow"),
    )

    assert isinstance(rejected, _RuntimeFailure)
    assert rejected.code == "RUNTIME_PERMISSION_REQUEST_UNKNOWN"
    assert action.lifecycle == terminal_lifecycle
    assert action.pending_permissions == pending_before
    assert action.completed_permission_response is None


@pytest.mark.parametrize("terminal_lifecycle", ("completed", "retired"))
def test_production_terminal_permission_is_rejected_before_provider_call(
    tmp_path, terminal_lifecycle
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
    request_id = "request:terminal-provider"
    client.permissions = [_paseo_permission(request_id, description="repo")]
    if terminal_lifecycle == "retired":
        client.agent.archived = True
        client.agent.lifecycle = "idle"
    else:
        record = adapter._actions[action_id]
        Path(record["result_file"]).parent.mkdir(parents=True, exist_ok=True)
        Path(record["result_file"]).write_bytes(
            gateway_module.canonical_bytes(
                {
                    "schema_version": "gwo.runtime.output.v1",
                    "subject_digest": subject.digest,
                    "stable_action_id": action_id,
                    "authority_digest": subject.authority_digest,
                    "payload": {"completed": True},
                }
            )
        )
        client.agent.lifecycle = "idle"
    permits_before = sum(
        args[:2] in (["permit", "allow"], ["permit", "deny"])
        for args in client.commands
    )

    rejected = _adapter_command(
        adapter,
        action_id, PermissionResponse(request_id, "allow")
    )

    assert isinstance(rejected, _RuntimeFailure)
    assert rejected.code == "RUNTIME_PERMISSION_REQUEST_UNKNOWN"
    assert (
        sum(
            args[:2] in (["permit", "allow"], ["permit", "deny"])
            for args in client.commands
        )
        == permits_before
    )
    observed = adapter.observe(action_id)
    assert not isinstance(observed, _RuntimeFailure)
    assert observed.lifecycle == terminal_lifecycle


def test_gateway_rejects_new_terminal_permission_but_allows_exact_replay(tmp_path):
    store = ArtifactStore(tmp_path / "artifacts", maximum_bytes=300_000)
    subject = _put_subject_artifacts(store, _subject())
    profile = _profile()
    adapter = _InMemoryRuntimeProviderAdapter(
        store,
        pending_permissions={
            subject.stable_action_id: (
                ("request:proved", "write", "repo"),
                ("request:new", "write", "repo"),
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
    proved = PermissionResponse("request:proved", "allow")
    gateway.transition(subject.stable_action_id, proved)
    adapter._complete_action(adapter._actions[subject.stable_action_id])
    calls_before = len(adapter.command_calls)

    replay = gateway.transition(subject.stable_action_id, proved)
    assert replay.status == "completed"
    assert len(adapter.command_calls) == calls_before
    with pytest.raises(RuntimeGatewayError) as stopped:
        gateway.transition(
            subject.stable_action_id,
            PermissionResponse("request:new", "allow"),
        )
    assert stopped.value.code == "RUNTIME_PERMISSION_REQUEST_UNKNOWN"
    assert len(adapter.command_calls) == calls_before
    assert adapter._actions[subject.stable_action_id].lifecycle == "completed"


@pytest.mark.parametrize("adapter_kind", ("production", "memory"))
def test_events_use_one_fair_isolated_readback_and_skip_emitted_terminal_actions(
    tmp_path, adapter_kind
):
    action_ids = ("a:stale", "b:terminal", "c:live", "d:live", "e:live")
    if adapter_kind == "production":
        store, source, workspace, client, adapter = _paseo_event_adapter(
            tmp_path, action_ids
        )
    else:
        adapter = _InMemoryRuntimeProviderAdapter(
            ArtifactStore(tmp_path / "artifacts")
        )
        adapter._actions = {
            action_id: SimpleNamespace(
                wake_state_digest=None,
                wake_terminal_emitted=False,
            )
            for action_id in action_ids
        }

    calls: list[str] = []

    def bounded_read(action_id):
        calls.append(action_id)
        if action_id == "a:stale":
            read = _event_observation_read(
                adapter,
                action_id,
                _event_bound_observation(action_id),
            )
            assert read.identity is not None
            assert read.token is not None
            return gateway_module._runtime_sealed_failure_read(
                action_id,
                _RuntimeFailure(
                    "RUNTIME_TRANSPORT_UNAVAILABLE",
                    "one stale action failed",
                ),
                identity=read.identity,
                selected_record_digest=read.token.selected_record_digest,
            )
        return _event_observation_read(
            adapter,
            action_id,
            _event_bound_observation(
                action_id,
                lifecycle=(
                        "retired" if action_id == "b:terminal" else "running"
                ),
                fenced=False,
            ),
        )

    adapter._reconcile_observation = bounded_read  # type: ignore[method-assign]
    for _ in range(7):
        before = len(calls)
        page = adapter.events(None)
        assert not isinstance(page, _RuntimeFailure)
        assert len(calls) - before <= 1

    if adapter_kind == "production":
        restarted = _PaseoRuntimeProviderAdapter(
            client=client,  # type: ignore[arg-type]
            artifacts=store,
            repository_contexts={
                "owner/repository": RuntimeRepositoryContext(source, "main")
            },
            state_path=adapter._state_path,
        )
        restarted._reconcile_observation = bounded_read  # type: ignore[method-assign]
        adapter = restarted
    for _ in range(7):
        before = len(calls)
        page = adapter.events(None)
        assert not isinstance(page, _RuntimeFailure)
        assert len(calls) - before <= 1

    assert set(action_ids).issubset(calls)
    assert calls.count("b:terminal") == 1


def test_production_event_scan_cursor_survives_failure_and_restart_mid_cycle(
    tmp_path,
):
    store, source, workspace, client, adapter = _paseo_event_adapter(
        tmp_path,
        ("a:failed", "b:next", "c:last"),
    )
    state_path = adapter._state_path
    def failed_read(action_id):
        read = _event_observation_read(
            adapter,
            action_id,
            _event_bound_observation(action_id),
        )
        assert read.identity is not None
        assert read.token is not None
        return gateway_module._runtime_sealed_failure_read(
            action_id,
            _RuntimeFailure(
                "RUNTIME_TRANSPORT_UNAVAILABLE",
                "isolated wake readback failed",
            ),
            identity=read.identity,
            selected_record_digest=read.token.selected_record_digest,
        )

    adapter._reconcile_observation = failed_read  # type: ignore[method-assign]

    first = adapter.events(None)

    assert not isinstance(first, _RuntimeFailure)
    durable = json.loads(state_path.read_text(encoding="utf-8"))
    assert durable["event_scan_cursor"] == 1
    assert durable["next_event_cursor"] == 1
    assert durable["events"] == []

    restarted = _PaseoRuntimeProviderAdapter(
        client=client,  # type: ignore[arg-type]
        artifacts=store,
        repository_contexts={
            "owner/repository": RuntimeRepositoryContext(source, "main")
        },
        state_path=state_path,
    )
    calls: list[str] = []

    def read_next(action_id):
        calls.append(action_id)
        return _event_observation_read(
            restarted,
            action_id,
            _event_bound_observation(action_id, lifecycle="retired"),
        )

    restarted._reconcile_observation = read_next  # type: ignore[method-assign]
    terminal = restarted.events(None)

    assert calls == ["b:next"]
    assert not isinstance(terminal, _RuntimeFailure)
    assert [event.stable_action_id for event in terminal.events] == ["b:next"]
    cursor = terminal.next_cursor
    assert cursor is not None
    after_terminal = restarted.events(cursor)
    assert not isinstance(after_terminal, _RuntimeFailure)
    assert [
        event.stable_action_id for event in after_terminal.events
    ] == ["a:failed"]
    assert calls == ["b:next", "a:failed"]
    final_page = restarted.events(after_terminal.next_cursor)
    assert not isinstance(final_page, _RuntimeFailure)
    assert [
        event.stable_action_id for event in final_page.events
    ] == ["c:last"]
    assert calls == ["b:next", "a:failed", "c:last"]
    stored = json.loads(state_path.read_text(encoding="utf-8"))
    assert [
        event["stable_action_id"]
        for event in stored["events"]
        if event["stable_action_id"] == "b:next"
    ] == ["b:next"]
    assert stored["actions"]["b:next"]["wake_terminal_emitted"] is True


@pytest.mark.parametrize("adapter_kind", ("production", "memory"))
def test_terminal_wakes_rearm_for_fence_then_retire_state_changes(
    tmp_path, adapter_kind
):
    if adapter_kind == "production":
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
        record = adapter._actions[action_id]
        Path(record["result_file"]).write_bytes(
            gateway_module.canonical_bytes(
                {
                    "schema_version": "gwo.runtime.output.v1",
                    "subject_digest": subject.digest,
                    "stable_action_id": action_id,
                    "authority_digest": subject.authority_digest,
                    "payload": {"completed": True},
                }
            )
        )
        client.agent.lifecycle = "idle"
        completed = adapter.observe(action_id)
        assert not isinstance(completed, _RuntimeFailure)
        assert completed.lifecycle == "completed"
    else:
        store = ArtifactStore(tmp_path / "artifacts")
        subject = _put_subject_artifacts(store, _subject())
        prompt = store.get(subject.planning_request_artifact_digest)
        adapter = _InMemoryRuntimeProviderAdapter(store)
        action_id = subject.stable_action_id
        spec = _RuntimeActionSpec(
            action_id, subject, _profile(), prompt, (prompt,)
        )
        assert not isinstance(adapter.prepare(spec), _RuntimeFailure)
        assert not isinstance(
            _adapter_command(adapter, action_id, RuntimeCommand.START),
            _RuntimeFailure,
        )

    completed_page = adapter.events(None)
    assert not isinstance(completed_page, _RuntimeFailure)
    assert [event.kind for event in completed_page.events] == [
        "state:completed"
    ]
    terminal_marker = (
        adapter._actions[action_id].wake_terminal_emitted
        if adapter_kind == "memory"
        else adapter._actions[action_id]["wake_terminal_emitted"]
    )
    assert terminal_marker is True

    assert not isinstance(
        _adapter_command(adapter, action_id, RuntimeCommand.FENCE),
        _RuntimeFailure,
    )
    fenced_page = adapter.events(completed_page.next_cursor)
    assert not isinstance(fenced_page, _RuntimeFailure)
    assert [event.kind for event in fenced_page.events] == [
        "state:completed"
    ]

    assert not isinstance(
        _adapter_command(adapter, action_id, RuntimeCommand.RETIRE),
        _RuntimeFailure,
    )
    retired_page = adapter.events(fenced_page.next_cursor)
    assert not isinstance(retired_page, _RuntimeFailure)
    assert [event.kind for event in retired_page.events] == ["state:retired"]


@pytest.mark.parametrize("command", (RuntimeCommand.FENCE, RuntimeCommand.RETIRE))
def test_not_dispatched_terminal_transition_restores_wake_terminal_marker(
    tmp_path, command
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
    record = adapter._actions[action_id]
    Path(record["result_file"]).write_bytes(
        gateway_module.canonical_bytes(
            {
                "schema_version": "gwo.runtime.output.v1",
                "subject_digest": subject.digest,
                "stable_action_id": action_id,
                "authority_digest": subject.authority_digest,
                "payload": {"completed": True},
            }
        )
    )
    client.agent.lifecycle = "idle"
    assert not isinstance(adapter.events(None), _RuntimeFailure)
    before = deepcopy(adapter._actions[action_id])
    assert before["wake_terminal_emitted"] is True
    native_run = client._run
    native_update = client.update_labels

    if command is RuntimeCommand.FENCE:
        def fail_update(_agent_id, _labels):
            assert adapter._actions[action_id][
                "wake_terminal_emitted"
            ] is False
            raise gateway_module._ProviderNotDispatched(
                OSError("label process was not created")
            )

        client.update_labels = fail_update  # type: ignore[method-assign]
    else:
        def fail_archive(args):
            if args[0] == "archive":
                assert adapter._actions[action_id][
                    "wake_terminal_emitted"
                ] is False
                raise gateway_module._ProviderNotDispatched(
                    OSError("archive process was not created")
                )
            return native_run(args)

        client._run = fail_archive  # type: ignore[method-assign]

    failed = _adapter_command(adapter, action_id, command)

    assert isinstance(failed, _RuntimeFailure)
    assert failed.code == "RUNTIME_TRANSPORT_UNAVAILABLE"
    assert adapter._actions[action_id] == before
    client._run = native_run  # type: ignore[method-assign]
    client.update_labels = native_update  # type: ignore[method-assign]


def test_planning_preflight_has_subject_only_semantic_signature_and_hides_host_types():
    assert list(inspect.signature(RuntimeGateway.planning_preflight).parameters) == [
        "self",
        "subject",
    ]
    assert not hasattr(gwo_v8, "RuntimeSelector")
    assert not hasattr(gwo_v8, "ProfileMapping")
    assert not hasattr(gwo_v8, "CampaignStartRuntimeOverrides")


def test_campaign_runtime_assertion_is_tristate_restart_safe_and_exact_cas(tmp_path):
    store = ArtifactStore(tmp_path / "artifacts", maximum_bytes=300_000)
    subject = _put_subject_artifacts(store, _subject())
    base = _profile()
    alternate = replace(base, name="alternate", model="alternate-model")
    changed = replace(base, name="changed", model="changed-model")
    key = (
        subject.repository,
        subject.campaign_key,
        subject.campaign_handle,
    )
    assertion = CampaignStartRuntimeOverrides(
        coordinator=ProfileMapping(alternate.digest)
    )

    def configuration(assertions):
        return RuntimeConfiguration(
            profiles={
                base.digest: base,
                alternate.digest: alternate,
                changed.digest: changed,
            },
            host_mappings={"coordinator": ProfileMapping(base.digest)},
            campaign_assertions=assertions,
        )

    adapter = _InMemoryRuntimeProviderAdapter(store)
    first_gateway = RuntimeGateway(
        store_path=tmp_path / "gateway.journal",
        _adapter=adapter,
        configuration=configuration({key: assertion}),
        _artifacts=store,
    )
    first = first_gateway.planning_preflight(subject)
    durable = json.loads(
        first_gateway._store_path.read_text(encoding="utf-8")
    )
    assert durable["campaigns"][subject.campaign_handle]["overrides"] == (
        assertion.canonical()
    )

    absent_retry = RuntimeGateway(
        store_path=tmp_path / "gateway.journal",
        _adapter=adapter,
        configuration=configuration({}),
        _artifacts=store,
    )
    assert absent_retry.planning_preflight(subject) == first
    identical_retry = RuntimeGateway(
        store_path=tmp_path / "gateway.journal",
        _adapter=adapter,
        configuration=configuration({key: assertion}),
        _artifacts=store,
    )
    assert identical_retry.planning_preflight(subject) == first
    changed_retry = RuntimeGateway(
        store_path=tmp_path / "gateway.journal",
        _adapter=adapter,
        configuration=configuration(
            {
                key: CampaignStartRuntimeOverrides(
                    coordinator=ProfileMapping(changed.digest)
                )
            }
        ),
        _artifacts=store,
    )
    with pytest.raises(RuntimeGatewayError) as stopped:
        changed_retry.planning_preflight(subject)
    assert stopped.value.code == "RUNTIME_CAMPAIGN_IDENTITY_MISMATCH"

    concurrent_path = tmp_path / "concurrent-gateway.journal"
    first_contender = RuntimeGateway(
        store_path=concurrent_path,
        _adapter=adapter,
        configuration=configuration({key: assertion}),
        _artifacts=store,
    )
    second_contender = RuntimeGateway(
        store_path=concurrent_path,
        _adapter=adapter,
        configuration=configuration(
            {
                key: CampaignStartRuntimeOverrides(
                    coordinator=ProfileMapping(changed.digest)
                )
            }
        ),
        _artifacts=store,
    )
    first_contender.planning_preflight(subject)
    with pytest.raises(RuntimeGatewayError) as stopped:
        second_contender.planning_preflight(subject)
    assert stopped.value.code == "RUNTIME_CAMPAIGN_IDENTITY_MISMATCH"

    default_subject = _put_subject_artifacts(
        store,
        replace(
            _subject(),
            campaign_handle="handle:default",
            stable_action_id="planning:default",
        ),
    )
    default_key = (
        default_subject.repository,
        default_subject.campaign_key,
        default_subject.campaign_handle,
    )
    explicit_empty = RuntimeGateway(
        store_path=tmp_path / "default-gateway.journal",
        _adapter=adapter,
        configuration=configuration(
            {default_key: CampaignStartRuntimeOverrides()}
        ),
        _artifacts=store,
    )
    explicit_empty.planning_preflight(default_subject)
    default_durable = json.loads(
        explicit_empty._store_path.read_text(encoding="utf-8")
    )
    assert default_durable["campaigns"][default_subject.campaign_handle][
        "overrides"
    ] == CampaignStartRuntimeOverrides().canonical()


def test_work_run_uses_closed_semantic_purpose_and_gateway_maps_selector_privately(
    tmp_path,
):
    purpose_type = gateway_module.WorkRunPurpose
    purposes = (
        (purpose_type.implementation(), "worker"),
        (
            purpose_type.terminal_recovery_implementation(),
            "recovery_worker",
        ),
        (purpose_type.formal_review(), "review_primary"),
        (
            purpose_type.invalid_review_payload_retry(),
            "review_strong",
        ),
        (purpose_type.specialist_review("security"), "specialist:security"),
    )
    assert not hasattr(purpose_type, "primary_formal_review")
    assert not hasattr(purpose_type, "strong_formal_review_retry")
    store = ArtifactStore(tmp_path / "artifacts", maximum_bytes=300_000)
    planning = _put_subject_artifacts(store, _subject())
    profile = _profile()
    gateway = RuntimeGateway(
        store_path=tmp_path / "gateway.journal",
        _adapter=_InMemoryRuntimeProviderAdapter(store),
        configuration=RuntimeConfiguration(
            profiles={profile.digest: profile},
            host_mappings={
                selector: ProfileMapping(profile.digest)
                for _purpose, selector in purposes
            }
            | {"coordinator": ProfileMapping(profile.digest)},
        ),
        _artifacts=store,
    )
    gateway.planning_preflight(planning)
    plan_digest = store.put_canonical({"revision": 1}).digest
    for index, (purpose, expected_selector) in enumerate(purposes):
        subject = WorkRunSubject(
            repository=planning.repository,
            campaign_key=planning.campaign_key,
            campaign_handle=planning.campaign_handle,
            plan_revision_digest=plan_digest,
            work_run_key=f"work-run:{index}",
            ticket_key=f"issue:{index}",
            purpose=purpose,
            prompt_artifact_digest=planning.planning_request_artifact_digest,
            authority_subtree_digest=planning.policy_witness_digest,
            stable_action_id=f"work:{index}",
        )
        assert "role" not in subject.canonical()
        assert subject.canonical()["purpose"] == purpose.canonical()
        assignment = gateway._assignment_for_progress(subject)
        assert assignment["selector"] == expected_selector

    common = {
        "repository": planning.repository,
        "campaign_key": planning.campaign_key,
        "campaign_handle": planning.campaign_handle,
        "plan_revision_digest": plan_digest,
        "work_run_key": "work-run:invalid",
        "ticket_key": "issue:invalid",
        "prompt_artifact_digest": planning.planning_request_artifact_digest,
        "authority_subtree_digest": planning.policy_witness_digest,
        "stable_action_id": "work:invalid",
    }
    with pytest.raises(RuntimeGatewayError) as raw:
        WorkRunSubject(**common, purpose="worker")  # type: ignore[arg-type]
    assert raw.value.code == "RUNTIME_SUBJECT_INVALID"

    class FabricatedPurpose(purpose_type):
        pass

    with pytest.raises(RuntimeGatewayError) as fabricated:
        WorkRunSubject(
            **common,
            purpose=FabricatedPurpose("implementation"),
        )
    assert fabricated.value.code == "RUNTIME_SUBJECT_INVALID"


@pytest.mark.parametrize(
    ("command", "provider_verb", "pending_field"),
    (
        (RuntimeCommand.RESUME, "send", "pending_resume"),
        (RuntimeCommand.PARK, "stop", "pending_park"),
        (RuntimeCommand.RETIRE, "archive", "pending_retire"),
    ),
)
def test_postdispatch_protocol_failure_keeps_command_claim_and_blocks_duplicate(
    tmp_path, command, provider_verb, pending_field
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
    if command is RuntimeCommand.RESUME:
        assert not isinstance(
            _adapter_command(adapter, action_id, RuntimeCommand.PARK),
            _RuntimeFailure,
        )
        parked = adapter.observe(action_id)
        assert not isinstance(parked, _RuntimeFailure)
        assert parked.lifecycle == "parked"
    native_run = client._run
    provider_calls = 0

    def fail_after_dispatch(args):
        nonlocal provider_calls
        if args[0] == provider_verb:
            provider_calls += 1
            client.commands.append(list(args))
            raise RuntimeGatewayError(
                "RUNTIME_PROVIDER_PROTOCOL_INVALID",
                "post-dispatch output was oversized or malformed",
            )
        return native_run(args)

    client._run = fail_after_dispatch  # type: ignore[method-assign]
    first = _adapter_command(adapter, action_id, command)

    assert isinstance(first, _RuntimeFailure)
    assert first.code == "RUNTIME_PROVIDER_PROTOCOL_INVALID"
    assert adapter._actions[action_id][pending_field] is True
    assert provider_calls == 1

    client._run = native_run  # type: ignore[method-assign]
    restarted = _PaseoRuntimeProviderAdapter(
        client=client,  # type: ignore[arg-type]
        artifacts=store,
        repository_contexts={
            "owner/repository": RuntimeRepositoryContext(source, "main")
        },
        state_path=adapter._state_path,
    )
    retry_verdict = gateway_module._ObservationProtocol.validate(
        restarted._reconcile_observation(action_id),
        selected_stable_action_id=action_id,
    )
    assert retry_verdict.kind in {
        "bound",
        "fairness_advance",
        "failure",
    }
    assert type(retry_verdict.token) is gateway_module._RuntimeObservationReadToken
    observed = restarted.observe(action_id)
    retried = (
        observed
        if isinstance(observed, _RuntimeFailure)
        else restarted.command(action_id, command)
    )

    assert isinstance(retried, _RuntimeFailure)
    assert retried.code in {
        "RUNTIME_MATERIALIZATION_PENDING",
        "RUNTIME_EFFECT_AMBIGUOUS",
    }
    assert provider_calls == 1


def test_explicit_not_dispatched_start_restores_exact_claim_and_can_retry(tmp_path):
    store = ArtifactStore(tmp_path / "artifacts")
    source, workspace = _repository_worktree(tmp_path)
    client = _RecordingPaseoCli(workspace)
    adapter = _PaseoRuntimeProviderAdapter(
        client=client,  # type: ignore[arg-type]
        artifacts=store,
        repository_contexts={
            "owner/repository": RuntimeRepositoryContext(source, "main")
        },
        state_path=tmp_path / "paseo-actions.json",
    )
    second_subject = _put_subject_artifacts(
        store,
        replace(
            _subject(),
            campaign_handle="handle:not-dispatched",
            stable_action_id="planning:not-dispatched",
        ),
    )
    prompt = store.get(second_subject.planning_request_artifact_digest)
    spec = _RuntimeActionSpec(
        second_subject.stable_action_id,
        second_subject,
        _profile(),
        prompt,
        (prompt,),
    )
    assert not isinstance(adapter.prepare(spec), _RuntimeFailure)
    before = deepcopy(adapter._actions[second_subject.stable_action_id])
    native_run = client._run

    def fail_process_creation(args):
        if args[0] == "run":
            raise gateway_module._ProviderNotDispatched(
                OSError("Popen failed before dispatch")
            )
        return native_run(args)

    client._run = fail_process_creation  # type: ignore[method-assign]
    failed = _adapter_command(
        adapter, second_subject.stable_action_id, RuntimeCommand.START
    )

    assert isinstance(failed, _RuntimeFailure)
    assert failed.code == "RUNTIME_TRANSPORT_UNAVAILABLE"
    assert adapter._actions[second_subject.stable_action_id] == before
    client._run = native_run  # type: ignore[method-assign]
    retried = _adapter_command(
        adapter, second_subject.stable_action_id, RuntimeCommand.START
    )
    assert isinstance(retried, _CommandReceipt)


@pytest.mark.parametrize("reserved_name", (".gwo", ".GWO"))
def test_tracked_reserved_tree_is_rejected_before_provider_mutating_effect(
    tmp_path, reserved_name
):
    store = ArtifactStore(tmp_path / "artifacts")
    source, workspace = _repository_worktree(tmp_path)
    tracked = (
        source
        / reserved_name
        / "runtime-artifacts"
        / "repository-owned.json"
    )
    tracked.parent.mkdir(parents=True)
    tracked.write_text("repository content", encoding="utf-8")
    subprocess.run(
        ["git", "-C", str(source), "add", reserved_name],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(source), "commit", "-m", "occupy reserved runtime tree"],
        check=True,
        capture_output=True,
    )
    client = _RecordingPaseoCli(workspace)
    adapter = _PaseoRuntimeProviderAdapter(
        client=client,  # type: ignore[arg-type]
        artifacts=store,
        repository_contexts={
            "owner/repository": RuntimeRepositoryContext(source, "main")
        },
        state_path=tmp_path / "paseo-actions.json",
    )
    subject = _put_subject_artifacts(store, _subject())
    prompt = store.get(subject.planning_request_artifact_digest)
    spec = _RuntimeActionSpec(
        subject.stable_action_id, subject, _profile(), prompt, (prompt,)
    )

    rejected = adapter.prepare(spec)

    assert isinstance(rejected, _RuntimeFailure)
    assert rejected.code == "RUNTIME_WORKSPACE_UNSAFE"
    assert any(command[:2] == ["ls", "--global"] for command in client.commands)
    assert _mutating_paseo_commands(client.commands) == []


def test_readonly_workspace_discovery_rejects_gwo_link_before_mutating_effect(
    tmp_path,
):
    store = ArtifactStore(tmp_path / "artifacts")
    source, workspace = _repository_worktree(tmp_path)
    outside = tmp_path / "outside-runtime-tree"
    artifact_root = outside / "runtime-artifacts"
    artifact_root.mkdir(parents=True)
    subject = _put_subject_artifacts(store, _subject())
    prompt = store.get(subject.planning_request_artifact_digest)
    sentinel = artifact_root / f"{prompt.digest}.json"
    sentinel.write_bytes(b"outside sentinel")
    try:
        os.symlink(outside, workspace / ".gwo", target_is_directory=True)
    except OSError as error:
        pytest.skip(f"directory symlinks are unavailable: {error}")
    slug = digest_value(
        {
            "repository": subject.repository,
            "stable_action_id": subject.stable_action_id,
        }
    )[:24]
    client = _RecordingPaseoCli(workspace)
    client.workspaces = [
        {
            "workspaceId": "workspace:one",
            "name": slug,
            "isolation": "worktree",
            "project": "project:one",
            "cwd": str(workspace),
        }
    ]
    adapter = _PaseoRuntimeProviderAdapter(
        client=client,  # type: ignore[arg-type]
        artifacts=store,
        repository_contexts={
            "owner/repository": RuntimeRepositoryContext(source, "main")
        },
        state_path=tmp_path / "paseo-actions.json",
    )
    spec = _RuntimeActionSpec(
        subject.stable_action_id, subject, _profile(), prompt, (prompt,)
    )

    rejected = adapter.prepare(spec)

    assert isinstance(rejected, _RuntimeFailure)
    assert rejected.code == "RUNTIME_WORKSPACE_UNSAFE"
    assert sentinel.read_bytes() == b"outside sentinel"
    assert any(
        command[:2] == ["workspace", "ls"] for command in client.commands
    )
    assert _mutating_paseo_commands(client.commands) == []


def test_restart_rejects_journal_tampering_that_redirects_a_recorded_artifact(
    tmp_path,
):
    store = ArtifactStore(tmp_path / "artifacts")
    source, workspace = _repository_worktree(tmp_path)
    client = _RecordingPaseoCli(workspace)
    state_path = tmp_path / "paseo-actions.json"
    adapter = _PaseoRuntimeProviderAdapter(
        client=client,  # type: ignore[arg-type]
        artifacts=store,
        repository_contexts={
            "owner/repository": RuntimeRepositoryContext(source, "main")
        },
        state_path=state_path,
    )
    subject = _put_subject_artifacts(store, _subject())
    prompt = store.get(subject.planning_request_artifact_digest)
    spec = _RuntimeActionSpec(
        subject.stable_action_id, subject, _profile(), prompt, (prompt,)
    )
    assert not isinstance(adapter.prepare(spec), _RuntimeFailure)
    outside = tmp_path / "outside-prompt.json"
    outside.write_bytes(store.read_bytes(prompt.digest))
    original = json.loads(state_path.read_text(encoding="utf-8"))
    action_id = subject.stable_action_id
    mutations = (
        lambda action: action.__setitem__("prompt_file", str(outside)),
        lambda action: action.__setitem__("result_file", str(outside)),
        lambda action: action.__setitem__("output_schema_file", str(outside)),
        lambda action: action["input_files"].__setitem__(
            prompt.digest, str(outside)
        ),
    )
    for mutate in mutations:
        durable = deepcopy(original)
        mutate(durable["actions"][action_id])
        state_path.write_bytes(gateway_module.canonical_bytes(durable))
        restarted = _PaseoRuntimeProviderAdapter(
            client=client,  # type: ignore[arg-type]
            artifacts=store,
            repository_contexts={
                "owner/repository": RuntimeRepositoryContext(source, "main")
            },
            state_path=state_path,
        )
        observed = restarted.observe(action_id)
        assert isinstance(observed, _RuntimeFailure)
        assert observed.code == "RUNTIME_WORKSPACE_UNSAFE"
    assert outside.read_bytes() == store.read_bytes(prompt.digest)


def test_simulated_reparse_result_on_runtime_schema_is_rejected(
    tmp_path, monkeypatch
):
    store = ArtifactStore(tmp_path / "artifacts")
    source, workspace = _repository_worktree(tmp_path)
    client = _RecordingPaseoCli(workspace)
    adapter = _PaseoRuntimeProviderAdapter(
        client=client,  # type: ignore[arg-type]
        artifacts=store,
        repository_contexts={
            "owner/repository": RuntimeRepositoryContext(source, "main")
        },
        state_path=tmp_path / "paseo-actions.json",
    )
    subject = _put_subject_artifacts(store, _subject())
    prompt = store.get(subject.planning_request_artifact_digest)
    spec = _RuntimeActionSpec(
        subject.stable_action_id, subject, _profile(), prompt, (prompt,)
    )
    native_lstat = gateway_module.os.lstat

    class _ReparseStat:
        def __init__(self, value):
            self._value = value
            self.st_file_attributes = 0x400

        def __getattr__(self, name):
            return getattr(self._value, name)

    def mark_schema_as_reparse(path, *args, **kwargs):
        value = native_lstat(path, *args, **kwargs)
        if "runtime-schemas" in Path(path).parts and Path(path).suffix == ".json":
            return _ReparseStat(value)
        return value

    monkeypatch.setattr(gateway_module.os, "lstat", mark_schema_as_reparse)

    rejected = adapter.prepare(spec)

    assert isinstance(rejected, _RuntimeFailure)
    assert rejected.code == "RUNTIME_WORKSPACE_UNSAFE"
    assert all(command[0] != "run" for command in client.commands)


def test_runtime_artifact_subdirectory_link_is_rejected_without_outside_write(
    tmp_path,
):
    store = ArtifactStore(tmp_path / "artifacts")
    source, workspace = _repository_worktree(tmp_path)
    subject = _put_subject_artifacts(store, _subject())
    prompt = store.get(subject.planning_request_artifact_digest)
    runtime_root = workspace / ".gwo"
    runtime_root.mkdir()
    outside = tmp_path / "outside-artifacts"
    outside.mkdir()
    sentinel = outside / f"{prompt.digest}.json"
    sentinel.write_bytes(b"outside subdirectory sentinel")
    os.symlink(
        outside,
        runtime_root / "runtime-artifacts",
        target_is_directory=True,
    )
    slug = digest_value(
        {
            "repository": subject.repository,
            "stable_action_id": subject.stable_action_id,
        }
    )[:24]
    client = _RecordingPaseoCli(workspace)
    client.workspaces = [
        {
            "workspaceId": "workspace:one",
            "name": slug,
            "isolation": "worktree",
            "project": "project:one",
            "cwd": str(workspace),
        }
    ]
    adapter = _PaseoRuntimeProviderAdapter(
        client=client,  # type: ignore[arg-type]
        artifacts=store,
        repository_contexts={
            "owner/repository": RuntimeRepositoryContext(source, "main")
        },
        state_path=tmp_path / "paseo-actions.json",
    )

    rejected = adapter.prepare(
        _RuntimeActionSpec(
            subject.stable_action_id, subject, _profile(), prompt, (prompt,)
        )
    )

    assert isinstance(rejected, _RuntimeFailure)
    assert rejected.code == "RUNTIME_WORKSPACE_UNSAFE"
    assert sentinel.read_bytes() == b"outside subdirectory sentinel"
    assert any(
        command[:2] == ["workspace", "ls"] for command in client.commands
    )
    assert _mutating_paseo_commands(client.commands) == []


def test_runtime_artifact_hard_link_is_rejected_on_readback(tmp_path):
    store = ArtifactStore(tmp_path / "artifacts")
    source, workspace = _repository_worktree(tmp_path)
    client = _RecordingPaseoCli(workspace)
    adapter = _PaseoRuntimeProviderAdapter(
        client=client,  # type: ignore[arg-type]
        artifacts=store,
        repository_contexts={
            "owner/repository": RuntimeRepositoryContext(source, "main")
        },
        state_path=tmp_path / "paseo-actions.json",
    )
    subject = _put_subject_artifacts(store, _subject())
    prompt = store.get(subject.planning_request_artifact_digest)
    spec = _RuntimeActionSpec(
        subject.stable_action_id, subject, _profile(), prompt, (prompt,)
    )
    assert not isinstance(adapter.prepare(spec), _RuntimeFailure)
    prompt_path = Path(
        adapter._actions[subject.stable_action_id]["prompt_file"]
    )
    payload = prompt_path.read_bytes()
    prompt_path.unlink()
    outside = tmp_path / "hard-linked-prompt.json"
    outside.write_bytes(payload)
    os.link(outside, prompt_path)

    observed = adapter.observe(subject.stable_action_id)

    assert isinstance(observed, _RuntimeFailure)
    assert observed.code == "RUNTIME_WORKSPACE_UNSAFE"
    assert outside.read_bytes() == payload


def test_result_and_resume_links_are_rejected_before_provider_effect(tmp_path):
    store = ArtifactStore(tmp_path / "artifacts")
    source, workspace = _repository_worktree(tmp_path)
    client = _RecordingPaseoCli(workspace)
    adapter = _PaseoRuntimeProviderAdapter(
        client=client,  # type: ignore[arg-type]
        artifacts=store,
        repository_contexts={
            "owner/repository": RuntimeRepositoryContext(source, "main")
        },
        state_path=tmp_path / "paseo-actions.json",
    )
    subject = _put_subject_artifacts(store, _subject())
    prompt = store.get(subject.planning_request_artifact_digest)
    spec = _RuntimeActionSpec(
        subject.stable_action_id, subject, _profile(), prompt, (prompt,)
    )
    assert not isinstance(adapter.prepare(spec), _RuntimeFailure)
    record = adapter._actions[subject.stable_action_id]
    result_target = Path(record["result_file"])
    result_link_verdict = gateway_module._ObservationProtocol.validate(
        adapter._reconcile_observation(subject.stable_action_id),
        selected_stable_action_id=subject.stable_action_id,
    )
    assert result_link_verdict.kind == "prepared"
    assert type(result_link_verdict.token) is (
        gateway_module._RuntimeObservationReadToken
    )
    outside_result = tmp_path / "outside-result.json"
    outside_result.write_bytes(b"outside result sentinel")
    os.symlink(outside_result, result_target)
    run_count = len([command for command in client.commands if command[0] == "run"])

    rejected_start = adapter.observe(subject.stable_action_id)

    assert isinstance(rejected_start, _RuntimeFailure)
    assert rejected_start.code == "RUNTIME_WORKSPACE_UNSAFE"
    assert outside_result.read_bytes() == b"outside result sentinel"
    assert len([command for command in client.commands if command[0] == "run"]) == run_count

    result_target.unlink()
    assert not isinstance(
        _adapter_command(
            adapter, subject.stable_action_id, RuntimeCommand.START
        ),
        _RuntimeFailure,
    )
    assert not isinstance(
        _adapter_command(
            adapter, subject.stable_action_id, RuntimeCommand.PARK
        ),
        _RuntimeFailure,
    )
    assert not isinstance(adapter.observe(subject.stable_action_id), _RuntimeFailure)
    resume_target = workspace / ".gwo" / "runtime-artifacts" / "resume.txt"
    resume_link_verdict = gateway_module._ObservationProtocol.validate(
        adapter._reconcile_observation(subject.stable_action_id),
        selected_stable_action_id=subject.stable_action_id,
    )
    assert resume_link_verdict.kind == "bound"
    assert type(resume_link_verdict.token) is (
        gateway_module._RuntimeObservationReadToken
    )
    outside_resume = tmp_path / "outside-resume.txt"
    outside_resume.write_bytes(b"outside resume sentinel")
    os.symlink(outside_resume, resume_target)
    send_count = len(
        [command for command in client.commands if command[0] == "send"]
    )

    observed_resume = adapter.observe(subject.stable_action_id)
    assert not isinstance(observed_resume, _RuntimeFailure)
    rejected_resume = adapter.command(
        subject.stable_action_id,
        RuntimeCommand.RESUME,
    )

    assert isinstance(rejected_resume, _RuntimeFailure)
    assert rejected_resume.code == "RUNTIME_WORKSPACE_UNSAFE"
    assert outside_resume.read_bytes() == b"outside resume sentinel"
    assert len(
        [command for command in client.commands if command[0] == "send"]
    ) == send_count


def test_workspace_owner_staging_recovers_after_finalize_interruption(
    tmp_path, monkeypatch
):
    store = ArtifactStore(tmp_path / "artifacts")
    source, workspace = _repository_worktree(tmp_path)
    client = _RecordingPaseoCli(workspace)
    state_path = tmp_path / "paseo-actions.json"
    adapter = _PaseoRuntimeProviderAdapter(
        client=client,  # type: ignore[arg-type]
        artifacts=store,
        repository_contexts={
            "owner/repository": RuntimeRepositoryContext(source, "main")
        },
        state_path=state_path,
    )
    subject = _put_subject_artifacts(store, _subject())
    prompt = store.get(subject.planning_request_artifact_digest)
    spec = _RuntimeActionSpec(
        subject.stable_action_id, subject, _profile(), prompt, (prompt,)
    )
    native_rename = gateway_module.os.rename
    interrupted = False

    def interrupt_finalize(source_path, target_path, *args, **kwargs):
        nonlocal interrupted
        if (
            not interrupted
            and Path(source_path).name.startswith(".gwo-init-")
            and Path(target_path).name == ".gwo"
        ):
            interrupted = True
            raise OSError("simulated crash before ownership finalization")
        return native_rename(source_path, target_path, *args, **kwargs)

    monkeypatch.setattr(gateway_module.os, "rename", interrupt_finalize)
    failed = adapter.prepare(spec)

    assert isinstance(failed, _RuntimeFailure)
    assert failed.code == "RUNTIME_WORKSPACE_UNSAFE"
    intent = adapter._workspace_intents[subject.stable_action_id]
    nonce = intent["ownership_nonce"]
    assert (workspace / f".gwo-init-{nonce}").is_dir()
    assert len(
        [
            command
            for command in client.commands
            if command[:2] == ["workspace", "create"]
        ]
    ) == 1

    monkeypatch.setattr(gateway_module.os, "rename", native_rename)
    restarted = _PaseoRuntimeProviderAdapter(
        client=client,  # type: ignore[arg-type]
        artifacts=store,
        repository_contexts={
            "owner/repository": RuntimeRepositoryContext(source, "main")
        },
        state_path=state_path,
    )
    recovered = restarted.prepare(spec)

    assert not isinstance(recovered, _RuntimeFailure)
    assert restarted._actions[subject.stable_action_id][
        "workspace_owner_nonce"
    ] == nonce
    assert (workspace / ".gwo" / "runtime-owner.v1.json").is_file()
    assert len(
        [
            command
            for command in client.commands
            if command[:2] == ["workspace", "create"]
        ]
    ) == 1


def test_orphaned_nonce_owned_marker_temp_recovers_without_second_create(
    tmp_path, monkeypatch
):
    store = ArtifactStore(tmp_path / "artifacts")
    source, workspace = _repository_worktree(tmp_path)
    client = _RecordingPaseoCli(workspace)
    state_path = tmp_path / "paseo-actions.json"
    adapter = _PaseoRuntimeProviderAdapter(
        client=client,  # type: ignore[arg-type]
        artifacts=store,
        repository_contexts={
            "owner/repository": RuntimeRepositoryContext(source, "main")
        },
        state_path=state_path,
    )
    subject = _put_subject_artifacts(store, _subject())
    prompt = store.get(subject.planning_request_artifact_digest)
    spec = _RuntimeActionSpec(
        subject.stable_action_id, subject, _profile(), prompt, (prompt,)
    )
    native_establish = gateway_module._RuntimeWorkspaceFiles.establish

    def leave_partial_marker(self):
        staging = self.workspace / f".gwo-init-{self.ownership_nonce}"
        staging.mkdir()
        for directory_name in gateway_module._RUNTIME_WORKSPACE_DIRECTORIES:
            (staging / directory_name).mkdir()
        marker_temp = staging / (
            f"{gateway_module._RUNTIME_WORKSPACE_OWNER_FILE}."
            f"{self.ownership_nonce}.tmp"
        )
        marker_temp.write_bytes(b"partial owner marker")
        raise RuntimeGatewayError(
            "RUNTIME_WORKSPACE_UNSAFE",
            "simulated hard stop during owner marker replacement",
        )

    monkeypatch.setattr(
        gateway_module._RuntimeWorkspaceFiles,
        "establish",
        leave_partial_marker,
    )
    interrupted = adapter.prepare(spec)

    assert isinstance(interrupted, _RuntimeFailure)
    nonce = adapter._workspace_intents[subject.stable_action_id][
        "ownership_nonce"
    ]
    marker_temp = (
        workspace
        / f".gwo-init-{nonce}"
        / f"{gateway_module._RUNTIME_WORKSPACE_OWNER_FILE}.{nonce}.tmp"
    )
    assert marker_temp.is_file()
    assert len(
        [
            command
            for command in client.commands
            if command[:2] == ["workspace", "create"]
        ]
    ) == 1

    monkeypatch.setattr(
        gateway_module._RuntimeWorkspaceFiles,
        "establish",
        native_establish,
    )
    restarted = _PaseoRuntimeProviderAdapter(
        client=client,  # type: ignore[arg-type]
        artifacts=store,
        repository_contexts={
            "owner/repository": RuntimeRepositoryContext(source, "main")
        },
        state_path=state_path,
    )
    recovered = restarted.prepare(spec)

    assert not isinstance(recovered, _RuntimeFailure)
    assert restarted._actions[subject.stable_action_id][
        "workspace_owner_nonce"
    ] == nonce
    assert not marker_temp.exists()
    assert len(
        [
            command
            for command in client.commands
            if command[:2] == ["workspace", "create"]
        ]
    ) == 1


def _configuration_with_worker(profile: RuntimeProfile) -> RuntimeConfiguration:
    return RuntimeConfiguration(
        profiles={profile.digest: profile},
        host_mappings={
            "coordinator": ProfileMapping(profile.digest),
            "worker": ProfileMapping(profile.digest),
        },
    )


def test_planning_preflight_reservation_rejects_conflicting_work_run_before_adapter_use(
    tmp_path,
):
    store = ArtifactStore(tmp_path / "artifacts")
    profile = _profile()
    adapter = _InMemoryRuntimeProviderAdapter(store)
    gateway = RuntimeGateway(
        store_path=tmp_path / "gateway.journal",
        _adapter=adapter,
        configuration=_configuration_with_worker(profile),
        _artifacts=store,
    )
    planning = _put_subject_artifacts(
        store,
        replace(_subject(), stable_action_id="shared:planning-first"),
    )
    gateway.planning_preflight(planning)
    work = _put_work_subject_artifacts(
        store,
        planning,
        stable_action_id=planning.stable_action_id,
    )
    operations_before = (
        list(adapter.observe_calls),
        list(adapter.prepare_calls),
        list(adapter.command_calls),
    )

    with pytest.raises(RuntimeGatewayError) as stopped:
        gateway.progress(work)

    assert stopped.value.code == "RUNTIME_ACTION_IDENTITY_MISMATCH"
    assert (
        adapter.observe_calls,
        adapter.prepare_calls,
        adapter.command_calls,
    ) == operations_before
    durable = json.loads((tmp_path / "gateway.journal").read_text(encoding="utf-8"))
    assert planning.stable_action_id not in durable["actions"]
    assert durable["preflights"][planning.stable_action_id][
        "subject_digest"
    ] == planning.digest


def test_work_run_reservation_rejects_conflicting_preflight_without_partial_record(
    tmp_path,
):
    store = ArtifactStore(tmp_path / "artifacts")
    profile = _profile()
    adapter = _InMemoryRuntimeProviderAdapter(store)
    gateway = RuntimeGateway(
        store_path=tmp_path / "gateway.journal",
        _adapter=adapter,
        configuration=_configuration_with_worker(profile),
        _artifacts=store,
    )
    campaign = _put_subject_artifacts(
        store,
        replace(_subject(), stable_action_id="planning:campaign-setup"),
    )
    gateway.planning_preflight(campaign)
    work = _put_work_subject_artifacts(
        store,
        campaign,
        stable_action_id="shared:work-first",
    )
    gateway.progress(work)
    conflicting = replace(campaign, stable_action_id=work.stable_action_id)
    operations_before = (
        list(adapter.observe_calls),
        list(adapter.prepare_calls),
        list(adapter.command_calls),
    )

    with pytest.raises(RuntimeGatewayError) as stopped:
        gateway.planning_preflight(conflicting)

    assert stopped.value.code == "RUNTIME_PREFLIGHT_IDENTITY_MISMATCH"
    assert (
        adapter.observe_calls,
        adapter.prepare_calls,
        adapter.command_calls,
    ) == operations_before
    durable = json.loads((tmp_path / "gateway.journal").read_text(encoding="utf-8"))
    assert conflicting.stable_action_id not in durable["preflights"]
    assert durable["actions"][work.stable_action_id]["subject_digest"] == work.digest


def test_same_subject_reservation_replays_after_restart(tmp_path):
    store = ArtifactStore(tmp_path / "artifacts")
    profile = _profile()
    configuration = _configuration_with_worker(profile)
    adapter = _InMemoryRuntimeProviderAdapter(store)
    store_path = tmp_path / "gateway.journal"
    gateway = RuntimeGateway(
        store_path=store_path,
        _adapter=adapter,
        configuration=configuration,
        _artifacts=store,
    )
    planning = _put_subject_artifacts(
        store,
        replace(_subject(), stable_action_id="planning:exact-replay"),
    )
    first_preflight = gateway.planning_preflight(planning)
    first_progress = gateway.progress(planning, first_preflight)

    restarted = RuntimeGateway(
        store_path=store_path,
        _adapter=adapter,
        configuration=configuration,
        _artifacts=store,
    )

    assert restarted.planning_preflight(planning) == first_preflight
    replayed = restarted.progress(planning, first_preflight)
    assert replayed.subject_digest == first_progress.subject_digest
    assert replayed.stable_action_id == first_progress.stable_action_id
    assert replayed.status == first_progress.status
    assert (
        replayed.planning_output_artifact_digest
        == first_progress.planning_output_artifact_digest
    )
    assert adapter.prepare_calls == [planning.stable_action_id]


def test_schema_one_reservation_rebuild_rejects_cross_map_identity_conflict(
    tmp_path,
):
    store = ArtifactStore(tmp_path / "artifacts")
    profile = _profile()
    configuration = _configuration_with_worker(profile)
    gateway = RuntimeGateway(
        store_path=tmp_path / "gateway.journal",
        _adapter=_InMemoryRuntimeProviderAdapter(store),
        configuration=configuration,
        _artifacts=store,
    )
    planning = _put_subject_artifacts(
        store,
        replace(_subject(), stable_action_id="planning:legacy-setup"),
    )
    gateway.planning_preflight(planning)
    work = _put_work_subject_artifacts(
        store,
        planning,
        stable_action_id="shared:legacy-conflict",
    )
    gateway.progress(work)
    durable_path = tmp_path / "gateway.journal"
    durable = json.loads(durable_path.read_text(encoding="utf-8"))
    durable.pop("action_identities", None)
    durable["preflights"][work.stable_action_id] = {
        "subject_digest": planning.digest,
        "campaign_overrides_digest": "0" * 64,
        "assignment": durable["preflights"][planning.stable_action_id]["assignment"],
        "receipt_digest": "1" * 64,
    }
    durable_path.write_bytes(gateway_module.canonical_bytes(durable))

    with pytest.raises(RuntimeGatewayError) as stopped:
        RuntimeGateway(
            store_path=durable_path,
            _adapter=_InMemoryRuntimeProviderAdapter(store),
            configuration=configuration,
            _artifacts=store,
        )

    assert stopped.value.code == "RUNTIME_STORE_INVALID"


def test_concurrent_gateways_reserve_only_one_subject_identity(tmp_path):
    store = ArtifactStore(tmp_path / "artifacts")
    profile = _profile()
    configuration = _configuration_with_worker(profile)
    store_path = tmp_path / "gateway.journal"
    setup_gateway = RuntimeGateway(
        store_path=store_path,
        _adapter=_InMemoryRuntimeProviderAdapter(store),
        configuration=configuration,
        _artifacts=store,
    )
    campaign = _put_subject_artifacts(
        store,
        replace(_subject(), stable_action_id="planning:concurrency-setup"),
    )
    setup_gateway.planning_preflight(campaign)
    shared_id = "shared:concurrent-first-write"
    planning = replace(campaign, stable_action_id=shared_id)
    work = _put_work_subject_artifacts(
        store,
        campaign,
        stable_action_id=shared_id,
    )
    planning_gateway = RuntimeGateway(
        store_path=store_path,
        _adapter=_InMemoryRuntimeProviderAdapter(store),
        configuration=configuration,
        _artifacts=store,
    )
    work_gateway = RuntimeGateway(
        store_path=store_path,
        _adapter=_InMemoryRuntimeProviderAdapter(store),
        configuration=configuration,
        _artifacts=store,
    )
    barrier = threading.Barrier(2)
    outcomes: list[tuple[str, object]] = []
    outcome_lock = threading.Lock()

    def attempt(kind, operation):
        barrier.wait()
        try:
            result = operation()
        except RuntimeGatewayError as error:
            result = error
        with outcome_lock:
            outcomes.append((kind, result))

    planning_thread = threading.Thread(
        target=attempt,
        args=("planning", lambda: planning_gateway.planning_preflight(planning)),
    )
    work_thread = threading.Thread(
        target=attempt,
        args=("work", lambda: work_gateway.progress(work)),
    )
    planning_thread.start()
    work_thread.start()
    planning_thread.join(timeout=30)
    work_thread.join(timeout=30)

    assert not planning_thread.is_alive()
    assert not work_thread.is_alive()
    assert len([result for _kind, result in outcomes if not isinstance(result, Exception)]) == 1
    failures = [result for _kind, result in outcomes if isinstance(result, RuntimeGatewayError)]
    assert len(failures) == 1
    assert failures[0].code in {
        "RUNTIME_ACTION_IDENTITY_MISMATCH",
        "RUNTIME_PREFLIGHT_IDENTITY_MISMATCH",
    }
    durable = json.loads(store_path.read_text(encoding="utf-8"))
    persisted = [
        durable[collection][shared_id]["subject_digest"]
        for collection in ("preflights", "actions")
        if shared_id in durable[collection]
    ]
    assert persisted in ([planning.digest], [work.digest])


def test_runtime_profile_recursively_defends_and_freezes_json_features():
    original_features = {
        "flags": {
            "levels": [1, 2],
            "nested": {"enabled": True},
        }
    }
    profile = RuntimeProfile(
        name="immutable",
        provider="test",
        model="test-model",
        thinking="high",
        mode="safe",
        features=original_features,
    )
    expected_canonical = (
        b'{"features":{"flags":{"levels":[1,2],"nested":{"enabled":true}}},'
        b'"mode":"safe","model":"test-model","name":"immutable",'
        b'"provider":"test","thinking":"high"}'
    )
    expected_digest = hashlib.sha256(expected_canonical).hexdigest()

    original_features["flags"]["levels"].append(3)
    original_features["flags"]["nested"]["enabled"] = False

    assert profile.digest == expected_digest
    assert gateway_module.canonical_bytes(asdict(profile)) == expected_canonical
    assert profile.features == {
        "flags": {
            "levels": [1, 2],
            "nested": {"enabled": True},
        }
    }
    with pytest.raises(TypeError):
        profile.features["flags"]["nested"]["enabled"] = False
    with pytest.raises(TypeError):
        profile.features["flags"]["levels"].append(3)


def test_runtime_configuration_defensively_copies_and_deeply_freezes_registries():
    profile = _profile()
    digest = profile.digest
    mapping = ProfileMapping(digest)
    profiles = {digest: profile}
    host_mappings = {"coordinator": mapping}
    repository_role_mappings = {"worker": mapping}
    repository_mappings = {"owner/repository": repository_role_mappings}
    ticket_overrides = {("issue:111", "worker"): mapping}
    assertion = CampaignStartRuntimeOverrides(
        coordinator=mapping,
        ticket_overrides=ticket_overrides,
    )
    assertion_key = ("owner/repository", "campaign:repair", "handle:repair")
    campaign_assertions = {assertion_key: assertion}

    configuration = RuntimeConfiguration(
        profiles=profiles,
        host_mappings=host_mappings,
        repository_mappings=repository_mappings,
        campaign_assertions=campaign_assertions,
    )

    profiles.clear()
    host_mappings.clear()
    repository_role_mappings.clear()
    repository_mappings.clear()
    ticket_overrides.clear()
    campaign_assertions.clear()

    assert configuration.profiles[digest] is not profile
    assert configuration.profiles[digest] == profile
    assert configuration.host_mappings[
        gateway_module.RuntimeSelector.coordinator()
    ] == mapping
    assert configuration.repository_mappings["owner/repository"][
        gateway_module.RuntimeSelector.worker()
    ] == mapping
    assert configuration.campaign_assertions[assertion_key].ticket_overrides[
        ("issue:111", "worker")
    ] == mapping
    with pytest.raises(TypeError):
        configuration.profiles[digest] = profile
    with pytest.raises(TypeError):
        configuration.host_mappings[
            gateway_module.RuntimeSelector.coordinator()
        ] = mapping
    with pytest.raises(TypeError):
        configuration.repository_mappings["owner/repository"][
            gateway_module.RuntimeSelector.worker()
        ] = mapping
    with pytest.raises(TypeError):
        configuration.campaign_assertions[assertion_key].ticket_overrides[
            ("issue:112", "worker")
        ] = mapping


@pytest.mark.parametrize("operation", ("preflight", "progress", "transition"))
@pytest.mark.parametrize(
    "drift",
    (
        lambda profile: replace(profile, name="drifted"),
        lambda _profile_value: object(),
    ),
)
def test_gateway_uses_private_profile_snapshot_after_caller_registry_drift(
    tmp_path, operation, drift
):
    store = ArtifactStore(tmp_path / "artifacts")
    profile = _profile()
    configuration = RuntimeConfiguration(
        profiles={profile.digest: profile},
        host_mappings={"coordinator": ProfileMapping(profile.digest)},
    )
    adapter = _InMemoryRuntimeProviderAdapter(store)
    gateway = RuntimeGateway(
        store_path=tmp_path / "gateway.journal",
        _adapter=adapter,
        configuration=configuration,
        _artifacts=store,
    )
    subject = _put_subject_artifacts(store, _subject())
    receipt = None
    if operation in {"progress", "transition"}:
        receipt = gateway.planning_preflight(subject)
    if operation == "transition":
        gateway.progress(subject, receipt)
    object.__setattr__(
        configuration,
        "profiles",
        {profile.digest: drift(profile)},
    )
    operations_before = (
        list(adapter.observe_calls),
        list(adapter.prepare_calls),
        list(adapter.command_calls),
    )

    if operation == "preflight":
        result = gateway.planning_preflight(subject)
        assert result.subject_digest == subject.digest
        assert (
            adapter.observe_calls,
            adapter.prepare_calls,
            adapter.command_calls,
        ) == operations_before
    elif operation == "progress":
        result = gateway.progress(subject, receipt)
        assert result.stable_action_id == subject.stable_action_id
    else:
        result = gateway.transition(subject.stable_action_id, RuntimeCommand.FENCE)
        assert result.stable_action_id == subject.stable_action_id
        assert adapter._actions[subject.stable_action_id].fenced is True
    if operation != "preflight":
        assert (
            gateway._data["actions"][subject.stable_action_id]["profile_digest"]
            == profile.digest
        )
    assert gateway._configuration.profiles[profile.digest] == profile


@pytest.mark.parametrize("operation", ("progress", "transition"))
@pytest.mark.parametrize("fallback_state", ("missing", "drifted", "exact"))
def test_persisted_fallback_uses_private_profile_snapshot_after_caller_registry_drift(
    tmp_path, operation, fallback_state
):
    store = ArtifactStore(tmp_path / "artifacts")
    primary = _profile()
    fallback = replace(primary, name="fallback")
    configuration = RuntimeConfiguration(
        profiles={
            primary.digest: primary,
            fallback.digest: fallback,
        },
        host_mappings={
            "coordinator": ProfileMapping(
                primary.digest,
                fallback.digest,
            )
        },
    )
    adapter = _InMemoryRuntimeProviderAdapter(store)
    gateway = RuntimeGateway(
        store_path=tmp_path / "gateway.journal",
        _adapter=adapter,
        configuration=configuration,
        _artifacts=store,
    )
    subject = _put_subject_artifacts(store, _subject())
    preflight = gateway.planning_preflight(subject)
    persisted_assignment = deepcopy(
        gateway._data["preflights"][subject.stable_action_id]["assignment"]
    )
    if operation == "transition":
        gateway.progress(subject, preflight)
    profiles = {primary.digest: primary}
    if fallback_state == "exact":
        profiles[fallback.digest] = fallback
    elif fallback_state == "drifted":
        profiles[fallback.digest] = replace(fallback, name="drifted-fallback")
    object.__setattr__(configuration, "profiles", profiles)
    operations_before = (
        list(adapter.observe_calls),
        list(adapter.prepare_calls),
        list(adapter.command_calls),
    )

    if operation == "progress":
        completed = gateway.progress(subject, preflight)
        expected_command = RuntimeCommand.START
        assert adapter.prepare_calls[len(operations_before[1]):] == [
            subject.stable_action_id
        ]
    else:
        completed = gateway.transition(
            subject.stable_action_id,
            RuntimeCommand.FENCE,
        )
        expected_command = RuntimeCommand.FENCE
        assert adapter.prepare_calls == operations_before[1]

    assert completed.status == "completed"
    assert adapter.command_calls[len(operations_before[2]):] == [
        (subject.stable_action_id, expected_command.value)
    ]
    assert len(adapter.observe_calls) > len(operations_before[0])
    action = gateway._data["actions"][subject.stable_action_id]
    assert action["profile_digest"] == primary.digest
    assert action["availability_fallback_profile_digest"] == fallback.digest
    assert action["fallback_selected"] is False
    assert (
        adapter._actions[subject.stable_action_id].spec.profile.digest
        == primary.digest
    )
    assert (
        gateway._data["preflights"][subject.stable_action_id]["assignment"]
        == persisted_assignment
    )
    assert gateway._configuration.profiles[primary.digest] == primary
    assert gateway._configuration.profiles[fallback.digest] == fallback


def _inspect_payload_with_equal_aliases() -> dict[str, object]:
    return {
        "id": "agent:one",
        "Id": "agent:one",
        "agentId": "agent:one",
        "provider": "test",
        "Provider": "test",
        "model": "model",
        "Model": "model",
        "thinking": "high",
        "Thinking": "high",
        "mode": "safe",
        "Mode": "safe",
        "cwd": "C:/workspace",
        "Cwd": "C:/workspace",
        "status": "running",
        "Status": "running",
        "archived": False,
        "Archived": False,
        "PendingPermissions": [],
    }


def test_paseo_inspect_accepts_equal_compatibility_aliases():
    transport = _PaseoCliTransport("paseo")
    transport._run = lambda _args: _inspect_payload_with_equal_aliases()  # type: ignore[method-assign]

    observed = transport.inspect("agent:one")

    assert observed.agent_id == "agent:one"
    assert observed.provider == "test"
    assert observed.model == "model"
    assert observed.thinking == "high"
    assert observed.mode == "safe"
    assert observed.cwd == "C:/workspace"
    assert observed.lifecycle == "running"
    assert observed.archived is False


@pytest.mark.parametrize(
    ("alias", "conflict"),
    (
        ("Id", "agent:other"),
        ("Provider", "other-provider"),
        ("Model", "other-model"),
        ("Thinking", "low"),
        ("Mode", "unsafe"),
        ("Cwd", "C:/other-workspace"),
        ("Status", "parked"),
        ("Archived", True),
    ),
)
def test_paseo_inspect_rejects_conflicting_populated_aliases(alias, conflict):
    payload = _inspect_payload_with_equal_aliases()
    payload[alias] = conflict
    transport = _PaseoCliTransport("paseo")
    transport._run = lambda _args: payload  # type: ignore[method-assign]

    with pytest.raises(RuntimeGatewayError) as stopped:
        transport.inspect("agent:one")

    assert stopped.value.code == "RUNTIME_IDENTITY_AMBIGUOUS"


def test_paseo_workspace_decoder_accepts_equal_compatibility_aliases():
    assert _PaseoRuntimeProviderAdapter._workspace_payload(
        {
            "id": "workspace:one",
            "Id": "workspace:one",
            "workspaceId": "workspace:one",
            "path": "C:/workspace",
            "Path": "C:/workspace",
            "cwd": "C:/workspace",
        }
    ) == ("workspace:one", "C:/workspace")


@pytest.mark.parametrize(
    ("alias", "conflict"),
    (
        ("Id", "workspace:other"),
        ("workspaceId", "workspace:other"),
        ("Path", "C:/other-workspace"),
        ("cwd", "C:/other-workspace"),
    ),
)
def test_paseo_workspace_decoder_rejects_conflicting_populated_aliases(
    alias, conflict
):
    payload = {
        "id": "workspace:one",
        "Id": "workspace:one",
        "workspaceId": "workspace:one",
        "path": "C:/workspace",
        "Path": "C:/workspace",
        "cwd": "C:/workspace",
    }
    payload[alias] = conflict

    with pytest.raises(RuntimeGatewayError) as stopped:
        _PaseoRuntimeProviderAdapter._workspace_payload(payload)

    assert stopped.value.code == "RUNTIME_IDENTITY_AMBIGUOUS"


def test_paseo_agent_list_accepts_equal_compatibility_aliases(tmp_path):
    store = ArtifactStore(tmp_path / "artifacts")
    source, workspace = _repository_worktree(tmp_path)
    client = _RecordingPaseoCli(workspace)
    client.agent = SimpleNamespace(
        agent_id="agent:one",
        provider="test",
        model="test-model",
        thinking="high",
        mode="safe",
        cwd=str(workspace),
        lifecycle="running",
        archived=False,
    )
    native_run = client._run

    def equal_alias_list(args):
        if args[:2] == ["ls", "--global"]:
            client.commands.append(list(args))
            return [
                {
                    "id": "agent:one",
                    "Id": "agent:one",
                    "agentId": "agent:one",
                    "AgentId": "agent:one",
                }
            ]
        return native_run(args)

    client._run = equal_alias_list  # type: ignore[method-assign]
    adapter = _PaseoRuntimeProviderAdapter(
        client=client,  # type: ignore[arg-type]
        artifacts=store,
        repository_contexts={
            "owner/repository": RuntimeRepositoryContext(source, "main")
        },
        state_path=tmp_path / "paseo-actions.json",
    )

    observed = adapter._one_agent(
        {"gwo.runtime_action": "planning:repair"},
        include_archived=True,
    )

    assert observed.agent_id == "agent:one"


def test_conflicting_agent_list_aliases_stop_prepare_before_provider_mutation(
    tmp_path,
):
    store = ArtifactStore(tmp_path / "artifacts")
    source, workspace = _repository_worktree(tmp_path)
    client = _RecordingPaseoCli(workspace)
    client.agent = SimpleNamespace(
        agent_id="agent:one",
        provider="test",
        model="test-model",
        thinking="high",
        mode="safe",
        cwd=str(workspace),
        lifecycle="running",
        archived=False,
    )
    native_run = client._run

    def conflicting_agent_list(args):
        if args[:2] == ["ls", "--global"]:
            client.commands.append(list(args))
            return [{"id": "agent:one", "AgentId": "agent:other"}]
        return native_run(args)

    client._run = conflicting_agent_list  # type: ignore[method-assign]
    adapter = _PaseoRuntimeProviderAdapter(
        client=client,  # type: ignore[arg-type]
        artifacts=store,
        repository_contexts={
            "owner/repository": RuntimeRepositoryContext(source, "main")
        },
        state_path=tmp_path / "paseo-actions.json",
    )
    subject = _put_subject_artifacts(store, _subject())
    prompt = store.get(subject.planning_request_artifact_digest)

    rejected = adapter.prepare(
        _RuntimeActionSpec(
            subject.stable_action_id,
            subject,
            _profile(),
            prompt,
            (prompt,),
        )
    )

    assert isinstance(rejected, _RuntimeFailure)
    assert rejected.code == "RUNTIME_IDENTITY_AMBIGUOUS"
    assert adapter._actions == {}
    assert _mutating_paseo_commands(client.commands) == []


def test_conflicting_workspace_list_aliases_do_not_persist_wrong_identity_or_mutate(
    tmp_path,
):
    store = ArtifactStore(tmp_path / "artifacts")
    source, workspace = _repository_worktree(tmp_path)
    client = _RecordingPaseoCli(workspace)
    subject = _put_subject_artifacts(store, _subject())
    prompt = store.get(subject.planning_request_artifact_digest)
    slug = digest_value(
        {
            "repository": subject.repository,
            "stable_action_id": subject.stable_action_id,
        }
    )[:24]
    native_run = client._run

    def conflicting_workspace_list(args):
        if args[:2] == ["workspace", "ls"]:
            client.commands.append(list(args))
            return [
                {
                    "id": "workspace:wrong",
                    "workspaceId": "workspace:one",
                    "name": slug,
                    "isolation": "worktree",
                    "path": str(workspace),
                    "cwd": str(workspace),
                }
            ]
        return native_run(args)

    client._run = conflicting_workspace_list  # type: ignore[method-assign]
    adapter = _PaseoRuntimeProviderAdapter(
        client=client,  # type: ignore[arg-type]
        artifacts=store,
        repository_contexts={
            "owner/repository": RuntimeRepositoryContext(source, "main")
        },
        state_path=tmp_path / "paseo-actions.json",
    )

    rejected = adapter.prepare(
        _RuntimeActionSpec(
            subject.stable_action_id,
            subject,
            _profile(),
            prompt,
            (prompt,),
        )
    )

    assert isinstance(rejected, _RuntimeFailure)
    assert rejected.code == "RUNTIME_IDENTITY_AMBIGUOUS"
    assert adapter._actions == {}
    assert _mutating_paseo_commands(client.commands) == []


def test_artifact_store_put_flushes_fsyncs_replaces_and_finally_verifies(
    tmp_path, monkeypatch
):
    store = ArtifactStore(tmp_path / "artifacts")
    payload = b"durable artifact payload"
    events: list[tuple[str, object]] = []
    native_fsync = os.fsync
    native_replace = os.replace

    def recording_fsync(fd):
        events.append(("fsync", os.fstat(fd).st_size))
        return native_fsync(fd)

    def recording_replace(source, target):
        events.append(("replace", (Path(source).name, Path(target).name)))
        return native_replace(source, target)

    monkeypatch.setattr(gateway_module.os, "fsync", recording_fsync)
    monkeypatch.setattr(gateway_module.os, "replace", recording_replace)

    reference = store.put(payload)

    assert store.read_bytes(reference.digest) == payload
    replace_index = next(
        index for index, event in enumerate(events) if event[0] == "replace"
    )
    assert any(
        event == ("fsync", len(payload))
        for event in events[:replace_index]
    )
    assert not list((tmp_path / "artifacts").glob("*.tmp"))


def test_runtime_journal_fsyncs_before_atomic_replace(tmp_path, monkeypatch):
    journal = gateway_module._V3JsonJournal(tmp_path / "runtime.journal")
    payload = {"sequence": 1}
    events: list[tuple[str, object]] = []
    native_fsync = os.fsync
    native_replace = os.replace

    def recording_fsync(fd):
        events.append(("fsync", os.fstat(fd).st_size))
        return native_fsync(fd)

    def recording_replace(source, target):
        events.append(("replace", (Path(source).name, Path(target).name)))
        return native_replace(source, target)

    monkeypatch.setattr(gateway_module.os, "fsync", recording_fsync)
    monkeypatch.setattr(gateway_module.os, "replace", recording_replace)

    with journal.exclusive():
        journal.replace_unlocked(payload)

    replace_index = next(
        index for index, event in enumerate(events) if event[0] == "replace"
    )
    assert any(
        event == ("fsync", len(gateway_module.canonical_bytes(payload)))
        for event in events[:replace_index]
    )
    assert journal.read_unlocked() == payload


def test_artifact_store_exclusive_temp_never_overwrites_unowned_collision(
    tmp_path, monkeypatch
):
    store = ArtifactStore(tmp_path / "artifacts")
    payload = b"exclusive temporary"
    digest = hashlib.sha256(payload).hexdigest()
    root = tmp_path / "artifacts"
    root.mkdir()
    collided = root / f"{digest}.fixed.tmp"
    collided.write_bytes(b"unowned sentinel")
    monkeypatch.setattr(
        gateway_module,
        "uuid4",
        lambda: SimpleNamespace(hex="fixed"),
    )

    with pytest.raises(RuntimeGatewayError) as stopped:
        store.put(payload)

    assert stopped.value.code == "RUNTIME_ARTIFACT_UNAVAILABLE"
    assert collided.read_bytes() == b"unowned sentinel"
    assert not (root / digest).exists()


def test_artifact_store_replace_failure_cleans_only_its_temporary(
    tmp_path, monkeypatch
):
    store = ArtifactStore(tmp_path / "artifacts")
    payload = b"replace failure"
    digest = hashlib.sha256(payload).hexdigest()

    def fail_replace(_source, _target):
        raise OSError("simulated replace failure")

    monkeypatch.setattr(gateway_module.os, "replace", fail_replace)

    with pytest.raises(RuntimeGatewayError) as stopped:
        store.put(payload)

    assert stopped.value.code == "RUNTIME_ARTIFACT_UNAVAILABLE"
    assert not store.path_for(digest).exists()
    assert not list((tmp_path / "artifacts").glob(f"{digest}.*.tmp"))


def test_artifact_store_final_verification_rejects_post_replace_corruption(
    tmp_path, monkeypatch
):
    store = ArtifactStore(tmp_path / "artifacts")
    payload = b"verify after replace"
    digest = hashlib.sha256(payload).hexdigest()
    native_replace = os.replace

    def corrupt_after_replace(source, target):
        native_replace(source, target)
        Path(target).write_bytes(b"corrupt")

    monkeypatch.setattr(gateway_module.os, "replace", corrupt_after_replace)

    with pytest.raises(RuntimeGatewayError) as stopped:
        store.put(payload)

    assert stopped.value.code == "RUNTIME_ARTIFACT_DIGEST_MISMATCH"
    assert not list((tmp_path / "artifacts").glob(f"{digest}.*.tmp"))


def test_artifact_store_concurrent_same_digest_writers_are_idempotent(tmp_path):
    store = ArtifactStore(tmp_path / "artifacts")
    payload = b"same digest from concurrent writers"
    barrier = threading.Barrier(8)
    references: list[gateway_module.ArtifactRef] = []
    failures: list[Exception] = []
    result_lock = threading.Lock()

    def writer():
        barrier.wait()
        try:
            result = store.put(payload)
        except Exception as error:
            with result_lock:
                failures.append(error)
        else:
            with result_lock:
                references.append(result)

    threads = [threading.Thread(target=writer) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert all(not thread.is_alive() for thread in threads)
    assert failures == []
    assert len(references) == 8
    assert len({reference.digest for reference in references}) == 1
    assert store.read_bytes(references[0].digest) == payload
    assert not list((tmp_path / "artifacts").glob("*.tmp"))


def test_artifact_store_existing_corrupt_digest_target_fails_closed(tmp_path):
    store = ArtifactStore(tmp_path / "artifacts")
    payload = b"expected artifact"
    digest = hashlib.sha256(payload).hexdigest()
    target = store.path_for(digest)
    target.parent.mkdir()
    target.write_bytes(b"corrupt existing target")

    with pytest.raises(RuntimeGatewayError) as stopped:
        store.put(payload)

    assert stopped.value.code == "RUNTIME_ARTIFACT_DIGEST_MISMATCH"
    assert target.read_bytes() == b"corrupt existing target"


def test_output_artifact_put_failure_never_persists_completion_or_receipt(
    tmp_path, monkeypatch
):
    gateway, store, adapter = _gateway(tmp_path)
    subject = _put_subject_artifacts(store, _subject())
    preflight = gateway.planning_preflight(subject)

    def fail_output_put(_payload):
        raise RuntimeGatewayError(
            "RUNTIME_ARTIFACT_UNAVAILABLE",
            "simulated durable Artifact failure",
        )

    monkeypatch.setattr(store, "put", fail_output_put)

    with pytest.raises(RuntimeGatewayError) as stopped:
        gateway.progress(subject, preflight)

    assert stopped.value.code == "RUNTIME_ARTIFACT_UNAVAILABLE"
    durable = json.loads((tmp_path / "gateway.journal").read_text(encoding="utf-8"))
    action = durable["actions"][subject.stable_action_id]
    assert action["lifecycle"] == "prepared"
    assert action["planning_output_artifact_digest"] is None
    assert adapter._actions[subject.stable_action_id].output_artifact_digest is None


def test_repair_packet_a_strict_canonical_domain_and_ingress_are_typed(tmp_path):
    store = ArtifactStore(tmp_path / "artifacts")

    with pytest.raises(RuntimeGatewayError) as nonfinite:
        store.put_canonical({"nested": [{"value": float("nan")}]})
    assert nonfinite.value.code == "RUNTIME_ARTIFACT_INVALID"

    with pytest.raises(RuntimeGatewayError) as colliding_keys:
        store.put_canonical({1: "numeric", "1": "text"})
    assert colliding_keys.value.code == "RUNTIME_ARTIFACT_INVALID"

    journal = gateway_module._V3JsonJournal(tmp_path / "runtime.journal")
    journal.path.write_bytes(b'{"value":NaN}')
    with journal.exclusive():
        with pytest.raises(RuntimeGatewayError) as invalid_journal:
            journal.read_unlocked()
    assert invalid_journal.value.code == "RUNTIME_STORE_INVALID"


def test_repair_packet_b_profiles_and_configuration_use_composed_snapshots():
    profile = RuntimeProfile(
        name="immutable",
        provider="test",
        model="test-model",
        thinking="high",
        mode="safe",
        features={"nested": {"levels": [1, 2]}},
    )
    configuration = RuntimeConfiguration(
        profiles={profile.digest: profile},
        host_mappings={"coordinator": ProfileMapping(profile.digest)},
    )
    configured = configuration.profiles[profile.digest]
    expected_digest = configured.digest

    with pytest.raises(TypeError):
        dict.__setitem__(profile.features, "injected", True)
    with pytest.raises(TypeError):
        list.append(profile.features["nested"]["levels"], 3)

    with pytest.raises((TypeError, AttributeError)):
        object.__setattr__(profile, "name", "forced-alias-drift")
    assert configured.name == "immutable"
    assert configured.digest == expected_digest
    assert dict(configured.features)["nested"]["levels"] == (1, 2)


def test_repair_packet_c_preflight_receipt_binds_whole_campaign_overrides(tmp_path):
    profile = _profile()
    fallback = replace(profile, name="ticket-fallback")
    subject = _subject()

    def receipt(root: Path, overrides: CampaignStartRuntimeOverrides):
        store = ArtifactStore(root / "artifacts", maximum_bytes=300_000)
        configured_subject = _put_subject_artifacts(store, subject)
        configuration = RuntimeConfiguration(
            profiles={profile.digest: profile, fallback.digest: fallback},
            host_mappings={"coordinator": ProfileMapping(profile.digest)},
            campaign_assertions={
                (
                    configured_subject.repository,
                    configured_subject.campaign_key,
                    configured_subject.campaign_handle,
                ): overrides
            },
        )
        gateway = RuntimeGateway(
            store_path=root / "gateway.journal",
            _adapter=_InMemoryRuntimeProviderAdapter(store),
            configuration=configuration,
            _artifacts=store,
        )
        return gateway.planning_preflight(configured_subject)

    empty = receipt(tmp_path / "empty", CampaignStartRuntimeOverrides())
    ticket_only = receipt(
        tmp_path / "ticket",
        CampaignStartRuntimeOverrides(
            ticket_overrides={
                ("issue:111", "worker"): ProfileMapping(fallback.digest)
            }
        ),
    )

    assert empty.subject_digest == ticket_only.subject_digest
    assert empty.receipt_digest != ticket_only.receipt_digest


def test_repair_packet_d_prepared_result_must_be_proven_absent(tmp_path):
    store = ArtifactStore(tmp_path / "artifacts")
    source, workspace = _repository_worktree(tmp_path)
    client = _RecordingPaseoCli(workspace)
    adapter = _PaseoRuntimeProviderAdapter(
        client=client,  # type: ignore[arg-type]
        artifacts=store,
        repository_contexts={
            "owner/repository": RuntimeRepositoryContext(source, "main")
        },
        state_path=tmp_path / "paseo-actions.json",
    )
    subject = _put_subject_artifacts(store, _subject())
    prompt = store.get(subject.planning_request_artifact_digest)
    spec = _RuntimeActionSpec(
        subject.stable_action_id, subject, _profile(), prompt, (prompt,)
    )
    assert not isinstance(adapter.prepare(spec), _RuntimeFailure)
    result_path = Path(adapter._actions[subject.stable_action_id]["result_file"])
    result_path.write_bytes(
        gateway_module.canonical_bytes(
            {
                "schema_version": "gwo.runtime.output.v1",
                "subject_digest": subject.digest,
                "stable_action_id": subject.stable_action_id,
                "authority_digest": subject.authority_digest,
                "payload": {"planted": True},
            }
        )
    )

    observed = adapter.observe(subject.stable_action_id)
    replayed = adapter.prepare(spec)

    assert isinstance(observed, _RuntimeFailure)
    assert observed.code == "RUNTIME_RESULT_PROVENANCE_INVALID"
    assert isinstance(replayed, _RuntimeFailure)
    assert replayed.code == "RUNTIME_RESULT_PROVENANCE_INVALID"
    assert all(command[0] != "run" for command in client.commands)


def test_repair_packet_e_not_dispatched_create_rolls_back_before_readback(
    tmp_path,
):
    store = ArtifactStore(tmp_path / "artifacts")
    source, workspace = _repository_worktree(tmp_path)
    client = _RecordingPaseoCli(workspace)
    adapter = _PaseoRuntimeProviderAdapter(
        client=client,  # type: ignore[arg-type]
        artifacts=store,
        repository_contexts={
            "owner/repository": RuntimeRepositoryContext(source, "main")
        },
        state_path=tmp_path / "paseo-actions.json",
    )
    subject = _put_subject_artifacts(store, _subject())
    prompt = store.get(subject.planning_request_artifact_digest)
    spec = _RuntimeActionSpec(
        subject.stable_action_id, subject, _profile(), prompt, (prompt,)
    )
    native_run = client._run
    create_rejected = False

    def fail_create_and_readback(args):
        nonlocal create_rejected
        if args[:2] == ["workspace", "create"]:
            client.commands.append(list(args))
            create_rejected = True
            raise gateway_module._ProviderNotDispatched(
                OSError("provider process was never created")
            )
        if create_rejected and args[:2] == ["workspace", "ls"]:
            client.commands.append(list(args))
            raise OSError("registry is independently unavailable")
        return native_run(args)

    client._run = fail_create_and_readback  # type: ignore[method-assign]
    failed = adapter.prepare(spec)

    assert isinstance(failed, _RuntimeFailure)
    assert failed.code == "RUNTIME_TRANSPORT_UNAVAILABLE"
    assert adapter._workspace_intents[subject.stable_action_id]["phase"] == "recorded"

    client._run = native_run  # type: ignore[method-assign]
    retried = adapter.prepare(spec)
    assert not isinstance(retried, _RuntimeFailure)
    assert len(
        [
            command
            for command in client.commands
            if command[:2] == ["workspace", "create"]
        ]
    ) == 2


def test_repair_packet_f_memory_completion_publication_is_recoverable(
    tmp_path, monkeypatch
):
    store = ArtifactStore(tmp_path / "artifacts", maximum_bytes=300_000)
    adapter = _InMemoryRuntimeProviderAdapter(store)
    subject = _put_subject_artifacts(store, _subject())
    prompt = store.get(subject.planning_request_artifact_digest)
    spec = _RuntimeActionSpec(
        subject.stable_action_id, subject, _profile(), prompt, (prompt,)
    )
    assert not isinstance(adapter.prepare(spec), _RuntimeFailure)
    native_put = store.put

    def fail_publication(_payload):
        raise RuntimeGatewayError(
            "RUNTIME_ARTIFACT_UNAVAILABLE", "simulated publication failure"
        )

    monkeypatch.setattr(store, "put", fail_publication)
    failed = _adapter_command(
        adapter, subject.stable_action_id, RuntimeCommand.START
    )
    assert isinstance(failed, _RuntimeFailure)
    assert failed.code == "RUNTIME_ARTIFACT_UNAVAILABLE"

    running = adapter.observe(subject.stable_action_id)
    assert not isinstance(running, _RuntimeFailure)
    assert running.binding_ref is not None
    assert running.lifecycle == "running"
    assert running.planning_output_artifact_digest is None

    monkeypatch.setattr(store, "put", native_put)
    completed = adapter.observe(subject.stable_action_id)
    assert not isinstance(completed, _RuntimeFailure)
    assert completed.lifecycle == "completed"
    assert completed.planning_output_artifact_digest is not None
    assert adapter.created_agent_count == 1


def test_repair_packet_g_workspace_registry_selection_is_target_scoped(tmp_path):
    client = _RecordingPaseoCli(tmp_path / "unused")
    duplicate_path = tmp_path / "duplicate"
    client.workspaces = [
        {
            "workspaceId": "workspace:one",
            "name": "stable-slug",
            "isolation": "worktree",
            "cwd": str(duplicate_path),
        },
        {
            "workspaceId": "workspace:two",
            "name": "stable-slug",
            "isolation": "container",
            "cwd": str(tmp_path / "other"),
        },
    ]
    adapter = _PaseoRuntimeProviderAdapter(
        client=client,  # type: ignore[arg-type]
        artifacts=ArtifactStore(tmp_path / "artifacts"),
        repository_contexts={},
        state_path=tmp_path / "paseo-actions.json",
    )

    assert adapter._workspace_by_identity(slug="stable-slug") == (
        "workspace:one",
        str(duplicate_path),
    )


def test_repair_packet_h_bound_prepare_replay_accepts_descendant_commit(tmp_path):
    (
        _store,
        _source,
        workspace,
        _client,
        adapter,
        _subject_value,
        spec,
    ) = _prepared_paseo_adapter(tmp_path)
    candidate = workspace / "candidate-replay.txt"
    candidate.write_text("candidate\n", encoding="utf-8")
    subprocess.run(
        ["git", "-C", str(workspace), "add", "candidate-replay.txt"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(workspace), "commit", "-m", "candidate replay"],
        check=True,
        capture_output=True,
    )

    replayed = adapter.prepare(spec)

    assert not isinstance(replayed, _RuntimeFailure)
    assert replayed.stable_action_id == spec.stable_action_id


def test_repair_packet_4_sealed_runtime_values_reject_reinitialization_and_attribute_bypasses():
    profile = RuntimeProfile(
        name="sealed",
        provider="test",
        model="test-model",
        thinking="high",
        mode="safe",
        features={"nested": {"levels": [1, 2]}},
    )
    other = replace(profile, name="other")
    mapping = ProfileMapping(profile.digest)
    selector = gateway_module.RuntimeSelector.worker()
    overrides = CampaignStartRuntimeOverrides(
        coordinator=mapping,
        ticket_overrides={("issue:111", "worker"): mapping},
    )
    feature_object = profile.features
    feature_array = profile.features["nested"]["levels"]
    expected_profile = profile.canonical()
    expected_digest = profile.digest
    expected_mapping = (
        mapping.primary_profile_digest,
        mapping.availability_fallback_profile_digest,
    )
    expected_selector = selector.value
    expected_overrides = overrides.canonical()
    mutations = (
        lambda: type(feature_object).__init__(
            feature_object, (("replaced", True),)
        ),
        lambda: object.__setattr__(
            feature_object,
            "_ImmutableJsonObject__entries",
            (("replaced", True),),
        ),
        lambda: type(feature_array).__init__(feature_array, (99,)),
        lambda: object.__setattr__(
            feature_array, "_ImmutableJsonArray__values", (99,)
        ),
        lambda: type(profile).__init__(
            profile,
            name="changed",
            provider="other",
            model="other",
            thinking="low",
            mode="unsafe",
            features={},
        ),
        lambda: object.__setattr__(profile, "name", "changed"),
        lambda: object.__delattr__(profile, "name"),
        lambda: type(mapping).__init__(mapping, other.digest, None),
        lambda: object.__setattr__(
            mapping, "primary_profile_digest", other.digest
        ),
        lambda: type(selector).__init__(selector, "review_primary"),
        lambda: object.__setattr__(selector, "value", "review_primary"),
        lambda: type(overrides).__init__(
            overrides,
            coordinator=ProfileMapping(other.digest),
            ticket_overrides={},
        ),
        lambda: object.__setattr__(
            overrides, "coordinator", ProfileMapping(other.digest)
        ),
    )

    for mutate in mutations:
        with pytest.raises((TypeError, AttributeError)):
            mutate()
        assert profile.canonical() == expected_profile
        assert asdict(profile) == expected_profile
        assert profile.digest == expected_digest
        assert dict(profile.features) == expected_profile["features"]
        assert (
            mapping.primary_profile_digest,
            mapping.availability_fallback_profile_digest,
        ) == expected_mapping
        assert selector.value == expected_selector
        assert overrides.canonical() == expected_overrides


def test_repair_packet_4_gateway_ignores_configuration_replacement_after_snapshot(
    tmp_path,
):
    store = ArtifactStore(tmp_path / "artifacts", maximum_bytes=300_000)
    primary = _profile()
    other = replace(primary, name="other-registered")
    configuration = RuntimeConfiguration(
        profiles={primary.digest: primary, other.digest: other},
        host_mappings={"coordinator": ProfileMapping(primary.digest)},
    )
    adapter = _InMemoryRuntimeProviderAdapter(store)
    gateway = RuntimeGateway(
        store_path=tmp_path / "gateway.journal",
        _adapter=adapter,
        configuration=configuration,
        _artifacts=store,
    )
    subject = _put_subject_artifacts(store, _subject())
    object.__setattr__(
        configuration,
        "host_mappings",
        {
            gateway_module.RuntimeSelector.coordinator(): ProfileMapping(
                other.digest
            )
        },
    )

    preflight = gateway.planning_preflight(subject)
    gateway.progress(subject, preflight)

    assert (
        adapter._actions[subject.stable_action_id].spec.profile.digest
        == primary.digest
    )


def test_repair_packet_4_campaign_override_tamper_breaks_preflight_cross_record_binding(
    tmp_path,
):
    store = ArtifactStore(tmp_path / "artifacts", maximum_bytes=300_000)
    primary = _profile()
    other = replace(primary, name="other-worker")
    subject = _put_subject_artifacts(store, _subject())
    configuration = RuntimeConfiguration(
        profiles={primary.digest: primary, other.digest: other},
        host_mappings={
            "coordinator": ProfileMapping(primary.digest),
            "worker": ProfileMapping(primary.digest),
        },
        campaign_assertions={
            (
                subject.repository,
                subject.campaign_key,
                subject.campaign_handle,
            ): CampaignStartRuntimeOverrides(
                ticket_overrides={
                    ("issue:111", "worker"): ProfileMapping(primary.digest)
                }
            )
        },
    )
    adapter = _InMemoryRuntimeProviderAdapter(store)
    journal_path = tmp_path / "gateway.journal"
    gateway = RuntimeGateway(
        store_path=journal_path,
        _adapter=adapter,
        configuration=configuration,
        _artifacts=store,
    )
    gateway.planning_preflight(subject)
    durable = json.loads(journal_path.read_text(encoding="utf-8"))
    campaign = durable["campaigns"][subject.campaign_handle]
    campaign["overrides"][
        "ticket_overrides"
    ][0]["mapping"]["primary_profile_digest"] = other.digest
    campaign["overrides_digest"] = digest_value(campaign["overrides"])
    campaign["preflight_bindings"][subject.stable_action_id][
        "campaign_overrides_digest"
    ] = campaign["overrides_digest"]
    journal_path.write_bytes(gateway_module.canonical_bytes(durable))

    with pytest.raises(RuntimeGatewayError) as rejected:
        RuntimeGateway(
            store_path=journal_path,
            _adapter=adapter,
            configuration=configuration,
            _artifacts=store,
        )

    assert rejected.value.code == "RUNTIME_STORE_INVALID"
    assert adapter.prepare_calls == []
    assert adapter.observe_calls == []
    assert adapter.command_calls == []


@pytest.mark.parametrize("cycle_kind", ("object", "nested_array"))
def test_repair_packet_4_canonical_cycles_are_typed_at_shared_artifact_and_profile_boundaries(
    tmp_path, cycle_kind
):
    if cycle_kind == "object":
        value: object = {}
        value["self"] = value  # type: ignore[index]
    else:
        nested: list[object] = []
        value = {"nested": [nested]}
        nested.append(value)

    with pytest.raises(canonical_module.CanonicalJsonError):
        canonical_module.canonical_bytes(value)
    with pytest.raises(RuntimeGatewayError) as artifact:
        ArtifactStore(tmp_path / "artifacts").put_canonical(value)
    assert artifact.value.code == "RUNTIME_ARTIFACT_INVALID"
    with pytest.raises(TypeError):
        RuntimeProfile(
            name="cycle",
            provider="test",
            model="test-model",
            thinking="high",
            mode="safe",
            features={"value": value},
        )


def test_repair_packet_4_canonical_depth_limit_has_exact_boundary(tmp_path):
    accepted: object = None
    for _index in range(canonical_module._MAX_CANONICAL_JSON_DEPTH):
        accepted = [accepted]
    rejected = [accepted]

    assert canonical_module.strict_json_loads(
        canonical_module.canonical_bytes(accepted)
    ) == accepted
    with pytest.raises(canonical_module.CanonicalJsonError):
        canonical_module.canonical_bytes(rejected)
    with pytest.raises(RuntimeGatewayError) as artifact:
        ArtifactStore(tmp_path / "artifacts").put_canonical(
            {"nested": rejected}
        )
    assert artifact.value.code == "RUNTIME_ARTIFACT_INVALID"
    with pytest.raises(TypeError):
        RuntimeProfile(
            name="deep",
            provider="test",
            model="test-model",
            thinking="high",
            mode="safe",
            features={"nested": rejected},
        )


def test_repair_packet_4_oversized_nested_integer_ingress_is_typed(tmp_path):
    payload = ('{"nested":[' + ("9" * 10_000) + "]}").encode("utf-8")

    with pytest.raises(canonical_module.CanonicalJsonError):
        canonical_module.strict_json_loads(payload)
    with pytest.raises(RuntimeGatewayError) as artifact:
        ArtifactStore._canonical_json(payload)
    assert artifact.value.code == "RUNTIME_ARTIFACT_INVALID"

    journal = gateway_module._V3JsonJournal(tmp_path / "runtime.journal")
    journal.path.write_bytes(payload)
    with journal.exclusive():
        with pytest.raises(RuntimeGatewayError) as store:
            journal.read_unlocked()
    assert store.value.code == "RUNTIME_STORE_INVALID"


def test_repair_packet_4_bound_prepared_lifecycle_is_rejected_before_persistence(
    tmp_path,
):
    gateway, store, adapter = _gateway(tmp_path)
    subject = _put_subject_artifacts(store, _subject())
    preflight = gateway.planning_preflight(subject)
    gateway.progress(subject, preflight)

    read = adapter._reconcile_observation(subject.stable_action_id)
    observed = read.result
    assert isinstance(observed, gateway_module._BoundRuntimeObservation)
    forged = replace(observed, lifecycle="prepared")
    adapter._reconcile_observation = (  # type: ignore[method-assign]
        lambda _stable_action_id: replace(read, result=forged)
    )
    journal_path = tmp_path / "gateway.journal"
    durable_before = journal_path.read_bytes()
    state_before = deepcopy(gateway._data)
    adapter_actions_before = deepcopy(adapter._actions)
    adapter_events_before = deepcopy(adapter._events)
    adapter_scan_before = adapter._event_scan_cursor
    adapter_next_event_before = adapter._next_event_cursor
    commands_before = list(adapter.command_calls)

    with pytest.raises(RuntimeGatewayError) as rejected:
        gateway.progress(subject, preflight)

    assert rejected.value.code == "RUNTIME_PROVIDER_PROTOCOL_INVALID"
    assert journal_path.read_bytes() == durable_before
    assert gateway._data == state_before
    assert adapter._actions == adapter_actions_before
    assert adapter._events == adapter_events_before
    assert adapter._event_scan_cursor == adapter_scan_before
    assert adapter._next_event_cursor == adapter_next_event_before
    assert adapter.command_calls == commands_before


@pytest.mark.parametrize("adapter_kind", ("memory", "paseo"))
def test_repair_packet_5_invalid_bound_lifecycle_never_publishes_adapter_observation(
    tmp_path, adapter_kind
):
    if adapter_kind == "memory":
        store = ArtifactStore(tmp_path / "artifacts")
        subject = _put_subject_artifacts(store, _subject())
        prompt = store.get(subject.planning_request_artifact_digest)
        adapter = _InMemoryRuntimeProviderAdapter(store)
        spec = _RuntimeActionSpec(
            subject.stable_action_id, subject, _profile(), prompt, (prompt,)
        )
        assert not isinstance(adapter.prepare(spec), _RuntimeFailure)
        assert not isinstance(
            _adapter_command(
                adapter, subject.stable_action_id, RuntimeCommand.START
            ),
            _RuntimeFailure,
        )
        adapter._actions[subject.stable_action_id].lifecycle = "prepared"
        durable_before = None
    else:
        (
            _store,
            _source,
            workspace,
            client,
            adapter,
            subject,
            _spec,
        ) = _prepared_paseo_adapter(tmp_path)
        descendant = workspace / "invalid-lifecycle-descendant.txt"
        descendant.write_text("descendant\n", encoding="utf-8")
        subprocess.run(
            ["git", "-C", str(workspace), "add", descendant.name],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(workspace), "commit", "-m", "invalid lifecycle"],
            check=True,
            capture_output=True,
        )
        assert client.agent is not None
        client.agent.lifecycle = "prepared"
        durable_before = adapter._state_path.read_bytes()

    actions_before = deepcopy(adapter._actions)
    events_before = deepcopy(adapter._events)
    scan_before = adapter._event_scan_cursor
    next_event_before = adapter._next_event_cursor

    page = adapter.events(None)

    assert not isinstance(page, _RuntimeFailure)
    assert page.events == ()
    assert adapter._actions == actions_before
    assert adapter._events == events_before
    assert adapter._event_scan_cursor == scan_before
    assert adapter._next_event_cursor == next_event_before
    if durable_before is not None:
        assert adapter._state_path.read_bytes() == durable_before


def test_repair_packet_5_sealed_feature_object_is_a_complete_mapping():
    expected_features = {
        "enabled": True,
        "nested": {"levels": [1, 2]},
    }
    profile = RuntimeProfile(
        name="mapping-contract",
        provider="test",
        model="test-model",
        thinking="high",
        mode="safe",
        features=expected_features,
    )
    features = profile.features
    missing = object()

    assert features == expected_features
    assert expected_features == features
    assert not features != expected_features
    assert not expected_features != features
    assert "enabled" in features
    assert "nested" in features
    assert "missing" not in features
    assert set(iter(features)) == {"enabled", "nested"}
    assert len(features) == 2
    assert features.get("enabled") is True
    assert features.get("missing", missing) is missing
    assert set(features.keys()) == {"enabled", "nested"}
    assert dict(features.items()) == expected_features
    assert dict(features) == expected_features
    assert dict(features)["nested"]["levels"] == (1, 2)
    assert asdict(profile) == profile.canonical() == {
        "name": "mapping-contract",
        "provider": "test",
        "model": "test-model",
        "thinking": "high",
        "mode": "safe",
        "features": expected_features,
    }
    with pytest.raises(TypeError):
        type(features).__init__(features, (("replaced", True),))
    with pytest.raises((TypeError, AttributeError)):
        object.__setattr__(features, "enabled", False)


def test_repair_packet_5_sealed_feature_array_comparison_is_symmetric():
    profile = RuntimeProfile(
        name="array-contract",
        provider="test",
        model="test-model",
        thinking="high",
        mode="safe",
        features={"values": [1, {"nested": True}]},
    )
    values = profile.features["values"]
    expected = [1, {"nested": True}]

    assert values == expected
    assert expected == values
    assert not values != expected
    assert not expected != values
    assert asdict(profile)["features"]["values"] == expected
    assert profile.canonical()["features"]["values"] == expected
    with pytest.raises(TypeError):
        type(values).__init__(values, (99,))
    with pytest.raises((TypeError, AttributeError)):
        object.__setattr__(values, "injected", 99)


@pytest.mark.parametrize(
    "split_registry",
    ("host_mappings", "repository_mappings", "profiles", "campaign_assertions"),
)
def test_repair_packet_5_gateway_resolves_only_from_deep_configuration_snapshot(
    tmp_path,
    split_registry,
):
    store = ArtifactStore(tmp_path / "artifacts", maximum_bytes=300_000)
    primary = _profile()
    other = replace(
        primary,
        name="split-view-other",
        features={"unsupported": True},
    )
    subject = _put_subject_artifacts(store, _subject())
    selector = gateway_module.RuntimeSelector.coordinator()
    primary_mapping = ProfileMapping(primary.digest)
    other_mapping = ProfileMapping(other.digest)
    configuration = RuntimeConfiguration(
        profiles={primary.digest: primary, other.digest: other},
        host_mappings={selector: primary_mapping},
    )
    if split_registry == "host_mappings":
        split_view = _SplitViewMapping(
            {selector: primary_mapping},
            {selector: other_mapping},
        )
        object.__setattr__(configuration, "host_mappings", split_view)
    elif split_registry == "repository_mappings":
        split_view = _SplitViewMapping(
            {selector: primary_mapping},
            {selector: other_mapping},
        )
        object.__setattr__(
            configuration,
            "repository_mappings",
            {subject.repository: split_view},
        )
    elif split_registry == "profiles":
        split_view = _SplitViewMapping(
            {primary.digest: primary, other.digest: other},
            {primary.digest: other},
        )
        object.__setattr__(configuration, "profiles", split_view)
    else:
        campaign_key = (
            subject.repository,
            subject.campaign_key,
            subject.campaign_handle,
        )
        split_view = _SplitViewMapping(
            {
                campaign_key: CampaignStartRuntimeOverrides(
                    coordinator=primary_mapping
                )
            },
            {
                campaign_key: CampaignStartRuntimeOverrides(
                    coordinator=other_mapping
                )
            },
        )
        object.__setattr__(
            configuration,
            "campaign_assertions",
            split_view,
        )
    adapter = _InMemoryRuntimeProviderAdapter(store)
    gateway = RuntimeGateway(
        store_path=tmp_path / "gateway.journal",
        _adapter=adapter,
        configuration=configuration,
        _artifacts=store,
    )
    split_view.split = True

    preflight = gateway.planning_preflight(subject)
    gateway.progress(subject, preflight)

    assert (
        adapter._actions[subject.stable_action_id].spec.profile.digest
        == primary.digest
    )


def test_repair_packet_5_campaign_groups_cannot_swap_preflight_ownership(
    tmp_path,
):
    store = ArtifactStore(tmp_path / "artifacts", maximum_bytes=300_000)
    primary = _profile()
    other = replace(primary, name="campaign-b-worker")
    subject_a = _put_subject_artifacts(
        store,
        replace(
            _subject(),
            campaign_key="campaign:a",
            campaign_handle="handle:a",
            stable_action_id="planning:a",
        ),
    )
    subject_b = _put_subject_artifacts(
        store,
        replace(
            _subject(),
            campaign_key="campaign:b",
            campaign_handle="handle:b",
            stable_action_id="planning:b",
        ),
    )
    configuration = RuntimeConfiguration(
        profiles={primary.digest: primary, other.digest: other},
        host_mappings={"coordinator": ProfileMapping(primary.digest)},
        campaign_assertions={
            (
                subject_a.repository,
                subject_a.campaign_key,
                subject_a.campaign_handle,
            ): CampaignStartRuntimeOverrides(
                ticket_overrides={
                    ("issue:a", "worker"): ProfileMapping(primary.digest)
                }
            ),
            (
                subject_b.repository,
                subject_b.campaign_key,
                subject_b.campaign_handle,
            ): CampaignStartRuntimeOverrides(
                ticket_overrides={
                    ("issue:b", "worker"): ProfileMapping(other.digest)
                }
            ),
        },
    )
    adapter = _InMemoryRuntimeProviderAdapter(store)
    journal_path = tmp_path / "gateway.journal"
    gateway = RuntimeGateway(
        store_path=journal_path,
        _adapter=adapter,
        configuration=configuration,
        _artifacts=store,
    )
    gateway.planning_preflight(subject_a)
    gateway.planning_preflight(subject_b)
    durable = json.loads(journal_path.read_text(encoding="utf-8"))
    campaign_a = durable["campaigns"][subject_a.campaign_handle]
    campaign_b = durable["campaigns"][subject_b.campaign_handle]
    for key in ("overrides", "overrides_digest", "preflight_bindings"):
        campaign_a[key], campaign_b[key] = campaign_b[key], campaign_a[key]
    journal_path.write_bytes(gateway_module.canonical_bytes(durable))

    with pytest.raises(RuntimeGatewayError) as rejected:
        RuntimeGateway(
            store_path=journal_path,
            _adapter=adapter,
            configuration=configuration,
            _artifacts=store,
        )

    assert rejected.value.code == "RUNTIME_STORE_INVALID"
    assert adapter.prepare_calls == []
    assert adapter.observe_calls == []
    assert adapter.command_calls == []


def test_repair_packet_5_campaign_without_owning_preflight_fails_closed(
    tmp_path,
):
    gateway, store, adapter = _gateway(tmp_path)
    subject = _put_subject_artifacts(store, _subject())
    gateway.planning_preflight(subject)
    journal_path = tmp_path / "gateway.journal"
    durable = json.loads(journal_path.read_text(encoding="utf-8"))
    durable["campaigns"][subject.campaign_handle][
        "preflight_bindings"
    ] = {}
    durable["preflights"].pop(subject.stable_action_id)
    durable["action_identities"].pop(subject.stable_action_id)
    journal_path.write_bytes(gateway_module.canonical_bytes(durable))

    with pytest.raises(RuntimeGatewayError) as rejected:
        profile = _profile()
        RuntimeGateway(
            store_path=journal_path,
            _adapter=adapter,
            configuration=RuntimeConfiguration(
                profiles={profile.digest: profile},
                host_mappings={
                    "coordinator": ProfileMapping(profile.digest)
                },
            ),
            _artifacts=store,
        )

    assert rejected.value.code == "RUNTIME_STORE_INVALID"
    assert adapter.prepare_calls == []
    assert adapter.observe_calls == []
    assert adapter.command_calls == []


@pytest.mark.parametrize("action_kind", ("planning", "work"))
def test_repair_packet_5_existing_action_requires_owning_campaign_and_preflight(
    tmp_path,
    action_kind,
):
    store = ArtifactStore(tmp_path / "artifacts", maximum_bytes=300_000)
    profile = _profile()
    configuration = RuntimeConfiguration(
        profiles={profile.digest: profile},
        host_mappings={
            "coordinator": ProfileMapping(profile.digest),
            "worker": ProfileMapping(profile.digest),
        },
    )
    adapter = _InMemoryRuntimeProviderAdapter(store)
    journal_path = tmp_path / "gateway.journal"
    gateway = RuntimeGateway(
        store_path=journal_path,
        _adapter=adapter,
        configuration=configuration,
        _artifacts=store,
    )
    planning = _put_subject_artifacts(store, _subject())
    preflight = gateway.planning_preflight(planning)
    if action_kind == "planning":
        target = planning
        gateway.progress(planning, preflight)
    else:
        target = _put_work_subject_artifacts(
            store,
            planning,
            stable_action_id="work:orphan-fast-path",
        )
        gateway.progress(target)
    durable = json.loads(journal_path.read_text(encoding="utf-8"))
    durable["campaigns"].pop(planning.campaign_handle)
    durable["preflights"].pop(planning.stable_action_id)
    if action_kind == "work":
        durable["action_identities"].pop(planning.stable_action_id)
    journal_path.write_bytes(gateway_module.canonical_bytes(durable))
    prepare_before = list(adapter.prepare_calls)
    observe_before = list(adapter.observe_calls)
    commands_before = list(adapter.command_calls)

    with pytest.raises(RuntimeGatewayError) as rejected:
        gateway.progress(
            target,
            preflight if action_kind == "planning" else None,
        )

    assert rejected.value.code == "RUNTIME_STORE_INVALID"
    assert adapter.prepare_calls == prepare_before
    assert adapter.observe_calls == observe_before
    assert adapter.command_calls == commands_before


@pytest.mark.parametrize(
    "malformed_field",
    (
        "running_fenced",
        "running_binding",
        "running_profile_digest",
        "prepared_fenced",
        "parked_permissions",
        "completed_output_digest",
    ),
)
def test_repair_packet_6_memory_rejects_complete_malformed_observation_before_publication(
    tmp_path,
    malformed_field,
):
    store = ArtifactStore(tmp_path / "artifacts", maximum_bytes=300_000)
    profile = _profile()
    subject = _put_subject_artifacts(store, _subject())
    request = ("request:packet6", "write", "repository:packet6")
    adapter = _InMemoryRuntimeProviderAdapter(
        store,
        pending_permissions={subject.stable_action_id: (request,)},
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
    running = gateway.progress(subject, preflight)
    assert running.status == "running"
    action = adapter._actions[subject.stable_action_id]

    if malformed_field == "running_fenced":
        action.pending_permissions.clear()
        action.fenced = "false"  # type: ignore[assignment]
    elif malformed_field == "running_binding":
        action.pending_permissions.clear()
        action.binding_ref = ""
    elif malformed_field == "running_profile_digest":
        action.pending_permissions.clear()
        action.spec = replace(
            action.spec,
            profile=replace(action.spec.profile, name="drifted-after-binding"),
        )
    elif malformed_field == "prepared_fenced":
        action.binding_ref = None
        action.lifecycle = "prepared"
        action.fenced = "false"  # type: ignore[assignment]
    elif malformed_field == "parked_permissions":
        gateway.transition(subject.stable_action_id, RuntimeCommand.PARK)
        action.pending_permissions.append(request)
    else:
        gateway.transition(
            subject.stable_action_id,
            PermissionResponse(request_id=request[0], decision="allow"),
        )
        action.output_artifact_digest = "not-a-digest"

    artifact_root = tmp_path / "artifacts"

    def artifact_snapshot():
        return {
            path.relative_to(artifact_root).as_posix(): path.read_bytes()
            for path in artifact_root.rglob("*")
            if path.is_file()
        }

    journal_path = tmp_path / "gateway.journal"
    durable_before = journal_path.read_bytes()
    gateway_state_before = deepcopy(gateway._data)
    actions_before = deepcopy(adapter._actions)
    artifacts_before = artifact_snapshot()
    events_before = deepcopy(adapter._events)
    scan_before = adapter._event_scan_cursor
    next_event_before = adapter._next_event_cursor
    commands_before = list(adapter.command_calls)

    observed = adapter.observe(subject.stable_action_id)

    assert type(observed) is _RuntimeFailure
    assert observed.code == "RUNTIME_OBSERVATION_INVALID"
    page = adapter.events(
        None if next_event_before == 1 else str(next_event_before - 1)
    )
    assert not isinstance(page, _RuntimeFailure)
    assert page.events == ()
    with pytest.raises(RuntimeGatewayError) as rejected:
        gateway.progress(subject, preflight)
    assert rejected.value.code == "RUNTIME_OBSERVATION_INVALID"
    assert journal_path.read_bytes() == durable_before
    assert gateway._data == gateway_state_before
    assert adapter._actions == actions_before
    assert artifact_snapshot() == artifacts_before
    assert adapter._events == events_before
    assert adapter._event_scan_cursor == scan_before
    assert adapter._next_event_cursor == next_event_before
    assert adapter.command_calls == commands_before


@pytest.mark.parametrize("adapter_kind", ("memory", "paseo"))
@pytest.mark.parametrize(
    "malformed_absence",
    (
        "wrong_action",
        "false_authoritative",
        "missing_authoritative",
        "wrong_detail",
        "extra_field",
    ),
)
def test_repair_packet_6_malformed_authoritative_absence_is_typed_without_fairness_publication(
    tmp_path,
    adapter_kind,
    malformed_absence,
):
    action_id = "planning:packet6-absence"
    if adapter_kind == "memory":
        adapter = _InMemoryRuntimeProviderAdapter(
            ArtifactStore(tmp_path / "artifacts")
        )
        adapter._actions[action_id] = SimpleNamespace(
            wake_state_digest=None,
            wake_terminal_emitted=False,
        )
        durable_before = None
    else:
        _store, _source, _workspace, _client, adapter = _paseo_event_adapter(
            tmp_path, (action_id,)
        )
        durable_before = adapter._state_path.read_bytes()

    failure = _RuntimeFailure.absent(action_id)
    if malformed_absence == "wrong_action":
        failure = _RuntimeFailure.absent("planning:other")
    elif malformed_absence == "false_authoritative":
        failure = replace(failure, authoritative_absence=False)
    elif malformed_absence == "missing_authoritative":
        object.__delattr__(failure, "authoritative_absence")
    elif malformed_absence == "wrong_detail":
        failure = replace(
            failure, detail="authoritative stable-action absence "
        )
    else:
        object.__setattr__(failure, "extra", "not in the closed failure schema")
    read = _event_observation_read(
        adapter,
        action_id,
        _event_bound_observation(action_id),
    )
    adapter._reconcile_observation = (  # type: ignore[method-assign]
        lambda _stable_action_id: replace(read, result=failure)
    )
    actions_before = deepcopy(adapter._actions)
    events_before = deepcopy(adapter._events)
    scan_before = adapter._event_scan_cursor
    next_event_before = adapter._next_event_cursor

    rejected = adapter.events(None)

    assert type(rejected) is _RuntimeFailure
    assert rejected.code == "RUNTIME_PROVIDER_PROTOCOL_INVALID"
    assert adapter._actions == actions_before
    assert adapter._events == events_before
    assert adapter._event_scan_cursor == scan_before
    assert adapter._next_event_cursor == next_event_before
    if durable_before is not None:
        assert adapter._state_path.read_bytes() == durable_before


@pytest.mark.parametrize("adapter_kind", ("memory", "paseo"))
@pytest.mark.parametrize("failure_kind", ("authoritative_absence", "transport"))
def test_repair_packet_6_exact_non_observation_failure_preserves_bounded_fairness(
    tmp_path,
    adapter_kind,
    failure_kind,
):
    action_id = "planning:packet6-fairness"
    if adapter_kind == "memory":
        adapter = _InMemoryRuntimeProviderAdapter(
            ArtifactStore(tmp_path / "artifacts")
        )
        adapter._actions[action_id] = SimpleNamespace(
            wake_state_digest=None,
            wake_terminal_emitted=False,
        )
    else:
        _store, _source, _workspace, _client, adapter = _paseo_event_adapter(
            tmp_path, (action_id,)
        )
    failure = (
        _RuntimeFailure.absent(action_id)
        if failure_kind == "authoritative_absence"
        else _RuntimeFailure.transport()
    )

    def failed_read(selected_action_id):
        read = _event_observation_read(
            adapter,
            selected_action_id,
            _event_bound_observation(selected_action_id),
        )
        assert read.identity is not None
        assert read.token is not None
        return gateway_module._runtime_sealed_failure_read(
            selected_action_id,
            failure,
            identity=read.identity,
            selected_record_digest=read.token.selected_record_digest,
        )

    adapter._reconcile_observation = failed_read  # type: ignore[method-assign]
    actions_before = deepcopy(adapter._actions)
    events_before = deepcopy(adapter._events)
    scan_before = adapter._event_scan_cursor
    next_event_before = adapter._next_event_cursor

    page = adapter.events(None)

    assert not isinstance(page, _RuntimeFailure)
    assert page.events == ()
    assert adapter._actions == actions_before
    assert adapter._events == events_before
    assert adapter._event_scan_cursor == scan_before + 1
    assert adapter._next_event_cursor == next_event_before


def test_repair_packet_6_paseo_event_selection_cas_has_one_scanner_winner(
    tmp_path,
):
    action_id = "planning:packet6-scanner"
    store, source, workspace, client, seed = _paseo_event_adapter(
        tmp_path, (action_id,)
    )
    state_path = seed._state_path
    context = {"owner/repository": RuntimeRepositoryContext(source, "main")}
    scanners = [
        _PaseoRuntimeProviderAdapter(
            client=client,  # type: ignore[arg-type]
            artifacts=store,
            repository_contexts=context,
            state_path=state_path,
        )
        for _ in range(2)
    ]
    selected = threading.Barrier(2, timeout=10)
    results: list[object] = []
    errors: list[BaseException] = []

    def scan(adapter):
        try:
            def _reconcile_observation(selected_action_id):
                selected.wait()
                return _event_observation_read(
                    adapter,
                    selected_action_id,
                    _event_bound_observation(selected_action_id),
                )

            adapter._reconcile_observation = _reconcile_observation  # type: ignore[method-assign]
            results.append(adapter.events(None))
        except BaseException as error:
            errors.append(error)

    threads = [
        threading.Thread(target=scan, args=(adapter,), daemon=True)
        for adapter in scanners
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=15)

    assert not any(thread.is_alive() for thread in threads)
    assert errors == []
    assert len(results) == 2
    assert all(not isinstance(result, _RuntimeFailure) for result in results)
    durable = json.loads(state_path.read_text(encoding="utf-8"))
    assert durable["event_scan_cursor"] == 1
    assert durable["next_event_cursor"] == 2
    assert [
        (event["stable_action_id"], event["kind"])
        for event in durable["events"]
    ] == [(action_id, "state:running")]


@pytest.mark.parametrize(
    "concurrent_change",
    ("selected_record", "eligible_identity"),
)
def test_repair_packet_6_paseo_event_selection_cas_rejects_stale_readback(
    tmp_path,
    concurrent_change,
):
    selected_action_id = "a:packet6-selected"
    other_action_id = "b:packet6-other"
    store, source, workspace, client, scanner = _paseo_event_adapter(
        tmp_path, (selected_action_id, other_action_id)
    )
    state_path = scanner._state_path
    context = {"owner/repository": RuntimeRepositoryContext(source, "main")}
    contender = _PaseoRuntimeProviderAdapter(
        client=client,  # type: ignore[arg-type]
        artifacts=store,
        repository_contexts=context,
        state_path=state_path,
    )
    readback_ready = threading.Event()
    publish_allowed = threading.Event()

    def stale_read(action_id):
        assert action_id == selected_action_id
        read = _event_observation_read(
            scanner,
            action_id,
            _event_bound_observation(action_id),
        )
        readback_ready.set()
        assert publish_allowed.wait(timeout=10)
        return read

    scanner._reconcile_observation = stale_read  # type: ignore[method-assign]
    result: list[object] = []
    thread = threading.Thread(
        target=lambda: result.append(scanner.events(None)),
        daemon=True,
    )
    thread.start()
    assert readback_ready.wait(timeout=10)

    if concurrent_change == "selected_record":
        changed_state = gateway_module._runtime_event_observation_state(
            _event_observation_read(
                contender,
                selected_action_id,
                _event_bound_observation(selected_action_id, fenced=True),
            ).result,
            selected_action_id,
        )[0]
        contender._transact(
            lambda state: state["actions"][selected_action_id].update(
                {
                    "wake_state": changed_state,
                    "wake_state_digest": digest_value(changed_state),
                }
            )
        )
    else:
        terminal_state = gateway_module._runtime_event_observation_state(
            _event_observation_read(
                contender,
                other_action_id,
                _event_bound_observation(other_action_id, lifecycle="retired"),
            ).result,
            other_action_id,
        )[0]
        contender._transact(
            lambda state: state["actions"][other_action_id].update(
                {
                    "wake_state": terminal_state,
                    "wake_state_digest": digest_value(terminal_state),
                    "wake_terminal_emitted": True,
                }
            )
        )
    publish_allowed.set()
    thread.join(timeout=15)

    assert not thread.is_alive()
    assert len(result) == 1
    assert not isinstance(result[0], _RuntimeFailure)
    durable = json.loads(state_path.read_text(encoding="utf-8"))
    assert durable["event_scan_cursor"] == 0
    assert durable["next_event_cursor"] == 1
    assert durable["events"] == []
    assert durable["actions"][selected_action_id]["wake_state_digest"] == (
        digest_value(changed_state)
        if concurrent_change == "selected_record"
        else None
    )
    assert (
        durable["actions"][other_action_id]["wake_terminal_emitted"]
        is (concurrent_change == "eligible_identity")
    )
    restarted = _PaseoRuntimeProviderAdapter(
        client=client,  # type: ignore[arg-type]
        artifacts=store,
        repository_contexts=context,
        state_path=state_path,
    )
    restarted._reconcile_observation = (  # type: ignore[method-assign]
        lambda action_id: _event_observation_read(
            restarted,
            action_id,
            _event_bound_observation(
                action_id,
                fenced=False,
            ),
        )
    )

    retry = restarted.events(None)

    assert not isinstance(retry, _RuntimeFailure)
    retried = json.loads(state_path.read_text(encoding="utf-8"))
    assert retried["event_scan_cursor"] == 1
    assert retried["next_event_cursor"] == 2
    assert [
        event["stable_action_id"] for event in retried["events"]
    ] == [selected_action_id]
    assert retried["actions"][selected_action_id]["pending_fence"] is False


@pytest.mark.parametrize("adapter_kind", ("memory", "paseo"))
@pytest.mark.parametrize(
    "malformed_variant",
    (
        "extra_observation_field",
        "missing_observation_field",
        "tuple_subclass",
        "nested_permission_field",
        "cross_action",
    ),
)
def test_repair_packet_7_event_readback_requires_one_exact_closed_observation(
    tmp_path,
    adapter_kind,
    malformed_variant,
):
    selected_action_id = "planning:packet7-selected"
    if adapter_kind == "memory":
        adapter = _InMemoryRuntimeProviderAdapter(
            ArtifactStore(tmp_path / "artifacts")
        )
        adapter._actions[selected_action_id] = SimpleNamespace(
            wake_state_digest=None,
            wake_terminal_emitted=False,
        )
        durable_before = None
    else:
        _store, _source, _workspace, _client, adapter = _paseo_event_adapter(
            tmp_path, (selected_action_id,)
        )
        durable_before = adapter._state_path.read_bytes()

    observation = _event_bound_observation(selected_action_id)
    read = _event_observation_read(
        adapter,
        selected_action_id,
        observation,
    )
    if malformed_variant == "extra_observation_field":
        object.__setattr__(observation, "unexpected", "field")
    elif malformed_variant == "missing_observation_field":
        object.__delattr__(observation, "prompt_accepted")
    elif malformed_variant == "tuple_subclass":
        class PermissionTuple(tuple):
            pass

        object.__setattr__(
            observation,
            "permission_requests",
            PermissionTuple(),
        )
    elif malformed_variant == "nested_permission_field":
        request = _PermissionRequest(
            request_id="request:packet7",
            operation_id="write",
            resource_id="repository",
            binding_ref=observation.binding_ref,
            authority_subtree_digest=observation.authority_subtree_digest,
            stable_action_id=selected_action_id,
            subject_digest=observation.subject_digest,
        )
        object.__setattr__(request, "unexpected", "field")
        object.__setattr__(
            observation,
            "permission_requests",
            (request,),
        )
    else:
        object.__setattr__(
            observation,
            "stable_action_id",
            "planning:packet7-other",
        )
    adapter._reconcile_observation = (  # type: ignore[method-assign]
        lambda _stable_action_id: read
    )
    actions_before = deepcopy(adapter._actions)
    events_before = deepcopy(adapter._events)
    scan_before = adapter._event_scan_cursor
    next_event_before = adapter._next_event_cursor

    rejected = adapter.events(None)

    assert type(rejected) is _RuntimeFailure
    assert rejected.code == "RUNTIME_PROVIDER_PROTOCOL_INVALID"
    assert adapter._actions == actions_before
    assert adapter._events == events_before
    assert adapter._event_scan_cursor == scan_before
    assert adapter._next_event_cursor == next_event_before
    if durable_before is not None:
        assert adapter._state_path.read_bytes() == durable_before


@pytest.mark.parametrize(
    "identity_drift",
    ("workspace_id", "binding_ref", "action_map_key"),
)
def test_repair_packet_7_memory_readback_binds_requested_action_and_frozen_identity(
    tmp_path,
    identity_drift,
):
    store = ArtifactStore(tmp_path / "artifacts", maximum_bytes=300_000)
    first = _put_subject_artifacts(
        store,
        replace(_subject(), stable_action_id="planning:packet7-first"),
    )
    second = _put_subject_artifacts(
        store,
        replace(_subject(), stable_action_id="planning:packet7-second"),
    )
    adapter = _InMemoryRuntimeProviderAdapter(
        store,
        pending_permissions={
            first.stable_action_id: (
                ("request:first", "write", "repository:first"),
            ),
            second.stable_action_id: (
                ("request:second", "write", "repository:second"),
            ),
        },
    )
    for subject in (first, second):
        prompt = store.get(subject.planning_request_artifact_digest)
        spec = _RuntimeActionSpec(
            subject.stable_action_id,
            subject,
            _profile(),
            prompt,
            (prompt,),
        )
        assert type(adapter.prepare(spec)) is gateway_module._PrepareReceipt
        assert type(
            _adapter_command(
                adapter, subject.stable_action_id, RuntimeCommand.START
            )
        ) is _CommandReceipt
    action = adapter._actions[first.stable_action_id]
    if identity_drift == "workspace_id":
        action.workspace_id = "workspace:nonempty-drift"
    elif identity_drift == "binding_ref":
        action.binding_ref = "binding:nonempty-drift"
    else:
        adapter._actions[first.stable_action_id] = adapter._actions[
            second.stable_action_id
        ]

    actions_before = deepcopy(adapter._actions)
    events_before = deepcopy(adapter._events)
    artifacts_before = {
        path.relative_to(store._root).as_posix(): path.read_bytes()
        for path in store._root.rglob("*")
        if path.is_file()
    }
    scan_before = adapter._event_scan_cursor
    next_event_before = adapter._next_event_cursor
    commands_before = list(adapter.command_calls)

    rejected = adapter.observe(first.stable_action_id)

    assert type(rejected) is _RuntimeFailure
    assert rejected.code == "RUNTIME_OBSERVATION_INVALID"
    assert adapter._actions == actions_before
    assert adapter._events == events_before
    assert {
        path.relative_to(store._root).as_posix(): path.read_bytes()
        for path in store._root.rglob("*")
        if path.is_file()
    } == artifacts_before
    assert adapter._event_scan_cursor == scan_before
    assert adapter._next_event_cursor == next_event_before
    assert adapter.command_calls == commands_before


@pytest.mark.parametrize(
    ("artifact_variant", "expected_code"),
    (
        ("missing", "RUNTIME_ARTIFACT_MISSING"),
        ("corrupt", "RUNTIME_ARTIFACT_DIGEST_MISMATCH"),
        ("wrong_action", "RUNTIME_OUTPUT_ARTIFACT_INVALID"),
    ),
)
def test_repair_packet_7_memory_completed_readback_requires_exact_output_artifact_proof(
    tmp_path,
    artifact_variant,
    expected_code,
):
    store = ArtifactStore(tmp_path / "artifacts", maximum_bytes=300_000)
    subject = _put_subject_artifacts(store, _subject())
    prompt = store.get(subject.planning_request_artifact_digest)
    adapter = _InMemoryRuntimeProviderAdapter(store)
    spec = _RuntimeActionSpec(
        subject.stable_action_id,
        subject,
        _profile(),
        prompt,
        (prompt,),
    )
    assert type(adapter.prepare(spec)) is gateway_module._PrepareReceipt
    assert type(
        _adapter_command(
            adapter, subject.stable_action_id, RuntimeCommand.START
        )
    ) is _CommandReceipt
    action = adapter._actions[subject.stable_action_id]
    assert action.lifecycle == "completed"
    output_digest = action.output_artifact_digest
    assert output_digest is not None
    output_path = store.path_for(output_digest)
    if artifact_variant == "missing":
        output_path.unlink()
    elif artifact_variant == "corrupt":
        output_path.write_bytes(b'{"corrupt":true}')
    else:
        action.output_artifact_digest = store.put_canonical(
            {
                "schema_version": "gwo.runtime.output.v1",
                "subject_digest": subject.digest,
                "stable_action_id": "planning:packet7-other",
                "authority_digest": subject.authority_digest,
                "payload": {},
            }
        ).digest

    actions_before = deepcopy(adapter._actions)
    events_before = deepcopy(adapter._events)
    artifact_bytes_before = {
        path.relative_to(store._root).as_posix(): path.read_bytes()
        for path in store._root.rglob("*")
        if path.is_file()
    }
    scan_before = adapter._event_scan_cursor
    next_event_before = adapter._next_event_cursor
    commands_before = list(adapter.command_calls)

    rejected = adapter.observe(subject.stable_action_id)

    assert type(rejected) is _RuntimeFailure
    assert rejected.code == expected_code
    assert adapter._actions == actions_before
    assert adapter._events == events_before
    assert {
        path.relative_to(store._root).as_posix(): path.read_bytes()
        for path in store._root.rglob("*")
        if path.is_file()
    } == artifact_bytes_before
    assert adapter._event_scan_cursor == scan_before
    assert adapter._next_event_cursor == next_event_before
    assert adapter.command_calls == commands_before


@pytest.mark.parametrize(
    ("artifact_variant", "expected_code"),
    (
        ("extra_pending_field", "RUNTIME_OUTPUT_ARTIFACT_INVALID"),
        ("missing", "RUNTIME_ARTIFACT_MISSING"),
        ("corrupt", "RUNTIME_ARTIFACT_DIGEST_MISMATCH"),
        ("wrong_action", "RUNTIME_OUTPUT_ARTIFACT_INVALID"),
    ),
)
def test_repair_packet_7_paseo_completed_readback_requires_exact_output_artifact_proof(
    tmp_path,
    artifact_variant,
    expected_code,
):
    store, _source, _workspace, client, adapter, subject, _spec = (
        _prepared_paseo_adapter(tmp_path)
    )
    record = adapter._actions[subject.stable_action_id]
    result_path = Path(record["result_file"])
    result_path.parent.mkdir(parents=True, exist_ok=True)
    output = {
        "schema_version": "gwo.runtime.output.v1",
        "subject_digest": subject.digest,
        "stable_action_id": subject.stable_action_id,
        "authority_digest": subject.authority_digest,
        "payload": {"completed": True},
    }
    if artifact_variant == "extra_pending_field":
        output["unexpected"] = True
        result_path.write_bytes(gateway_module.canonical_bytes(output))
        client.agent.lifecycle = "idle"
    else:
        result_path.write_bytes(gateway_module.canonical_bytes(output))
        client.agent.lifecycle = "idle"
        completed = adapter.observe(subject.stable_action_id)
        assert type(completed) is gateway_module._BoundRuntimeObservation
        output_digest = completed.output_artifact_digest
        assert output_digest is not None
        if artifact_variant == "missing":
            store.path_for(output_digest).unlink()
        elif artifact_variant == "corrupt":
            store.path_for(output_digest).write_bytes(b'{"corrupt":true}')
        else:
            wrong = store.put_canonical(
                {
                    **output,
                    "stable_action_id": "planning:packet7-other",
                }
            )
            adapter._transact(
                lambda state: state["actions"][
                    subject.stable_action_id
                ].__setitem__("output_artifact_digest", wrong.digest)
            )

    actions_before = deepcopy(adapter._actions)
    events_before = deepcopy(adapter._events)
    durable_before = adapter._state_path.read_bytes()
    artifacts_before = {
        path.relative_to(store._root).as_posix(): path.read_bytes()
        for path in store._root.rglob("*")
        if path.is_file()
    }

    rejected = adapter.observe(subject.stable_action_id)

    assert type(rejected) is _RuntimeFailure
    assert rejected.code == expected_code
    assert adapter._actions == actions_before
    assert adapter._events == events_before
    assert adapter._state_path.read_bytes() == durable_before
    assert {
        path.relative_to(store._root).as_posix(): path.read_bytes()
        for path in store._root.rglob("*")
        if path.is_file()
    } == artifacts_before


def test_repair_packet_7_gateway_independently_proves_closed_completed_output(
    tmp_path,
):
    gateway, store, adapter = _gateway(tmp_path)
    subject = _put_subject_artifacts(store, _subject())
    preflight = gateway.planning_preflight(subject)
    completed = gateway.progress(subject, preflight)
    assert completed.status == "completed"
    native = adapter._reconcile_observation(subject.stable_action_id)
    assert type(native.result) is gateway_module._BoundRuntimeObservation
    malformed_output = store.put_canonical(
        {
            "schema_version": "gwo.runtime.output.v1",
            "subject_digest": subject.digest,
            "stable_action_id": subject.stable_action_id,
            "authority_digest": subject.authority_digest,
            "payload": {"completed": True},
            "unexpected": True,
        }
    )
    forged_observation = replace(
        native.result,
        planning_output_artifact_digest=malformed_output.digest,
    )
    assert native.identity is not None
    forged_evidence = replace(
        native.artifact_evidence,
        output=gateway_module._RuntimeOutputArtifactProof(
            artifact_digest=malformed_output.digest,
            byte_length=malformed_output.byte_length,
            schema_version="gwo.runtime.output.v1",
            subject_digest=subject.digest,
            stable_action_id=subject.stable_action_id,
            authority_digest=subject.authority_digest,
        ),
    )
    assert native.token is not None
    forged = replace(
        native,
        result=forged_observation,
        artifact_evidence=forged_evidence,
        token=replace(
            native.token,
            observation_digest=digest_value(
                gateway_module._json_projection(
                    asdict(forged_observation)
                )
            ),
            output_artifact_digest=malformed_output.digest,
        ),
    )
    adapter._reconcile_observation = (  # type: ignore[method-assign]
        lambda _stable_action_id: forged
    )
    journal_before = gateway._store_path.read_bytes()
    data_before = deepcopy(gateway._data)
    command_before = list(adapter.command_calls)

    with pytest.raises(RuntimeGatewayError) as rejected:
        gateway.progress(subject, preflight)

    assert rejected.value.code == "RUNTIME_OUTPUT_ARTIFACT_INVALID"
    assert gateway._store_path.read_bytes() == journal_before
    assert gateway._data == data_before
    assert adapter.command_calls == command_before


@pytest.mark.parametrize(
    "malformed_variant",
    (
        "receipt_subclass",
        "receipt_extra_field",
        "receipt_wrong_action",
        "failure_subclass",
        "failure_extra_field",
        "ack_missing_action",
        "unknown_failure_code",
    ),
)
def test_repair_packet_7_prepare_result_is_one_exact_closed_union(
    tmp_path,
    malformed_variant,
):
    gateway, store, adapter = _gateway(tmp_path)
    subject = _put_subject_artifacts(store, _subject())
    preflight = gateway.planning_preflight(subject)
    persisted = gateway._require_preflight(subject, preflight)
    record = gateway._assignment_for_progress(subject, persisted)
    prompt, inputs = gateway._resolve_input_artifacts(subject)
    spec = _RuntimeActionSpec(
        subject.stable_action_id,
        subject,
        gateway._profile(record["profile_digest"]),
        prompt,
        inputs,
    )
    if malformed_variant.startswith("receipt"):
        if malformed_variant == "receipt_subclass":
            class ReceiptSubclass(gateway_module._PrepareReceipt):
                pass

            value = ReceiptSubclass(
                subject.stable_action_id,
                "workspace:packet7",
            )
        else:
            value = gateway_module._PrepareReceipt(
                (
                    "planning:packet7-other"
                    if malformed_variant == "receipt_wrong_action"
                    else subject.stable_action_id
                ),
                "workspace:packet7",
            )
            if malformed_variant == "receipt_extra_field":
                object.__setattr__(value, "unexpected", True)
    else:
        if malformed_variant == "failure_subclass":
            class FailureSubclass(_RuntimeFailure):
                pass

            value = FailureSubclass(
                "RUNTIME_CONFIGURATION_INVALID",
                "closed failure",
            )
        elif malformed_variant == "unknown_failure_code":
            value = _RuntimeFailure(
                "RUNTIME_VENDOR_MYSTERY",
                "closed failure",
            )
        else:
            value = _RuntimeFailure(
                (
                    "RUNTIME_PREPARE_ACK_LOST"
                    if malformed_variant == "ack_missing_action"
                    else "RUNTIME_CONFIGURATION_INVALID"
                ),
                "closed failure",
            )
            if malformed_variant == "failure_extra_field":
                object.__setattr__(value, "unexpected", True)
    adapter.prepare = lambda _spec: value  # type: ignore[method-assign]
    journal_before = gateway._store_path.read_bytes()
    data_before = deepcopy(gateway._data)

    rejected = gateway._prepare(spec)

    assert type(rejected) is _RuntimeFailure
    assert rejected.code == "RUNTIME_PROVIDER_PROTOCOL_INVALID"
    assert gateway._store_path.read_bytes() == journal_before
    assert gateway._data == data_before
    assert adapter.command_calls == []


@pytest.mark.parametrize(
    "malformed_variant",
    (
        "receipt_subclass",
        "receipt_extra_field",
        "receipt_wrong_action",
        "failure_subclass",
        "failure_extra_field",
        "ack_missing_action",
        "unknown_failure_code",
    ),
)
def test_repair_packet_7_command_result_is_one_exact_closed_union(
    tmp_path,
    malformed_variant,
):
    store = ArtifactStore(tmp_path / "artifacts", maximum_bytes=300_000)
    subject = _put_subject_artifacts(store, _subject())
    profile = _profile()
    adapter = _InMemoryRuntimeProviderAdapter(
        store,
        pending_permissions={
            subject.stable_action_id: (
                ("request:packet7", "write", "repository"),
            ),
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
    running = gateway.progress(subject, preflight)
    assert running.status == "running"
    observation_verdict = gateway._observe_verdict(
        subject.stable_action_id
    )
    assert observation_verdict.kind == "bound"
    if malformed_variant.startswith("receipt"):
        if malformed_variant == "receipt_subclass":
            class ReceiptSubclass(_CommandReceipt):
                pass

            value = ReceiptSubclass(
                subject.stable_action_id,
                RuntimeCommand.FENCE,
            )
        else:
            value = _CommandReceipt(
                (
                    "planning:packet7-other"
                    if malformed_variant == "receipt_wrong_action"
                    else subject.stable_action_id
                ),
                RuntimeCommand.FENCE,
            )
            if malformed_variant == "receipt_extra_field":
                object.__setattr__(value, "unexpected", True)
    else:
        if malformed_variant == "failure_subclass":
            class FailureSubclass(_RuntimeFailure):
                pass

            value = FailureSubclass(
                "RUNTIME_COMMAND_INVALID",
                "closed failure",
            )
        elif malformed_variant == "unknown_failure_code":
            value = _RuntimeFailure(
                "RUNTIME_VENDOR_MYSTERY",
                "closed failure",
            )
        else:
            value = _RuntimeFailure(
                (
                    "RUNTIME_COMMAND_ACK_LOST"
                    if malformed_variant == "ack_missing_action"
                    else "RUNTIME_COMMAND_INVALID"
                ),
                "closed failure",
            )
            if malformed_variant == "failure_extra_field":
                object.__setattr__(value, "unexpected", True)
    adapter.command = (  # type: ignore[method-assign]
        lambda _stable_action_id, _command, **_kwargs: value
    )
    journal_before = gateway._store_path.read_bytes()
    data_before = deepcopy(gateway._data)

    with pytest.raises(RuntimeGatewayError) as rejected:
        gateway._command_with_readback(
            subject.stable_action_id,
            RuntimeCommand.FENCE,
        )

    assert rejected.value.code == "RUNTIME_PROVIDER_PROTOCOL_INVALID"
    assert gateway._store_path.read_bytes() == journal_before
    assert gateway._data == data_before


def test_repair_packet_7_gateway_stale_read_token_stops_command_before_effect(
    tmp_path,
):
    store = ArtifactStore(tmp_path / "artifacts", maximum_bytes=300_000)
    subject = _put_subject_artifacts(store, _subject())
    profile = _profile()
    adapter = _InMemoryRuntimeProviderAdapter(
        store,
        pending_permissions={
            subject.stable_action_id: (
                ("request:packet7", "write", "repository"),
            ),
        },
    )
    configuration = RuntimeConfiguration(
        profiles={profile.digest: profile},
        host_mappings={"coordinator": ProfileMapping(profile.digest)},
    )
    journal = tmp_path / "gateway.journal"
    first = RuntimeGateway(
        store_path=journal,
        _adapter=adapter,
        configuration=configuration,
        _artifacts=store,
    )
    preflight = first.planning_preflight(subject)
    assert first.progress(subject, preflight).status == "running"
    contender = RuntimeGateway(
        store_path=journal,
        _adapter=adapter,
        configuration=configuration,
        _artifacts=store,
    )
    fence_dispatched = threading.Event()
    retire_recorded = threading.Event()
    native_command = adapter.command

    def interleaved_command(
        stable_action_id,
        command,
    ):
        if command is RuntimeCommand.FENCE:
            fence_dispatched.set()
            assert retire_recorded.wait(timeout=10)
        return native_command(
            stable_action_id,
            command,
        )

    adapter.command = interleaved_command  # type: ignore[method-assign]
    failures: list[RuntimeGatewayError] = []

    def stale_fence():
        try:
            first.transition(
                subject.stable_action_id,
                RuntimeCommand.FENCE,
            )
        except RuntimeGatewayError as error:
            failures.append(error)

    thread = threading.Thread(target=stale_fence, daemon=True)
    thread.start()
    assert fence_dispatched.wait(timeout=10)
    retired = contender.transition(
        subject.stable_action_id,
        RuntimeCommand.RETIRE,
    )
    assert retired.status == "retired"
    journal_after_retire = journal.read_bytes()
    retire_recorded.set()
    thread.join(timeout=15)

    assert not thread.is_alive()
    assert [failure.code for failure in failures] == [
        "RUNTIME_ACTION_STATE_CHANGED"
    ]
    assert journal.read_bytes() == journal_after_retire
    assert adapter._actions[subject.stable_action_id].fenced is False
    assert adapter._actions[subject.stable_action_id].lifecycle == "retired"
    assert [
        command
        for _stable_action_id, command in adapter.command_calls
        if command in {"fence", "retire"}
    ] == ["retire"]


@pytest.mark.parametrize("adapter_kind", ("memory", "paseo"))
def test_repair_packet_7_same_adapter_event_scanners_have_one_causal_winner(
    tmp_path,
    adapter_kind,
):
    selected_action_id = "planning:packet7-same-scanner"
    if adapter_kind == "memory":
        adapter = _InMemoryRuntimeProviderAdapter(
            ArtifactStore(tmp_path / "artifacts")
        )
        adapter._actions[selected_action_id] = SimpleNamespace(
            wake_state_digest=None,
            wake_terminal_emitted=False,
        )
    else:
        _store, _source, _workspace, _client, adapter = _paseo_event_adapter(
            tmp_path, (selected_action_id,)
        )
    observation = _event_bound_observation(selected_action_id)
    read = _event_observation_read(
        adapter,
        selected_action_id,
        observation,
    )
    selected = threading.Barrier(2, timeout=10)
    results: list[object] = []
    errors: list[BaseException] = []

    def blocked_read(_stable_action_id):
        selected.wait()
        return read

    adapter._reconcile_observation = blocked_read  # type: ignore[method-assign]

    def scan():
        try:
            results.append(adapter.events(None))
        except BaseException as error:
            errors.append(error)

    threads = [
        threading.Thread(target=scan, daemon=True)
        for _ in range(2)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=15)

    assert not any(thread.is_alive() for thread in threads)
    assert errors == []
    assert len(results) == 2
    assert adapter._event_scan_cursor == 1
    assert adapter._next_event_cursor == 2
    assert [
        (event.stable_action_id, event.kind) for event in adapter._events
    ] == [(selected_action_id, "state:running")]


@pytest.mark.parametrize(
    "malformed_variant",
    (
        "page_subclass",
        "page_extra_field",
        "events_tuple_subclass",
        "nested_event_extra_field",
        "failure_subclass",
        "failure_extra_field",
        "unknown_failure_code",
    ),
)
def test_repair_packet_7_gateway_event_boundary_is_one_exact_closed_union(
    tmp_path,
    malformed_variant,
):
    gateway, store, adapter = _gateway(tmp_path)
    subject = _put_subject_artifacts(store, _subject())
    preflight = gateway.planning_preflight(subject)
    gateway.progress(subject, preflight)
    event = gateway_module._RuntimeEvent(
        "1",
        subject.stable_action_id,
        "state:completed",
    )
    if malformed_variant.startswith("page"):
        if malformed_variant == "page_subclass":
            class PageSubclass(gateway_module._RuntimeEventPage):
                pass

            value = PageSubclass((event,), "1")
        else:
            value = gateway_module._RuntimeEventPage((event,), "1")
            object.__setattr__(value, "unexpected", True)
    elif malformed_variant == "events_tuple_subclass":
        class EventTuple(tuple):
            pass

        value = gateway_module._RuntimeEventPage(
            EventTuple((event,)),
            "1",
        )
    elif malformed_variant == "nested_event_extra_field":
        object.__setattr__(event, "unexpected", True)
        value = gateway_module._RuntimeEventPage((event,), "1")
    else:
        if malformed_variant == "failure_subclass":
            class FailureSubclass(_RuntimeFailure):
                pass

            value = FailureSubclass(
                "RUNTIME_TRANSPORT_UNAVAILABLE",
                "closed failure",
            )
        elif malformed_variant == "unknown_failure_code":
            value = _RuntimeFailure(
                "RUNTIME_VENDOR_MYSTERY",
                "closed failure",
            )
        else:
            value = _RuntimeFailure.transport()
            object.__setattr__(value, "unexpected", True)
    adapter.events = lambda _cursor: value  # type: ignore[method-assign]
    journal_before = gateway._store_path.read_bytes()
    data_before = deepcopy(gateway._data)

    with pytest.raises(RuntimeGatewayError) as rejected:
        gateway.progress(subject, preflight)

    assert rejected.value.code == "RUNTIME_PROVIDER_PROTOCOL_INVALID"
    assert gateway._store_path.read_bytes() == journal_before
    assert gateway._data == data_before


@pytest.mark.parametrize("adapter_kind", ("memory", "paseo"))
def test_repair_packet_8_adapter_command_requires_a_fresh_observation_gate_before_mutation(
    tmp_path,
    adapter_kind,
):
    store = ArtifactStore(tmp_path / "artifacts", maximum_bytes=300_000)
    subject = _put_subject_artifacts(store, _subject())
    prompt = store.get(subject.planning_request_artifact_digest)
    spec = _RuntimeActionSpec(
        subject.stable_action_id,
        subject,
        _profile(),
        prompt,
        (prompt,),
    )
    client = None
    if adapter_kind == "memory":
        adapter = _InMemoryRuntimeProviderAdapter(store)
    else:
        source, workspace = _repository_worktree(tmp_path)
        client = _RecordingPaseoCli(workspace)
        adapter = _PaseoRuntimeProviderAdapter(
            client=client,  # type: ignore[arg-type]
            artifacts=store,
            repository_contexts={
                "owner/repository": RuntimeRepositoryContext(source, "main")
            },
            state_path=tmp_path / "paseo-actions.json",
        )
    assert type(adapter.prepare(spec)) is gateway_module._PrepareReceipt
    actions_before = deepcopy(adapter._actions)
    commands_before = (
        list(adapter.command_calls)
        if adapter_kind == "memory"
        else deepcopy(client.commands)
    )
    durable_before = (
        None
        if adapter_kind == "memory"
        else adapter._state_path.read_bytes()
    )

    rejected = adapter.command(subject.stable_action_id, RuntimeCommand.START)

    assert type(rejected) is _RuntimeFailure
    assert rejected.code == "RUNTIME_ACTION_STATE_CHANGED"

    assert adapter._actions == actions_before
    if adapter_kind == "memory":
        assert adapter.command_calls == commands_before
        assert adapter.created_agent_count == 0
    else:
        assert client.commands == commands_before
        assert adapter._state_path.read_bytes() == durable_before


@pytest.mark.parametrize("adapter_kind", ("memory", "paseo"))
def test_repair_packet_16_private_command_gate_is_one_shot_and_event_reads_do_not_open_it(
    tmp_path,
    adapter_kind,
):
    store = ArtifactStore(tmp_path / "artifacts", maximum_bytes=300_000)
    subject = _put_subject_artifacts(store, _subject())
    prompt = store.get(subject.planning_request_artifact_digest)
    spec = _RuntimeActionSpec(
        subject.stable_action_id,
        subject,
        _profile(),
        prompt,
        (prompt,),
    )
    client = None
    if adapter_kind == "memory":
        adapter = _InMemoryRuntimeProviderAdapter(store)
    else:
        source, workspace = _repository_worktree(tmp_path)
        client = _RecordingPaseoCli(workspace)
        adapter = _PaseoRuntimeProviderAdapter(
            client=client,  # type: ignore[arg-type]
            artifacts=store,
            repository_contexts={
                "owner/repository": RuntimeRepositoryContext(source, "main")
            },
            state_path=tmp_path / "paseo-actions.json",
        )
    assert type(adapter.prepare(spec)) is gateway_module._PrepareReceipt

    no_observe = adapter.command(subject.stable_action_id, RuntimeCommand.START)
    assert type(no_observe) is _RuntimeFailure
    assert no_observe.code == "RUNTIME_ACTION_STATE_CHANGED"

    event_only = adapter.events(None)
    assert not isinstance(event_only, _RuntimeFailure)
    no_gate_from_event = adapter.command(
        subject.stable_action_id, RuntimeCommand.START
    )
    assert type(no_gate_from_event) is _RuntimeFailure
    assert no_gate_from_event.code == "RUNTIME_ACTION_STATE_CHANGED"

    observed = adapter.observe(subject.stable_action_id)
    assert type(observed) is gateway_module._PreparedRuntimeObservation
    started = adapter.command(subject.stable_action_id, RuntimeCommand.START)
    assert type(started) is _CommandReceipt
    consumed = adapter.command(subject.stable_action_id, RuntimeCommand.FENCE)
    assert type(consumed) is _RuntimeFailure
    assert consumed.code == "RUNTIME_ACTION_STATE_CHANGED"
    if adapter_kind == "memory":
        assert adapter.created_agent_count == 1
    else:
        assert client is not None
        assert len([args for args in client.commands if args[0] == "run"]) == 1


def test_repair_packet_16_restart_drops_an_unconsumed_private_command_gate(tmp_path):
    store = ArtifactStore(tmp_path / "artifacts", maximum_bytes=300_000)
    source, workspace = _repository_worktree(tmp_path)
    client = _RecordingPaseoCli(workspace)
    state_path = tmp_path / "paseo-actions.json"
    adapter = _PaseoRuntimeProviderAdapter(
        client=client,  # type: ignore[arg-type]
        artifacts=store,
        repository_contexts={
            "owner/repository": RuntimeRepositoryContext(source, "main")
        },
        state_path=state_path,
    )
    subject = _put_subject_artifacts(store, _subject())
    prompt = store.get(subject.planning_request_artifact_digest)
    assert type(
        adapter.prepare(
            _RuntimeActionSpec(
                subject.stable_action_id,
                subject,
                _profile(),
                prompt,
                (prompt,),
            )
        )
    ) is gateway_module._PrepareReceipt
    assert type(adapter.observe(subject.stable_action_id)) is gateway_module._PreparedRuntimeObservation

    restarted = _PaseoRuntimeProviderAdapter(
        client=client,  # type: ignore[arg-type]
        artifacts=store,
        repository_contexts={
            "owner/repository": RuntimeRepositoryContext(source, "main")
        },
        state_path=state_path,
    )
    rejected = restarted.command(subject.stable_action_id, RuntimeCommand.START)

    assert type(rejected) is _RuntimeFailure
    assert rejected.code == "RUNTIME_ACTION_STATE_CHANGED"
    assert not any(args and args[0] == "run" for args in client.commands)


@pytest.mark.parametrize(
    "command",
    (RuntimeCommand.START, RuntimeCommand.RESUME),
)
def test_repair_packet_8_start_and_resume_use_one_sealed_precommand_read(
    tmp_path,
    command,
):
    gateway, store, adapter = _gateway(tmp_path)
    subject = _put_subject_artifacts(store, _subject())
    adapter._pending_permissions[subject.stable_action_id] = [
        ("request:active", "write", "repository")
    ]
    preflight = gateway.planning_preflight(subject)
    if command is RuntimeCommand.START:
        record = gateway._assignment_for_progress(
            subject,
            gateway._require_preflight(subject, preflight),
        )
        prompt, inputs = gateway._resolve_input_artifacts(subject)
        assert type(
            adapter.prepare(
                _RuntimeActionSpec(
                    stable_action_id=subject.stable_action_id,
                    subject=subject,
                    profile=gateway._profile(record["profile_digest"]),
                    prompt_artifact=prompt,
                    input_artifacts=inputs,
                )
            )
        ) is gateway_module._PrepareReceipt
    else:
        gateway.progress(subject, preflight)
        gateway.transition(subject.stable_action_id, RuntimeCommand.PARK)

    reads = []
    command_entries = []
    native_read = adapter._reconcile_observation
    native_command = adapter.command

    def tracked_read(stable_action_id):
        read = native_read(stable_action_id)
        reads.append(read)
        return read

    def tracked_command(
        stable_action_id,
        selected_command,
    ):
        command_entries.append(
            (len(reads), tuple(reads))
        )
        return native_command(
            stable_action_id,
            selected_command,
        )

    adapter._reconcile_observation = tracked_read  # type: ignore[method-assign]
    adapter.command = tracked_command  # type: ignore[method-assign]

    progressed = gateway.transition(subject.stable_action_id, command)

    assert progressed.command is command
    assert len(command_entries) == 1
    read_count_at_command, prior_reads = command_entries[0]
    assert read_count_at_command == 1
    assert len(prior_reads) == 1


def test_repair_packet_8_paseo_effect_claim_rechecks_the_sealed_token_in_its_cas(
    tmp_path,
):
    store = ArtifactStore(tmp_path / "artifacts", maximum_bytes=300_000)
    subject = _put_subject_artifacts(store, _subject())
    source, workspace = _repository_worktree(tmp_path)
    client = _RecordingPaseoCli(workspace)
    adapter = _PaseoRuntimeProviderAdapter(
        client=client,  # type: ignore[arg-type]
        artifacts=store,
        repository_contexts={
            "owner/repository": RuntimeRepositoryContext(source, "main")
        },
        state_path=tmp_path / "paseo-actions.json",
    )
    prompt = store.get(subject.planning_request_artifact_digest)
    assert type(
        adapter.prepare(
            _RuntimeActionSpec(
                subject.stable_action_id,
                subject,
                _profile(),
                prompt,
                (prompt,),
            )
        )
    ) is gateway_module._PrepareReceipt
    read = adapter._reconcile_observation(subject.stable_action_id)
    verdict = gateway_module._ObservationProtocol.validate(
        read,
        selected_stable_action_id=subject.stable_action_id,
    )
    assert verdict.kind == "prepared"
    assert type(verdict.token) is gateway_module._RuntimeObservationReadToken
    expected_token = verdict.token

    native_claim = adapter._claim_record_update
    claim_entered = threading.Event()
    release_claim = threading.Event()
    received_tokens = []

    def blocked_claim(record, *, already_claimed, update, **kwargs):
        received_tokens.append(kwargs.get("precondition"))
        claim_entered.set()
        assert release_claim.wait(5)
        return native_claim(
            record,
            already_claimed=already_claimed,
            update=update,
            **kwargs,
        )

    adapter._claim_record_update = blocked_claim  # type: ignore[method-assign]
    observed = adapter.observe(subject.stable_action_id)
    assert type(observed) is gateway_module._PreparedRuntimeObservation
    result = []

    def issue_start():
        observed = adapter.observe(subject.stable_action_id)
        assert type(observed) is gateway_module._PreparedRuntimeObservation
        result.append(
            adapter.command(
                subject.stable_action_id,
                RuntimeCommand.START,
            )
        )

    worker = threading.Thread(target=issue_start)
    worker.start()
    assert claim_entered.wait(5)

    def win_effect_claim(state):
        state["actions"][subject.stable_action_id]["pending_start"] = True

    adapter._transact(win_effect_claim)
    release_claim.set()
    worker.join(5)
    assert not worker.is_alive()

    assert received_tokens == [expected_token]
    assert len(result) == 1
    assert type(result[0]) is _RuntimeFailure
    assert result[0].code == "RUNTIME_ACTION_STATE_CHANGED"
    assert not any(command and command[0] == "run" for command in client.commands)


def test_repair_packet_8_identityless_failure_cannot_name_another_selected_action():
    read = gateway_module._runtime_sealed_failure_read(
        "action:selected",
        _RuntimeFailure(
            "RUNTIME_TRANSPORT_UNAVAILABLE",
            "closed transport failure",
            stable_action_id="action:other",
        ),
    )

    verdict = gateway_module._ObservationProtocol.validate(
        read,
        selected_stable_action_id="action:selected",
    )

    assert verdict.kind == "invalid"
    assert verdict.failure.code == "RUNTIME_PROVIDER_PROTOCOL_INVALID"


@pytest.mark.parametrize(
    "code",
    (
        "RUNTIME_ACTION_ABSENT",
        "RUNTIME_BINDING_MISSING",
        "RUNTIME_MATERIALIZATION_PENDING",
        "RUNTIME_PREPARE_ACK_LOST",
        "RUNTIME_COMMAND_ACK_LOST",
        "RUNTIME_EFFECT_AMBIGUOUS",
    ),
)
def test_repair_packet_8_action_bound_failure_codes_require_a_stable_action_id(
    code,
):
    failure = _RuntimeFailure(
        code,
        (
            "authoritative stable-action absence"
            if code == "RUNTIME_ACTION_ABSENT"
            else "closed action-bound failure"
        ),
        authoritative_absence=(code == "RUNTIME_ACTION_ABSENT"),
    )

    assert not gateway_module._runtime_failure_is_structurally_valid(failure)


@pytest.mark.parametrize(
    ("failure", "expected_code"),
    (
        (
            _RuntimeFailure(
                "RUNTIME_CONFIGURATION_INVALID",
                "permanent configuration failure",
            ),
            "RUNTIME_CONFIGURATION_INVALID",
        ),
        (
            _RuntimeFailure(
                "RUNTIME_PROVIDER_PROTOCOL_INVALID",
                "closed protocol failure",
            ),
            "RUNTIME_PROVIDER_PROTOCOL_INVALID",
        ),
        (
            _RuntimeFailure(
                "RUNTIME_VENDOR_MYSTERY",
                "unknown provider failure",
            ),
            "RUNTIME_PROVIDER_PROTOCOL_INVALID",
        ),
        (
            _RuntimeFailure.transport(),
            "RUNTIME_TRANSPORT_UNAVAILABLE",
        ),
    ),
)
def test_repair_packet_8_prepare_readback_never_swallows_nonrecoverable_failure(
    tmp_path,
    failure,
    expected_code,
):
    gateway, store, adapter = _gateway(tmp_path)
    subject = _put_subject_artifacts(store, _subject())
    preflight = gateway.planning_preflight(subject)
    native_prepare = adapter.prepare

    def stage_then_fail(spec):
        assert type(native_prepare(spec)) is gateway_module._PrepareReceipt
        return failure

    adapter.prepare = stage_then_fail  # type: ignore[method-assign]

    with pytest.raises(RuntimeGatewayError) as stopped:
        gateway.progress(subject, preflight)

    assert stopped.value.code == expected_code
    assert adapter.command_calls == []
    assert adapter.created_agent_count == 0
    assert isinstance(
        adapter._reconcile_observation(subject.stable_action_id).result,
        gateway_module._PreparedRuntimeObservation,
    )


@pytest.mark.parametrize(
    "failure_code",
    ("RUNTIME_PREPARE_ACK_LOST", "RUNTIME_EFFECT_AMBIGUOUS"),
)
def test_repair_packet_8_prepare_readback_recovers_only_closed_ambiguous_taxonomy(
    tmp_path,
    failure_code,
):
    gateway, store, adapter = _gateway(tmp_path)
    subject = _put_subject_artifacts(store, _subject())
    adapter._pending_permissions[subject.stable_action_id] = [
        ("request:active", "write", "repository")
    ]
    preflight = gateway.planning_preflight(subject)
    native_prepare = adapter.prepare

    def stage_then_lose_ack(spec):
        assert type(native_prepare(spec)) is gateway_module._PrepareReceipt
        return _RuntimeFailure(
            failure_code,
            "closed ambiguous prepare failure",
            stable_action_id=spec.stable_action_id,
        )

    adapter.prepare = stage_then_lose_ack  # type: ignore[method-assign]

    progressed = gateway.progress(subject, preflight)

    assert progressed.status == "running"
    assert adapter.command_calls == [
        (subject.stable_action_id, RuntimeCommand.START.value)
    ]


def test_repair_packet_8_retained_permission_name_binds_the_normalized_operation(
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
    request_id = "receipt8-full-provider-request"
    client.permissions = [_paseo_permission(request_id)]
    command = PermissionResponse(request_id, "allow")
    assert type(
        _adapter_command(adapter, subject.stable_action_id, command)
    ) is _CommandReceipt
    valid_read = adapter._reconcile_observation(subject.stable_action_id)
    valid_verdict = gateway_module._ObservationProtocol.validate(
        valid_read,
        selected_stable_action_id=subject.stable_action_id,
    )
    assert valid_verdict.kind == "bound"
    observation = valid_verdict.observation
    assert isinstance(observation, gateway_module._BoundRuntimeObservation)
    completed = observation.completed_permission_response
    assert completed is not None
    assert completed.provider_receipt["name"] == completed.request.operation_id

    tampered_receipt = dict(completed.provider_receipt)
    tampered_receipt["name"] = "paseo/0.2.3:operation:tampered"
    tampered_completed = replace(
        completed,
        provider_receipt=tampered_receipt,
        provider_receipt_digest=digest_value(tampered_receipt),
    )
    tampered_observation = replace(
        observation,
        completed_permission_response=tampered_completed,
    )
    assert valid_read.token is not None
    tampered_read = replace(
        valid_read,
        result=tampered_observation,
        token=replace(
            valid_read.token,
            observation_digest=digest_value(
                gateway_module._json_projection(
                    asdict(tampered_observation)
                )
            ),
        ),
    )
    tampered_verdict = gateway_module._ObservationProtocol.validate(
        tampered_read,
        selected_stable_action_id=subject.stable_action_id,
    )
    assert tampered_verdict.kind == "invalid"

    durable = json.loads(adapter._state_path.read_text(encoding="utf-8"))
    durable_completed = durable["actions"][subject.stable_action_id][
        "completed_permission_response"
    ]
    durable_completed["provider_receipt"]["name"] = (
        "paseo/0.2.3:operation:tampered"
    )
    durable_completed["provider_receipt_digest"] = digest_value(
        durable_completed["provider_receipt"]
    )
    adapter._state_path.write_bytes(
        gateway_module.canonical_bytes(durable)
    )
    tampered_bytes = adapter._state_path.read_bytes()
    mutations_before = _mutating_paseo_commands(client.commands)

    with pytest.raises(RuntimeGatewayError) as rejected:
        _PaseoRuntimeProviderAdapter(
            client=client,  # type: ignore[arg-type]
            artifacts=store,
            repository_contexts={
                "owner/repository": RuntimeRepositoryContext(source, "main")
            },
            state_path=adapter._state_path,
        )

    assert rejected.value.code == "RUNTIME_STORE_INVALID"
    assert _mutating_paseo_commands(client.commands) == mutations_before
    assert adapter._state_path.read_bytes() == tampered_bytes


@pytest.mark.parametrize(
    "value",
    (
        "\ud800",
        "\udfff",
        {"\ud800": "object key"},
        {"value": "\udfff"},
        {"nested": ["valid", {"value": "\ud800"}]},
    ),
)
def test_repair_packet_8_canonical_json_rejects_lone_surrogates_at_serialize_ingress(
    tmp_path,
    value,
):
    with pytest.raises(canonical_module.CanonicalJsonError):
        canonical_module.canonical_bytes(value)

    with pytest.raises(RuntimeGatewayError) as artifact:
        ArtifactStore(tmp_path / "artifacts").put_canonical(value)

    assert artifact.value.code == "RUNTIME_ARTIFACT_INVALID"


@pytest.mark.parametrize(
    "payload",
    (
        b'{"value":"\\ud800"}',
        b'{"\\udfff":"object key"}',
        b'{"nested":["valid",{"value":"\\ud800"}]}',
    ),
)
def test_repair_packet_8_canonical_json_rejects_lone_surrogates_at_load_ingress(
    tmp_path,
    payload,
):
    with pytest.raises(canonical_module.CanonicalJsonError):
        canonical_module.strict_json_loads(payload)
    with pytest.raises(canonical_module.CanonicalJsonError):
        canonical_module.load_canonical_json(payload)
    with pytest.raises(RuntimeGatewayError) as artifact:
        ArtifactStore._canonical_json(payload)
    assert artifact.value.code == "RUNTIME_ARTIFACT_INVALID"

    journal = gateway_module._V3JsonJournal(tmp_path / "runtime.journal")
    journal.path.write_bytes(payload)
    with journal.exclusive():
        with pytest.raises(RuntimeGatewayError) as stored:
            journal.read_unlocked()
    assert stored.value.code == "RUNTIME_STORE_INVALID"


@pytest.mark.parametrize(
    "profile_values",
    (
        {"name": "\ud800"},
        {"provider": "\udfff"},
        {"features": {"\ud800": "key"}},
        {"features": {"nested": ["ok", "\udfff"]}},
    ),
)
def test_repair_packet_8_runtime_profile_rejects_lone_surrogates_as_typed_input(
    profile_values,
):
    values = {
        "name": "unicode",
        "provider": "test",
        "model": "test-model",
        "thinking": "high",
        "mode": "safe",
        "features": {},
    }
    values.update(profile_values)

    with pytest.raises(TypeError):
        RuntimeProfile(**values)


def test_repair_packet_8_canonical_json_accepts_unicode_scalars_and_escaped_pairs():
    emoji = "\U0001f600"
    value = {f"key:{emoji}": {"value": emoji}}

    payload = canonical_module.canonical_bytes(value)

    assert canonical_module.strict_json_loads(payload) == value
    assert canonical_module.load_canonical_json(payload) == value
    assert canonical_module.strict_json_loads(
        b'{"value":"\\ud83d\\ude00"}'
    ) == {"value": emoji}
    profile = RuntimeProfile(
        name=f"profile:{emoji}",
        provider="test",
        model="test-model",
        thinking="high",
        mode="safe",
        features=value,
    )
    assert profile.name == f"profile:{emoji}"
    assert profile.features.to_json() == value


class _Packet9StringSubclass(str):
    pass


class _Packet9TupleSubclass(tuple):
    pass


class _Packet9EvilScalar:
    def __eq__(self, _other):
        raise RuntimeError("provider scalar equality must not execute")

    def __hash__(self):
        raise RuntimeError("provider scalar hash must not execute")


class _Packet9PreparedObservationSubclass(
    gateway_module._PreparedRuntimeObservation
):
    pass


def _packet9_mutate(value, *, field=None, replacement=None, missing=None, extra=False):
    if field is not None:
        object.__setattr__(value, field, replacement)
    if missing is not None:
        object.__delattr__(value, missing)
    if extra:
        object.__setattr__(value, "provider_extra", "untrusted")
    return value


def test_repair_packet_9_closed_result_scalars_are_exact_and_total(tmp_path):
    stable_action_id = "planning:packet-9-scalars"
    spec = SimpleNamespace(stable_action_id=stable_action_id)
    invalid_prepare_results = (
        gateway_module._PrepareReceipt(
            _Packet9StringSubclass(stable_action_id),
            "workspace:packet-9",
        ),
        gateway_module._PrepareReceipt(
            _Packet9EvilScalar(),  # type: ignore[arg-type]
            "workspace:packet-9",
        ),
        gateway_module._PrepareReceipt(
            stable_action_id,
            _Packet9StringSubclass("workspace:packet-9"),
        ),
        _packet9_mutate(
            gateway_module._PrepareReceipt(
                stable_action_id,
                "workspace:packet-9",
            ),
            missing="stable_action_id",
        ),
        _packet9_mutate(
            gateway_module._PrepareReceipt(
                stable_action_id,
                "workspace:packet-9",
            ),
            extra=True,
        ),
    )
    invalid_command_results = (
        gateway_module._CommandReceipt(
            _Packet9StringSubclass(stable_action_id),
            RuntimeCommand.FENCE,
        ),
        gateway_module._CommandReceipt(
            _Packet9EvilScalar(),  # type: ignore[arg-type]
            RuntimeCommand.FENCE,
        ),
        _packet9_mutate(
            gateway_module._CommandReceipt(
                stable_action_id,
                RuntimeCommand.FENCE,
            ),
            missing="command",
        ),
        _packet9_mutate(
            gateway_module._CommandReceipt(
                stable_action_id,
                RuntimeCommand.FENCE,
            ),
            extra=True,
        ),
    )
    invalid_failures = (
        _RuntimeFailure(
            _Packet9StringSubclass("RUNTIME_TRANSPORT_UNAVAILABLE"),
            "transport failed",
        ),
        _RuntimeFailure(
            _Packet9EvilScalar(),  # type: ignore[arg-type]
            "transport failed",
        ),
        _RuntimeFailure(
            "RUNTIME_TRANSPORT_UNAVAILABLE",
            _Packet9StringSubclass("transport failed"),
        ),
        _RuntimeFailure(
            "RUNTIME_PREPARE_ACK_LOST",
            "prepare failed",
            _Packet9StringSubclass(stable_action_id),
        ),
        _packet9_mutate(
            _RuntimeFailure(
                "RUNTIME_TRANSPORT_UNAVAILABLE",
                "transport failed",
            ),
            field="authoritative_absence",
            replacement=1,
        ),
        _packet9_mutate(
            _RuntimeFailure(
                "RUNTIME_TRANSPORT_UNAVAILABLE",
                "transport failed",
            ),
            missing="detail",
        ),
        _packet9_mutate(
            _RuntimeFailure(
                "RUNTIME_TRANSPORT_UNAVAILABLE",
                "transport failed",
            ),
            extra=True,
        ),
    )
    invalid_event_pages = (
        gateway_module._RuntimeEventPage(
            (
                gateway_module._RuntimeEvent(
                    "1",
                    stable_action_id,
                    _Packet9StringSubclass("state:running"),
                ),
            ),
            "1",
        ),
        gateway_module._RuntimeEventPage(
            (
                gateway_module._RuntimeEvent(
                    "1",
                    stable_action_id,
                    _Packet9EvilScalar(),  # type: ignore[arg-type]
                ),
            ),
            "1",
        ),
        gateway_module._RuntimeEventPage(
            (
                gateway_module._RuntimeEvent(
                    _Packet9StringSubclass("1"),
                    stable_action_id,
                    "state:running",
                ),
            ),
            "1",
        ),
        gateway_module._RuntimeEventPage(
            (
                gateway_module._RuntimeEvent(
                    "1",
                    _Packet9StringSubclass(stable_action_id),
                    "state:running",
                ),
            ),
            "1",
        ),
        gateway_module._RuntimeEventPage(
            (),
            _Packet9StringSubclass("1"),
        ),
        gateway_module._RuntimeEventPage(
            _Packet9TupleSubclass(),
            None,
        ),
        _packet9_mutate(
            gateway_module._RuntimeEventPage((), None),
            missing="events",
        ),
        _packet9_mutate(
            gateway_module._RuntimeEventPage((), None),
            extra=True,
        ),
        gateway_module._RuntimeEventPage(
            (
                _packet9_mutate(
                    gateway_module._RuntimeEvent(
                        "1",
                        stable_action_id,
                        "state:running",
                    ),
                    missing="kind",
                ),
            ),
            "1",
        ),
        gateway_module._RuntimeEventPage(
            (
                _packet9_mutate(
                    gateway_module._RuntimeEvent(
                        "1",
                        stable_action_id,
                        "state:running",
                    ),
                    extra=True,
                ),
            ),
            "1",
        ),
    )

    for value in (*invalid_prepare_results, *invalid_failures):
        verdict = gateway_module._RuntimePrepareResultProtocol.validate(
            value,
            spec,  # type: ignore[arg-type]
        )
        assert verdict.kind == "invalid"
        assert verdict.failure is not None
        assert verdict.failure.code == "RUNTIME_PROVIDER_PROTOCOL_INVALID"

    for value in (*invalid_command_results, *invalid_failures):
        verdict = gateway_module._RuntimeCommandResultProtocol.validate(
            value,
            stable_action_id,
            RuntimeCommand.FENCE,
        )
        assert verdict.kind == "invalid"
        assert verdict.failure is not None
        assert verdict.failure.code == "RUNTIME_PROVIDER_PROTOCOL_INVALID"

    for value in (*invalid_event_pages, *invalid_failures):
        verdict = gateway_module._RuntimeEventPageProtocol.validate(value)
        assert verdict.kind == "invalid"
        assert verdict.failure is not None
        assert verdict.failure.code == "RUNTIME_PROVIDER_PROTOCOL_INVALID"


@pytest.mark.parametrize(
    "decision",
    (
        _Packet9StringSubclass("allow"),
        _Packet9EvilScalar(),
    ),
)
def test_repair_packet_9_permission_response_constructor_requires_exact_decision(
    decision,
):
    with pytest.raises(RuntimeGatewayError) as rejected:
        PermissionResponse(
            "request:packet-9",
            decision,  # type: ignore[arg-type]
        )
    assert rejected.value.code == "RUNTIME_COMMAND_INVALID"


def test_repair_packet_9_gateway_and_memory_reject_tampered_permission_before_effect(
    tmp_path,
):
    gateway, store, adapter = _gateway(tmp_path)
    subject = _put_subject_artifacts(store, _subject())
    request_id = "request:packet-9-memory"
    adapter._pending_permissions[subject.stable_action_id] = [
        (request_id, "write", "repository")
    ]
    preflight = gateway.planning_preflight(subject)
    gateway.progress(subject, preflight)
    read_verdict = gateway_module._ObservationProtocol.validate(
        adapter._reconcile_observation(subject.stable_action_id),
        selected_stable_action_id=subject.stable_action_id,
    )
    assert read_verdict.kind == "bound"
    assert read_verdict.token is not None
    malformed = PermissionResponse(request_id, "allow")
    object.__setattr__(malformed, "decision", _Packet9EvilScalar())
    actions_before = deepcopy(adapter._actions)
    command_calls_before = list(adapter.command_calls)
    created_before = adapter.created_agent_count

    with pytest.raises(RuntimeGatewayError) as gateway_rejected:
        gateway.transition(subject.stable_action_id, malformed)

    assert gateway_rejected.value.code == "RUNTIME_COMMAND_INVALID"
    assert adapter._actions == actions_before
    assert adapter.command_calls == command_calls_before
    assert adapter.created_agent_count == created_before

    direct_rejected = adapter.command(
        subject.stable_action_id,
        malformed,
    )

    assert type(direct_rejected) is _RuntimeFailure
    assert direct_rejected.code == "RUNTIME_COMMAND_INVALID"
    assert adapter._actions == actions_before
    assert adapter.command_calls == command_calls_before
    assert adapter.created_agent_count == created_before


def test_repair_packet_9_paseo_rejects_tampered_permission_before_effect(
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
    request_id = "request:packet-9-paseo"
    client.permissions = [_paseo_permission(request_id)]
    read_verdict = gateway_module._ObservationProtocol.validate(
        adapter._reconcile_observation(subject.stable_action_id),
        selected_stable_action_id=subject.stable_action_id,
    )
    assert read_verdict.kind == "bound"
    assert read_verdict.token is not None
    malformed = PermissionResponse(request_id, "allow")
    object.__setattr__(malformed, "decision", _Packet9EvilScalar())
    state_before = adapter._state_path.read_bytes()
    commands_before = deepcopy(client.commands)

    rejected = adapter.command(
        subject.stable_action_id,
        malformed,
    )

    assert type(rejected) is _RuntimeFailure
    assert rejected.code == "RUNTIME_COMMAND_INVALID"
    assert adapter._state_path.read_bytes() == state_before
    assert client.commands == commands_before


@pytest.mark.parametrize(
    "after_cursor",
    (
        True,
        1,
        _Packet9StringSubclass("1"),
        "\u0661",
        _Packet9EvilScalar(),
    ),
)
def test_repair_packet_9_memory_event_cursor_is_exact_ascii_and_total(
    tmp_path,
    after_cursor,
):
    gateway, store, adapter = _gateway(tmp_path)
    subject = _put_subject_artifacts(store, _subject())
    gateway.planning_preflight(subject)
    prompt = store.get(subject.planning_request_artifact_digest)
    assert type(
        adapter.prepare(
            _RuntimeActionSpec(
                subject.stable_action_id,
                subject,
                _profile(),
                prompt,
                (prompt,),
            )
        )
    ) is gateway_module._PrepareReceipt
    actions_before = deepcopy(adapter._actions)
    event_scan_before = adapter._event_scan_cursor

    rejected = adapter.events(after_cursor)  # type: ignore[arg-type]

    assert type(rejected) is _RuntimeFailure
    assert rejected.code == "RUNTIME_EVENT_CURSOR_INVALID"
    assert adapter._actions == actions_before
    assert adapter._event_scan_cursor == event_scan_before


@pytest.mark.parametrize(
    "after_cursor",
    (
        True,
        1,
        _Packet9StringSubclass("1"),
        "\u0661",
        _Packet9EvilScalar(),
    ),
)
def test_repair_packet_9_paseo_event_cursor_is_exact_ascii_and_total(
    tmp_path,
    after_cursor,
):
    (
        _store,
        _source,
        _workspace,
        client,
        adapter,
        _subject,
        _spec,
    ) = _prepared_paseo_adapter(tmp_path)
    state_before = adapter._state_path.read_bytes()
    event_scan_before = adapter._event_scan_cursor
    commands_before = deepcopy(client.commands)

    rejected = adapter.events(after_cursor)  # type: ignore[arg-type]

    assert type(rejected) is _RuntimeFailure
    assert rejected.code == "RUNTIME_EVENT_CURSOR_INVALID"
    assert adapter._state_path.read_bytes() == state_before
    assert adapter._event_scan_cursor == event_scan_before
    assert client.commands == commands_before


def test_repair_packet_9_observation_protocol_has_closed_semantic_kinds(
    tmp_path,
):
    gateway, store, adapter = _gateway(tmp_path)
    subject = _put_subject_artifacts(store, _subject())
    gateway.planning_preflight(subject)
    prompt = store.get(subject.planning_request_artifact_digest)
    assert type(
        adapter.prepare(
            _RuntimeActionSpec(
                subject.stable_action_id,
                subject,
                _profile(),
                prompt,
                (prompt,),
            )
        )
    ) is gateway_module._PrepareReceipt
    prepared_read = adapter._reconcile_observation(subject.stable_action_id)
    prepared_verdict = gateway_module._ObservationProtocol.validate(
        prepared_read,
        selected_stable_action_id=subject.stable_action_id,
    )

    assert prepared_verdict.kind == "prepared"
    assert prepared_verdict.token is not None
    malicious_result = _Packet9PreparedObservationSubclass(
        **asdict(prepared_verdict.observation)
    )
    malicious_verdict = gateway_module._ObservationProtocol.validate(
        replace(prepared_read, result=malicious_result),
        selected_stable_action_id=subject.stable_action_id,
    )
    assert malicious_verdict.kind == "invalid"
    observed = adapter.observe(subject.stable_action_id)
    assert type(observed) is gateway_module._PreparedRuntimeObservation
    receipt = adapter.command(
        subject.stable_action_id,
        RuntimeCommand.START,
    )
    assert type(receipt) is _CommandReceipt
    bound_verdict = gateway_module._ObservationProtocol.validate(
        adapter._reconcile_observation(subject.stable_action_id),
        selected_stable_action_id=subject.stable_action_id,
    )
    assert bound_verdict.kind == "bound"


@pytest.mark.parametrize(
    "failure_code",
    (
        "RUNTIME_PREPARE_ACK_LOST",
        "RUNTIME_EFFECT_AMBIGUOUS",
    ),
)
def test_repair_packet_9_malformed_prepare_ambiguity_has_zero_recovery_or_effect(
    tmp_path,
    failure_code,
):
    gateway, store, adapter = _gateway(tmp_path)
    subject = _put_subject_artifacts(store, _subject())
    preflight = gateway.planning_preflight(subject)
    read_calls = []
    native_read = adapter._reconcile_observation

    def counted_read(stable_action_id):
        read_calls.append(stable_action_id)
        return native_read(stable_action_id)

    def malformed_prepare(spec):
        return _RuntimeFailure(
            _Packet9StringSubclass(failure_code),
            "malformed prepare ambiguity",
            stable_action_id=spec.stable_action_id,
        )

    adapter._reconcile_observation = counted_read  # type: ignore[method-assign]
    adapter.prepare = malformed_prepare  # type: ignore[method-assign]

    with pytest.raises(RuntimeGatewayError) as rejected:
        gateway.progress(subject, preflight)

    assert rejected.value.code == "RUNTIME_PROVIDER_PROTOCOL_INVALID"
    assert read_calls == [subject.stable_action_id]
    assert adapter._actions == {}
    assert adapter.command_calls == []
    assert adapter.created_agent_count == 0


def test_repair_packet_9_malformed_command_ack_has_zero_recovery_or_effect(
    tmp_path,
):
    gateway, store, adapter = _gateway(tmp_path)
    subject = _put_subject_artifacts(store, _subject())
    adapter._pending_permissions[subject.stable_action_id] = [
        ("request:packet-9-ack", "write", "repository")
    ]
    preflight = gateway.planning_preflight(subject)
    gateway.progress(subject, preflight)
    actions_before = deepcopy(adapter._actions)
    command_calls_before = list(adapter.command_calls)
    created_before = adapter.created_agent_count
    read_calls = []
    native_read = adapter._reconcile_observation

    def counted_read(stable_action_id):
        read_calls.append(stable_action_id)
        return native_read(stable_action_id)

    def malformed_command(stable_action_id, command):
        return _RuntimeFailure(
            _Packet9StringSubclass("RUNTIME_COMMAND_ACK_LOST"),
            "malformed command acknowledgement",
            stable_action_id=stable_action_id,
        )

    adapter._reconcile_observation = counted_read  # type: ignore[method-assign]
    adapter.command = malformed_command  # type: ignore[method-assign]

    with pytest.raises(RuntimeGatewayError) as rejected:
        gateway.transition(
            subject.stable_action_id,
            RuntimeCommand.FENCE,
        )

    assert rejected.value.code == "RUNTIME_PROVIDER_PROTOCOL_INVALID"
    assert read_calls == [subject.stable_action_id]
    assert adapter._actions == actions_before
    assert adapter.command_calls == command_calls_before
    assert adapter.created_agent_count == created_before


@pytest.mark.parametrize("variant", ("transport", "event_kind"))
def test_repair_packet_9_malformed_event_transport_has_zero_publication_or_mutation(
    tmp_path,
    variant,
):
    gateway, store, adapter = _gateway(tmp_path)
    subject = _put_subject_artifacts(store, _subject())
    adapter._pending_permissions[subject.stable_action_id] = [
        ("request:packet-9-event", "write", "repository")
    ]
    preflight = gateway.planning_preflight(subject)
    running = gateway.progress(subject, preflight)
    actions_before = deepcopy(adapter._actions)
    event_scan_before = adapter._event_scan_cursor
    gateway_bytes_before = gateway._journal.path.read_bytes()

    def malformed_events(_cursor):
        if variant == "transport":
            return _RuntimeFailure(
                _Packet9StringSubclass(
                    "RUNTIME_TRANSPORT_UNAVAILABLE"
                ),
                "malformed event transport",
            )
        return gateway_module._RuntimeEventPage(
            (
                gateway_module._RuntimeEvent(
                    "1",
                    subject.stable_action_id,
                    _Packet9StringSubclass("state:running"),
                ),
            ),
            "1",
        )

    adapter.events = malformed_events  # type: ignore[method-assign]

    with pytest.raises(RuntimeGatewayError) as rejected:
        gateway._wake_hints(running.wake_cursor, subject)

    assert rejected.value.code == "RUNTIME_PROVIDER_PROTOCOL_INVALID"
    assert adapter._actions == actions_before
    assert adapter._event_scan_cursor == event_scan_before
    assert gateway._journal.path.read_bytes() == gateway_bytes_before


class _Packet10AlwaysEqual:
    def __eq__(self, _other):
        return True

    def __hash__(self):
        return hash("packet-10-always-equal")


class _Packet10IntSubclass(int):
    pass


def _packet10_observation_reads(tmp_path):
    _prepared_gateway, prepared_store, prepared_adapter = _gateway(
        tmp_path / "prepared"
    )
    prepared_subject = _put_subject_artifacts(prepared_store, _subject())
    prompt = prepared_store.get(
        prepared_subject.planning_request_artifact_digest
    )
    prepared = prepared_adapter.prepare(
        _RuntimeActionSpec(
            prepared_subject.stable_action_id,
            prepared_subject,
            _profile(),
            prompt,
            (prompt,),
        )
    )
    assert type(prepared) is gateway_module._PrepareReceipt
    prepared_read = prepared_adapter._reconcile_observation(
        prepared_subject.stable_action_id
    )
    assert gateway_module._ObservationProtocol.validate(
        prepared_read,
        selected_stable_action_id=prepared_subject.stable_action_id,
    ).kind == "prepared"

    gateway, store, adapter = _gateway(tmp_path / "completed")
    subject = _put_subject_artifacts(store, _subject())
    preflight = gateway.planning_preflight(subject)
    completed = gateway.progress(subject, preflight)
    assert completed.status == "completed"
    completed_read = adapter._reconcile_observation(subject.stable_action_id)
    assert gateway_module._ObservationProtocol.validate(
        completed_read,
        selected_stable_action_id=subject.stable_action_id,
    ).kind == "bound"
    return subject, prepared_read, completed_read


def _packet10_assert_invalid_read(read, stable_action_id):
    verdict = gateway_module._ObservationProtocol.validate(
        read,
        selected_stable_action_id=stable_action_id,
    )
    assert verdict.kind == "invalid"
    assert verdict.failure is not None
    assert verdict.failure.code == "RUNTIME_PROVIDER_PROTOCOL_INVALID"


@pytest.mark.parametrize(
    ("component", "field", "variant"),
    (
        ("read", "selected_stable_action_id", "subclass"),
        ("read", "selected_stable_action_id", "always_equal"),
        ("token", "stable_action_id", "subclass"),
        ("token", "stable_action_id", "always_equal"),
        ("token", "identity_digest", "subclass"),
        ("token", "identity_digest", "always_equal"),
        ("token", "selected_record_digest", "subclass"),
        ("token", "observation_digest", "subclass"),
        ("token", "observation_digest", "always_equal"),
        ("token", "output_artifact_digest", "subclass"),
        ("token", "output_artifact_digest", "always_equal"),
        ("prompt", "artifact_digest", "subclass"),
        ("prompt", "artifact_digest", "always_equal"),
        ("prompt", "byte_length", "int_subclass"),
        ("prompt", "byte_length", "over_store_bound"),
        ("prompt", "byte_length", "too_large"),
        ("output", "artifact_digest", "subclass"),
        ("output", "artifact_digest", "always_equal"),
        ("output", "byte_length", "int_subclass"),
        ("output", "byte_length", "over_store_bound"),
        ("output", "byte_length", "too_large"),
        ("output", "schema_version", "subclass"),
        ("output", "schema_version", "always_equal"),
        ("output", "subject_digest", "subclass"),
        ("output", "stable_action_id", "subclass"),
        ("output", "authority_digest", "subclass"),
    ),
)
def test_repair_packet_10_read_token_and_artifact_scalars_are_exact(
    tmp_path,
    component,
    field,
    variant,
):
    subject, prepared_read, completed_read = _packet10_observation_reads(
        tmp_path
    )
    read = (
        completed_read
        if component == "output"
        or field == "output_artifact_digest"
        else prepared_read
    )
    if component == "read":
        current = getattr(read, field)
        replacement = (
            _Packet9StringSubclass(current)
            if variant == "subclass"
            else _Packet10AlwaysEqual()
        )
        malicious = replace(read, **{field: replacement})
    elif component == "token":
        assert read.token is not None
        current = getattr(read.token, field)
        replacement = (
            _Packet9StringSubclass(current)
            if variant == "subclass"
            else _Packet10AlwaysEqual()
        )
        malicious = replace(
            read,
            token=replace(read.token, **{field: replacement}),
        )
    elif component == "prompt":
        assert read.artifact_evidence is not None
        proof = read.artifact_evidence.prompt
        current = getattr(proof, field)
        if variant == "subclass":
            replacement = _Packet9StringSubclass(current)
        elif variant == "always_equal":
            replacement = _Packet10AlwaysEqual()
        elif variant == "int_subclass":
            replacement = _Packet10IntSubclass(current)
        elif variant == "over_store_bound":
            replacement = 1_048_577
        else:
            replacement = 1 << 63
        malicious = replace(
            read,
            artifact_evidence=replace(
                read.artifact_evidence,
                prompt=replace(proof, **{field: replacement}),
            ),
        )
    else:
        assert read.artifact_evidence is not None
        proof = read.artifact_evidence.output
        assert proof is not None
        current = getattr(proof, field)
        if variant == "subclass":
            replacement = _Packet9StringSubclass(current)
        elif variant == "always_equal":
            replacement = _Packet10AlwaysEqual()
        elif variant == "int_subclass":
            replacement = _Packet10IntSubclass(current)
        elif variant == "over_store_bound":
            replacement = 1_048_577
        else:
            replacement = 1 << 63
        malicious = replace(
            read,
            artifact_evidence=replace(
                read.artifact_evidence,
                output=replace(proof, **{field: replacement}),
            ),
        )
    _packet10_assert_invalid_read(malicious, subject.stable_action_id)


def test_repair_packet_10_identity_scalar_table_is_exhaustive(tmp_path):
    subject, prepared_read, _completed_read = _packet10_observation_reads(
        tmp_path
    )
    assert prepared_read.identity is not None
    identity = prepared_read.identity
    scalar_fields = (
        "stable_action_id",
        "repository",
        "campaign_key",
        "campaign_handle",
        "subject_digest",
        "profile_digest",
        "workspace_id",
        "prompt_artifact_digest",
        "authority_subtree_digest",
        "spec_identity_digest",
    )
    for field in scalar_fields:
        current = getattr(identity, field)
        malicious = replace(
            prepared_read,
            identity=replace(
                identity,
                **{field: _Packet9StringSubclass(current)},
            ),
        )
        _packet10_assert_invalid_read(malicious, subject.stable_action_id)
    tuple_subclass = replace(
        prepared_read,
        identity=replace(
            identity,
            input_artifact_digests=_Packet9TupleSubclass(
                identity.input_artifact_digests
            ),
        ),
    )
    _packet10_assert_invalid_read(tuple_subclass, subject.stable_action_id)


def test_repair_packet_10_artifact_bound_parameter_is_exact_and_total(
    tmp_path,
):
    subject, prepared_read, _completed_read = _packet10_observation_reads(
        tmp_path
    )
    for invalid_bound in (
        True,
        _Packet10IntSubclass(1_048_576),
        0,
        1 << 63,
        _Packet9EvilScalar(),
    ):
        verdict = gateway_module._ObservationProtocol.validate(
            prepared_read,
            selected_stable_action_id=subject.stable_action_id,
            maximum_artifact_bytes=invalid_bound,
        )
        assert verdict.kind == "invalid"


@pytest.mark.parametrize(
    "lifecycle",
    (
        _Packet9StringSubclass("prepared"),
        _Packet10AlwaysEqual(),
    ),
)
def test_repair_packet_10_prepared_lifecycle_is_exact(tmp_path, lifecycle):
    subject, prepared_read, _completed_read = _packet10_observation_reads(
        tmp_path
    )
    malicious = replace(prepared_read.result, lifecycle=lifecycle)

    assert (
        gateway_module._runtime_observation_is_structurally_valid(malicious)
        is False
    )
    _packet10_assert_invalid_read(
        replace(prepared_read, result=malicious),
        subject.stable_action_id,
    )


def test_repair_packet_10_bound_lifecycle_evil_hash_is_total(tmp_path):
    subject, _prepared_read, completed_read = _packet10_observation_reads(
        tmp_path
    )
    malicious = replace(
        completed_read.result,
        lifecycle=_Packet9EvilScalar(),
    )

    assert (
        gateway_module._runtime_observation_is_structurally_valid(malicious)
        is False
    )
    _packet10_assert_invalid_read(
        replace(completed_read, result=malicious),
        subject.stable_action_id,
    )


def test_repair_packet_10_sealed_read_missing_and_extra_fields_are_invalid(
    tmp_path,
):
    subject, prepared_read, _completed_read = _packet10_observation_reads(
        tmp_path
    )
    missing = deepcopy(prepared_read)
    object.__delattr__(missing, "token")
    _packet10_assert_invalid_read(missing, subject.stable_action_id)

    mutable_result = deepcopy(prepared_read.result)
    object.__setattr__(mutable_result, "provider_extra", "untrusted")
    _packet10_assert_invalid_read(
        replace(prepared_read, result=mutable_result),
        subject.stable_action_id,
    )


@pytest.mark.parametrize(
    "cursor",
    (
        "",
        "0",
        "00",
        "01",
        "+1",
        " 1",
        "\u0661",
        _Packet9StringSubclass("1"),
        _Packet9EvilScalar(),
        str(1 << 63),
        "9" * 10_000,
        True,
        1,
    ),
)
def test_repair_packet_10_event_cursor_parser_is_canonical_bounded_and_total(
    cursor,
):
    assert gateway_module._runtime_event_cursor_value(cursor) is None


def test_repair_packet_10_event_cursor_parser_accepts_exact_domain_boundaries():
    maximum = (1 << 63) - 1
    assert gateway_module._runtime_event_cursor_value(None) == 0
    assert gateway_module._runtime_event_cursor_value("1") == 1
    assert (
        gateway_module._runtime_event_cursor_value(str(maximum))
        == maximum
    )


@pytest.mark.parametrize(
    ("after_cursor", "events", "next_cursor"),
    (
        (None, (), None),
        ("7", (), "7"),
        (
            "7",
            (
                gateway_module._RuntimeEvent(
                    "8",
                    "planning:packet-10",
                    "state:running",
                ),
                gateway_module._RuntimeEvent(
                    "10",
                    "planning:packet-10",
                    "state:parked",
                ),
            ),
            "10",
        ),
    ),
)
def test_repair_packet_10_event_page_protocol_binds_requested_cursor(
    after_cursor,
    events,
    next_cursor,
):
    verdict = gateway_module._RuntimeEventPageProtocol.validate(
        gateway_module._RuntimeEventPage(events, next_cursor),
        after_cursor=after_cursor,
    )
    assert verdict.kind == "page"


@pytest.mark.parametrize(
    ("after_cursor", "events", "next_cursor"),
    (
        ("0", (), "0"),
        ("7", (), None),
        ("7", (), "6"),
        ("7", (), "8"),
        (
            "7",
            (
                gateway_module._RuntimeEvent(
                    "7",
                    "planning:packet-10",
                    "state:running",
                ),
            ),
            "7",
        ),
        (
            "7",
            (
                gateway_module._RuntimeEvent(
                    "08",
                    "planning:packet-10",
                    "state:running",
                ),
            ),
            "08",
        ),
        (
            "7",
            (
                gateway_module._RuntimeEvent(
                    "8",
                    "planning:packet-10",
                    "state:running",
                ),
            ),
            "9",
        ),
        (
            "7",
            (
                gateway_module._RuntimeEvent(
                    str(1 << 63),
                    "planning:packet-10",
                    "state:running",
                ),
            ),
            str(1 << 63),
        ),
    ),
)
def test_repair_packet_10_event_page_protocol_rejects_replay_skip_and_regression(
    after_cursor,
    events,
    next_cursor,
):
    verdict = gateway_module._RuntimeEventPageProtocol.validate(
        gateway_module._RuntimeEventPage(events, next_cursor),
        after_cursor=after_cursor,
    )
    assert verdict.kind == "invalid"
    assert verdict.failure is not None
    assert verdict.failure.code == (
        "RUNTIME_EVENT_CURSOR_INVALID"
        if after_cursor == "0"
        else "RUNTIME_PROVIDER_PROTOCOL_INVALID"
    )


def test_repair_packet_10_memory_paseo_and_gateway_cursor_rejection_has_zero_mutation(
    tmp_path,
):
    invalid_cursors = (
        "0",
        "01",
        "1",
        str(1 << 63),
        "9" * 10_000,
        _Packet9StringSubclass("1"),
        _Packet9EvilScalar(),
    )
    gateway, store, memory = _gateway(tmp_path / "memory")
    subject = _put_subject_artifacts(store, _subject())
    gateway.planning_preflight(subject)
    memory_before = (
        deepcopy(memory._actions),
        deepcopy(memory._events),
        memory._next_event_cursor,
        memory._event_scan_cursor,
    )
    gateway_before = gateway._journal.path.read_bytes()

    (tmp_path / "paseo").mkdir()
    (
        _paseo_store,
        _source,
        _workspace,
        client,
        paseo,
        _paseo_subject,
        _spec,
    ) = _prepared_paseo_adapter(tmp_path / "paseo")
    paseo_before = paseo._state_path.read_bytes()
    paseo_scan_before = paseo._event_scan_cursor
    paseo_commands_before = deepcopy(client.commands)

    for cursor in invalid_cursors:
        memory_failure = memory.events(cursor)  # type: ignore[arg-type]
        assert type(memory_failure) is _RuntimeFailure
        assert memory_failure.code == "RUNTIME_EVENT_CURSOR_INVALID"
        with pytest.raises(RuntimeGatewayError) as gateway_failure:
            gateway._wake_hints(cursor, subject)  # type: ignore[arg-type]
        assert gateway_failure.value.code == "RUNTIME_EVENT_CURSOR_INVALID"
        paseo_failure = paseo.events(cursor)  # type: ignore[arg-type]
        assert type(paseo_failure) is _RuntimeFailure
        assert paseo_failure.code == "RUNTIME_EVENT_CURSOR_INVALID"

    assert (
        deepcopy(memory._actions),
        deepcopy(memory._events),
        memory._next_event_cursor,
        memory._event_scan_cursor,
    ) == memory_before
    assert gateway._journal.path.read_bytes() == gateway_before
    assert paseo._state_path.read_bytes() == paseo_before
    assert paseo._event_scan_cursor == paseo_scan_before
    assert client.commands == paseo_commands_before


@pytest.mark.parametrize(
    "mutation",
    (
        lambda state: state.update(
            {
                "events": [
                    {
                        "cursor": str(index),
                        "stable_action_id": "planning:packet-10",
                        "kind": "state:running",
                    }
                    for index in range(1, 66)
                ],
                "next_event_cursor": 66,
            }
        ),
        lambda state: state.update(
            {
                "events": [
                    {
                        "cursor": "01",
                        "stable_action_id": "planning:packet-10",
                        "kind": "state:running",
                    }
                ],
                "next_event_cursor": 2,
            }
        ),
        lambda state: state.update(
            {
                "events": [
                    {
                        "cursor": "2",
                        "stable_action_id": "planning:packet-10",
                        "kind": "state:running",
                    }
                ],
                "next_event_cursor": 3,
            }
        ),
        lambda state: state.update(
            {
                "events": [
                    {
                        "cursor": "1",
                        "stable_action_id": "planning:packet-10",
                        "kind": "state:unknown",
                    }
                ],
                "next_event_cursor": 2,
            }
        ),
        lambda state: state.update({"next_event_cursor": 2}),
        lambda state: state.update({"event_scan_cursor": True}),
        lambda state: state.update({"event_scan_cursor": 1 << 63}),
        lambda state: state.update({"next_event_cursor": (1 << 63) + 1}),
    ),
)
def test_repair_packet_10_paseo_journal_rejects_cursor_corruption_without_normalizing(
    tmp_path,
    mutation,
):
    store, source, workspace, client, adapter, _subject, _spec = (
        _prepared_paseo_adapter(tmp_path)
    )
    durable = json.loads(adapter._state_path.read_text(encoding="utf-8"))
    mutation(durable)
    adapter._state_path.write_bytes(gateway_module.canonical_bytes(durable))
    corrupted = adapter._state_path.read_bytes()

    with pytest.raises(RuntimeGatewayError) as rejected:
        _PaseoRuntimeProviderAdapter(
            client=client,  # type: ignore[arg-type]
            artifacts=store,
            repository_contexts={
                "owner/repository": RuntimeRepositoryContext(source, "main")
            },
            state_path=adapter._state_path,
        )

    assert rejected.value.code == "RUNTIME_STORE_INVALID"
    assert adapter._state_path.read_bytes() == corrupted


@pytest.mark.parametrize(
    ("failure", "expected_kind"),
    (
        (_RuntimeFailure.transport(), "fairness_advance"),
        (
            _RuntimeFailure(
                "RUNTIME_BINDING_MISSING",
                "binding is not visible",
                stable_action_id="planning:packet-10-disposition",
            ),
            "fairness_advance",
        ),
        (
            _RuntimeFailure(
                "RUNTIME_MATERIALIZATION_PENDING",
                "materialization is pending",
                stable_action_id="planning:packet-10-disposition",
            ),
            "fairness_advance",
        ),
        (
            _RuntimeFailure(
                "RUNTIME_IDENTITY_AMBIGUOUS",
                "identity is ambiguous",
            ),
            "failure",
        ),
    ),
)
def test_repair_packet_10_observation_protocol_owns_event_failure_disposition(
    tmp_path,
    failure,
    expected_kind,
):
    action_id = "planning:packet-10-disposition"
    adapter = _InMemoryRuntimeProviderAdapter(
        ArtifactStore(tmp_path / "artifacts")
    )
    adapter._actions[action_id] = SimpleNamespace(
        wake_state_digest=None,
        wake_terminal_emitted=False,
    )
    observation_read = _event_observation_read(
        adapter,
        action_id,
        _event_bound_observation(action_id),
    )
    assert observation_read.identity is not None
    assert observation_read.token is not None
    failure_read = gateway_module._runtime_sealed_failure_read(
        action_id,
        failure,
        identity=observation_read.identity,
        selected_record_digest=(
            observation_read.token.selected_record_digest
        ),
    )

    verdict = gateway_module._ObservationProtocol.validate(
        failure_read,
        selected_stable_action_id=action_id,
    )

    assert verdict.kind == expected_kind
    assert verdict.failure == failure


def test_repair_packet_10_malformed_transport_never_advances_event_scan(
    tmp_path,
):
    action_id = "planning:packet-10-malformed-transport"
    adapter = _InMemoryRuntimeProviderAdapter(
        ArtifactStore(tmp_path / "artifacts")
    )
    adapter._actions[action_id] = SimpleNamespace(
        wake_state_digest=None,
        wake_terminal_emitted=False,
    )
    observation_read = _event_observation_read(
        adapter,
        action_id,
        _event_bound_observation(action_id),
    )
    assert observation_read.identity is not None
    assert observation_read.token is not None
    malformed = gateway_module._runtime_sealed_failure_read(
        action_id,
        _RuntimeFailure(
            _Packet9StringSubclass("RUNTIME_TRANSPORT_UNAVAILABLE"),
            "transport unavailable",
        ),
        identity=observation_read.identity,
        selected_record_digest=(
            observation_read.token.selected_record_digest
        ),
    )
    adapter._reconcile_observation = (  # type: ignore[method-assign]
        lambda _stable_action_id: malformed
    )
    actions_before = deepcopy(adapter._actions)
    events_before = deepcopy(adapter._events)
    scan_before = adapter._event_scan_cursor
    next_before = adapter._next_event_cursor

    rejected = adapter.events(None)

    assert type(rejected) is _RuntimeFailure
    assert rejected.code == "RUNTIME_PROVIDER_PROTOCOL_INVALID"
    assert adapter._actions == actions_before
    assert adapter._events == events_before
    assert adapter._event_scan_cursor == scan_before
    assert adapter._next_event_cursor == next_before


def test_repair_packet_10_event_page_protocol_owns_transient_transport_kind(
    tmp_path,
):
    invalid_request = gateway_module._RuntimeEventPageProtocol.validate(
        _RuntimeFailure.transport(),
        after_cursor="0",
    )
    assert invalid_request.kind == "invalid"
    verdict = gateway_module._RuntimeEventPageProtocol.validate(
        _RuntimeFailure.transport(),
        after_cursor="7",
    )
    assert verdict.kind == "transient_failure"

    gateway, store, adapter = _gateway(tmp_path)
    subject = _put_subject_artifacts(store, _subject())
    gateway.planning_preflight(subject)
    adapter.events = (  # type: ignore[method-assign]
        lambda _after_cursor: _RuntimeFailure.transport()
    )
    assert gateway._wake_hints("7", subject) == ((), "7")


@pytest.mark.parametrize("adapter_kind", ("memory", "paseo"))
def test_repair_packet_10_event_cursor_exhaustion_is_typed_and_atomic(
    tmp_path,
    adapter_kind,
):
    maximum = (1 << 63) - 1
    action_id = "planning:packet-10-exhaustion"
    lifecycle = ["running"]
    if adapter_kind == "memory":
        adapter = _InMemoryRuntimeProviderAdapter(
            ArtifactStore(tmp_path / "artifacts")
        )
        adapter._actions[action_id] = SimpleNamespace(
            wake_state_digest=None,
            wake_terminal_emitted=False,
        )
        adapter._next_event_cursor = maximum
        durable_path = None
    else:
        (
            _store,
            _source,
            _workspace,
            _client,
            adapter,
            prepared_subject,
            _spec,
        ) = _prepared_paseo_adapter(
            tmp_path
        )
        action_id = prepared_subject.stable_action_id

        def seed(state):
            state["actions"][action_id]["wake_state_digest"] = None
            state["actions"][action_id]["wake_terminal_emitted"] = False
            state["events"] = [
                gateway_module._RuntimeEvent(
                    str(cursor),
                    action_id,
                    "state:running",
                )
                for cursor in range(maximum - 64, maximum)
            ]
            state["next_event_cursor"] = maximum

        adapter._transact(seed)
        durable_path = adapter._state_path

    def selected_read(selected_action_id):
        return _event_observation_read(
            adapter,
            selected_action_id,
            _event_bound_observation(
                selected_action_id,
                lifecycle=lifecycle[0],
            ),
        )

    adapter._reconcile_observation = selected_read  # type: ignore[method-assign]
    first = adapter.events(None)
    assert type(first) is gateway_module._RuntimeEventPage
    assert adapter._next_event_cursor == maximum + 1
    assert adapter._events[-1].cursor == str(maximum)

    events_at_max = deepcopy(adapter._events)
    scan_at_max = adapter._event_scan_cursor
    unchanged = adapter.events(str(maximum))
    assert type(unchanged) is gateway_module._RuntimeEventPage
    assert unchanged.events == ()
    assert adapter._events == events_at_max
    assert adapter._next_event_cursor == maximum + 1
    assert adapter._event_scan_cursor == scan_at_max + 1

    lifecycle[0] = "parked"
    actions_before = deepcopy(adapter._actions)
    events_before = deepcopy(adapter._events)
    scan_before = adapter._event_scan_cursor
    next_before = adapter._next_event_cursor
    durable_before = (
        None if durable_path is None else durable_path.read_bytes()
    )

    rejected = adapter.events(str(maximum))

    assert type(rejected) is _RuntimeFailure
    assert rejected.code == "RUNTIME_EVENT_CURSOR_EXHAUSTED"
    assert adapter._actions == actions_before
    assert adapter._events == events_before
    assert adapter._event_scan_cursor == scan_before
    assert adapter._next_event_cursor == next_before
    if durable_path is not None:
        assert durable_path.read_bytes() == durable_before


@pytest.mark.parametrize("adapter_kind", ("memory", "paseo"))
def test_repair_packet_10_event_scan_counter_wraps_at_exact_bound(
    tmp_path,
    adapter_kind,
):
    maximum = (1 << 63) - 1
    action_id = "planning:packet-10-scan-wrap"
    observation = _event_bound_observation(action_id)
    state = gateway_module._runtime_event_observation_state(
        observation, action_id
    )[0]
    state_digest = digest_value(state)
    if adapter_kind == "memory":
        adapter = _InMemoryRuntimeProviderAdapter(
            ArtifactStore(tmp_path / "artifacts")
        )
        adapter._actions[action_id] = SimpleNamespace(
            wake_state_digest=state_digest,
            wake_terminal_emitted=False,
        )
        adapter._event_scan_cursor = maximum
        durable_path = None
    else:
        (
            _store,
            _source,
            _workspace,
            _client,
            adapter,
            prepared_subject,
            _spec,
        ) = _prepared_paseo_adapter(
            tmp_path
        )
        action_id = prepared_subject.stable_action_id
        observation = _event_bound_observation(action_id)
        _event_observation_read(adapter, action_id, observation)
        state = gateway_module._runtime_event_observation_state(
            observation, action_id
        )[0]
        state_digest = digest_value(state)

        def seed(candidate):
            candidate["actions"][action_id]["wake_state"] = state
            candidate["actions"][action_id]["wake_state_digest"] = state_digest
            candidate["actions"][action_id]["wake_terminal_emitted"] = False
            candidate["event_scan_cursor"] = maximum

        adapter._transact(seed)
        durable_path = adapter._state_path

    adapter._reconcile_observation = (  # type: ignore[method-assign]
        lambda selected_action_id: _event_observation_read(
            adapter,
            selected_action_id,
            observation,
        )
    )
    events_before = deepcopy(adapter._events)
    next_before = adapter._next_event_cursor

    page = adapter.events(None)

    assert type(page) is gateway_module._RuntimeEventPage
    assert page.events == ()
    assert adapter._event_scan_cursor == 0
    assert adapter._events == events_before
    assert adapter._next_event_cursor == next_before
    if durable_path is not None:
        durable = json.loads(durable_path.read_text(encoding="utf-8"))
        assert durable["event_scan_cursor"] == 0


class _Packet11EqualityTrap:
    def __init__(self):
        self.equality_calls = 0
        self.hash_calls = 0

    def __eq__(self, _other):
        self.equality_calls += 1
        raise RuntimeError("malformed token equality must not execute")

    def __hash__(self):
        self.hash_calls += 1
        raise RuntimeError("malformed token hashing must not execute")


class _Packet11HashTrap:
    def __init__(self):
        self.equality_calls = 0
        self.hash_calls = 0

    def __eq__(self, _other):
        self.equality_calls += 1
        return True

    def __hash__(self):
        self.hash_calls += 1
        raise RuntimeError("malformed token hashing must not execute")


@pytest.mark.parametrize(
    "variant",
    (
        "stable_action_subclass",
        "identity_digest_subclass",
        "selected_record_digest_subclass",
        "observation_digest_subclass",
        "output_digest_wrong_type",
        "evil_equality",
        "evil_hash",
        "missing_field",
    ),
)
def test_repair_packet_11_memory_command_rejects_malformed_exact_token_before_effect(
    tmp_path,
    variant,
):
    gateway, store, adapter = _gateway(tmp_path)
    subject = _put_subject_artifacts(store, _subject())
    adapter._pending_permissions[subject.stable_action_id] = [
        ("request:packet-11", "write", "repository")
    ]
    preflight = gateway.planning_preflight(subject)
    running = gateway.progress(subject, preflight)
    assert running.status == "running"
    verdict = gateway_module._ObservationProtocol.validate(
        adapter._reconcile_observation(subject.stable_action_id),
        selected_stable_action_id=subject.stable_action_id,
        maximum_artifact_bytes=store.maximum_bytes,
    )
    assert verdict.kind == "bound"
    assert type(verdict.token) is gateway_module._RuntimeObservationReadToken
    token = deepcopy(verdict.token)
    trap = None
    if variant == "stable_action_subclass":
        object.__setattr__(
            token,
            "stable_action_id",
            _Packet9StringSubclass(token.stable_action_id),
        )
    elif variant == "identity_digest_subclass":
        object.__setattr__(
            token,
            "identity_digest",
            _Packet9StringSubclass(token.identity_digest),
        )
    elif variant == "selected_record_digest_subclass":
        object.__setattr__(
            token,
            "selected_record_digest",
            _Packet9StringSubclass(token.selected_record_digest),
        )
    elif variant == "observation_digest_subclass":
        assert token.observation_digest is not None
        object.__setattr__(
            token,
            "observation_digest",
            _Packet9StringSubclass(token.observation_digest),
        )
    elif variant == "output_digest_wrong_type":
        object.__setattr__(
            token,
            "output_artifact_digest",
            _Packet9EvilScalar(),
        )
    elif variant == "evil_equality":
        trap = _Packet11EqualityTrap()
        object.__setattr__(token, "stable_action_id", trap)
    elif variant == "evil_hash":
        trap = _Packet11HashTrap()
        object.__setattr__(token, "stable_action_id", trap)
    else:
        object.__delattr__(token, "identity_digest")

    actions_before = deepcopy(adapter._actions)
    command_calls_before = list(adapter.command_calls)
    created_before = adapter.created_agent_count
    events_before = deepcopy(adapter._events)
    scan_before = adapter._event_scan_cursor
    next_before = adapter._next_event_cursor
    journal_before = gateway._journal.path.read_bytes()
    artifacts_before = {
        path.relative_to(store._root).as_posix(): path.read_bytes()
        for path in store._root.rglob("*")
        if path.is_file()
    }
    adapter._command_gate.replace(subject.stable_action_id, token)

    rejected = adapter.command(
        subject.stable_action_id,
        RuntimeCommand.PARK,
    )

    assert type(rejected) is _RuntimeFailure
    assert rejected.code == "RUNTIME_PROVIDER_PROTOCOL_INVALID"
    assert adapter._actions == actions_before
    assert adapter.command_calls == command_calls_before
    assert adapter.created_agent_count == created_before
    assert adapter._events == events_before
    assert adapter._event_scan_cursor == scan_before
    assert adapter._next_event_cursor == next_before
    assert gateway._journal.path.read_bytes() == journal_before
    assert {
        path.relative_to(store._root).as_posix(): path.read_bytes()
        for path in store._root.rglob("*")
        if path.is_file()
    } == artifacts_before
    if trap is not None:
        assert trap.equality_calls == 0
        assert trap.hash_calls == 0


@pytest.mark.parametrize(
    "variant",
    (
        "missing_next",
        "missing_scan",
        "missing_both",
        "next_none",
        "scan_none",
        "next_string",
        "scan_float",
        "next_zero",
        "scan_negative",
        "next_overflow",
        "scan_overflow",
    ),
)
def test_repair_packet_11_paseo_v3_requires_exact_event_counters_on_restart(
    tmp_path,
    variant,
):
    store, source, _workspace, client, adapter, _subject, _spec = (
        _prepared_paseo_adapter(tmp_path)
    )
    durable = json.loads(adapter._state_path.read_text(encoding="utf-8"))
    if variant in {"missing_next", "missing_both"}:
        durable.pop("next_event_cursor")
    if variant in {"missing_scan", "missing_both"}:
        durable.pop("event_scan_cursor")
    if variant == "next_none":
        durable["next_event_cursor"] = None
    elif variant == "scan_none":
        durable["event_scan_cursor"] = None
    elif variant == "next_string":
        durable["next_event_cursor"] = "1"
    elif variant == "scan_float":
        durable["event_scan_cursor"] = 0.0
    elif variant == "next_zero":
        durable["next_event_cursor"] = 0
    elif variant == "scan_negative":
        durable["event_scan_cursor"] = -1
    elif variant == "next_overflow":
        durable["next_event_cursor"] = (1 << 63) + 1
    elif variant == "scan_overflow":
        durable["event_scan_cursor"] = 1 << 63
    adapter._state_path.write_bytes(gateway_module.canonical_bytes(durable))
    corrupted = adapter._state_path.read_bytes()
    commands_before = deepcopy(client.commands)

    with pytest.raises(RuntimeGatewayError) as rejected:
        _PaseoRuntimeProviderAdapter(
            client=client,  # type: ignore[arg-type]
            artifacts=store,
            repository_contexts={
                "owner/repository": RuntimeRepositoryContext(source, "main")
            },
            state_path=adapter._state_path,
        )

    assert rejected.value.code == "RUNTIME_STORE_INVALID"
    assert adapter._state_path.read_bytes() == corrupted
    assert client.commands == commands_before


@pytest.mark.parametrize(
    "field",
    ("next_event_cursor", "event_scan_cursor"),
)
def test_repair_packet_11_paseo_v3_rejects_counter_subclasses_on_restart(
    tmp_path,
    monkeypatch,
    field,
):
    store, source, _workspace, client, adapter, _subject, _spec = (
        _prepared_paseo_adapter(tmp_path)
    )
    durable_before = adapter._state_path.read_bytes()
    commands_before = deepcopy(client.commands)
    native_read = gateway_module._V3JsonJournal.read_unlocked

    def subclass_counter(journal):
        value = native_read(journal)
        assert type(value) is dict
        value[field] = _Packet10IntSubclass(value[field])
        return value

    monkeypatch.setattr(
        gateway_module._V3JsonJournal,
        "read_unlocked",
        subclass_counter,
    )

    with pytest.raises(RuntimeGatewayError) as rejected:
        _PaseoRuntimeProviderAdapter(
            client=client,  # type: ignore[arg-type]
            artifacts=store,
            repository_contexts={
                "owner/repository": RuntimeRepositoryContext(source, "main")
            },
            state_path=adapter._state_path,
        )

    assert rejected.value.code == "RUNTIME_STORE_INVALID"
    assert adapter._state_path.read_bytes() == durable_before
    assert client.commands == commands_before


def test_repair_packet_12_canonical_exact_tuple_and_list_share_array_identity():
    tuple_value = {
        "array": (
            {"nested": (None, True, -7)},
            ["tail", (2, 3)],
        )
    }
    list_value = {
        "array": [
            {"nested": [None, True, -7]},
            ["tail", [2, 3]],
        ]
    }
    expected = (
        b'{"array":[{"nested":[null,true,-7]},["tail",[2,3]]]}'
    )

    assert canonical_module.canonical_bytes(tuple_value) == expected
    assert canonical_module.canonical_bytes(list_value) == expected
    assert canonical_module.digest_value(tuple_value) == (
        canonical_module.digest_value(list_value)
    )


class _Packet12TupleSubclass(tuple):
    pass


class _Packet12ListSubclass(list):
    pass


class _Packet12HostileIterable:
    def __init__(self):
        self.iteration_calls = 0

    def __iter__(self):
        self.iteration_calls += 1
        raise RuntimeError("non-array iterable must not execute")

    def __len__(self):
        self.iteration_calls += 1
        raise RuntimeError("non-array iterable length must not execute")


class _Packet12HostileTypeMeta(type):
    hash_calls = 0

    def __hash__(cls):
        cls.hash_calls += 1
        raise RuntimeError("unknown value type hashing must not execute")


class _Packet12HostileTypedValue(metaclass=_Packet12HostileTypeMeta):
    pass


@pytest.mark.parametrize(
    "value",
    (
        _Packet12TupleSubclass(("tuple",)),
        {"nested": _Packet12TupleSubclass(("tuple",))},
        _Packet12ListSubclass(["list"]),
        {"nested": _Packet12ListSubclass(["list"])},
    ),
)
def test_repair_packet_12_canonical_array_subclasses_remain_outside_domain(
    value,
):
    with pytest.raises(canonical_module.CanonicalJsonError):
        canonical_module.canonical_bytes(value)


def test_repair_packet_12_canonical_tuple_cycle_is_typed():
    bridge = []
    value = (bridge,)
    bridge.append(value)

    with pytest.raises(
        canonical_module.CanonicalJsonError,
        match="reference cycle",
    ):
        canonical_module.canonical_bytes(value)


def test_repair_packet_12_canonical_tuple_depth_has_exact_boundary():
    accepted = None
    for _index in range(canonical_module._MAX_CANONICAL_JSON_DEPTH):
        accepted = (accepted,)
    rejected = (accepted,)

    canonical_module.canonical_bytes(accepted)
    with pytest.raises(canonical_module.CanonicalJsonError):
        canonical_module.canonical_bytes(rejected)


def test_repair_packet_12_canonical_tuple_rejects_hostile_nested_iterable_without_calling_it():
    hostile = _Packet12HostileIterable()

    with pytest.raises(
        canonical_module.CanonicalJsonError,
        match=r"\$\[1\]\.nested contains a value outside",
    ):
        canonical_module.canonical_bytes(("safe", {"nested": hostile}))

    assert hostile.iteration_calls == 0


@pytest.mark.parametrize(
    ("nested", "detail"),
    (
        (float("nan"), "non-finite number"),
        (float("inf"), "non-finite number"),
        (float("-inf"), "non-finite number"),
        ("\ud800", "non-scalar Unicode surrogate"),
        ("\udfff", "non-scalar Unicode surrogate"),
        (
            canonical_module._MAX_CANONICAL_INTEGER_ABS,
            "maximum canonical integer size",
        ),
    ),
)
def test_repair_packet_12_canonical_tuple_recurses_through_closed_scalar_domain(
    nested,
    detail,
):
    with pytest.raises(canonical_module.CanonicalJsonError, match=detail):
        canonical_module.canonical_bytes(("safe", {"nested": nested}))


def test_repair_packet_12_canonical_unknown_type_does_not_hash_its_metaclass():
    hostile = _Packet12HostileTypedValue()
    _Packet12HostileTypeMeta.hash_calls = 0

    with pytest.raises(canonical_module.CanonicalJsonError):
        canonical_module.canonical_bytes(hostile)

    assert _Packet12HostileTypeMeta.hash_calls == 0


def test_repair_packet_13_gateway_assignment_is_cross_bound_to_preflight_before_adapter_touch(
    tmp_path,
):
    """A valid replacement Profile cannot rewrite a crashed Planning action."""

    store = ArtifactStore(tmp_path / "artifacts", maximum_bytes=300_000)
    primary = _profile()
    replacement = replace(primary, name="replacement")
    adapter = _InMemoryRuntimeProviderAdapter(store)
    configuration = RuntimeConfiguration(
        profiles={primary.digest: primary, replacement.digest: replacement},
        host_mappings={"coordinator": ProfileMapping(primary.digest)},
    )
    durable_path = tmp_path / "gateway.journal"
    subject = _put_subject_artifacts(store, _subject())
    gateway = RuntimeGateway(
        store_path=durable_path,
        _adapter=adapter,
        configuration=configuration,
        _artifacts=store,
    )
    preflight = gateway.planning_preflight(subject)
    # Simulate the durable point after assignment but before Adapter.prepare.
    gateway._assignment_for_progress(subject, gateway._require_preflight(subject, preflight))
    durable = json.loads(durable_path.read_text(encoding="utf-8"))
    action = durable["actions"][subject.stable_action_id]
    action["profile_digest"] = replacement.digest
    # An attacker can recompute a local action checksum.  The independently
    # preflight-pinned seal must still reject the substituted Profile.
    action["assignment_digest"] = gateway_module._assignment_digest(
        subject,
        {
            key: action[key]
            for key in gateway_module._ASSIGNMENT_RECORD_KEYS
        },
    )
    durable_path.write_bytes(gateway_module.canonical_bytes(durable))
    prepare_calls_before = list(adapter.prepare_calls)

    with pytest.raises(RuntimeGatewayError) as rejected:
        RuntimeGateway(
            store_path=durable_path,
            _adapter=adapter,
            configuration=configuration,
            _artifacts=store,
        )

    assert rejected.value.code == "RUNTIME_STORE_INVALID"
    assert adapter.prepare_calls == prepare_calls_before == []


def test_repair_packet_13_gateway_assignment_rejects_unknown_action_fields_before_adapter_touch(
    tmp_path,
):
    gateway, store, adapter = _gateway(tmp_path)
    subject = _put_subject_artifacts(store, _subject())
    preflight = gateway.planning_preflight(subject)
    gateway._assignment_for_progress(subject, gateway._require_preflight(subject, preflight))
    durable_path = gateway._store_path
    durable = json.loads(durable_path.read_text(encoding="utf-8"))
    durable["actions"][subject.stable_action_id]["assignment_unrecognized"] = "tamper"
    durable_path.write_bytes(gateway_module.canonical_bytes(durable))
    prepare_calls_before = list(adapter.prepare_calls)

    with pytest.raises(RuntimeGatewayError) as rejected:
        RuntimeGateway(
            store_path=durable_path,
            _adapter=adapter,
            configuration=gateway._configuration,
            _artifacts=store,
        )

    assert rejected.value.code == "RUNTIME_STORE_INVALID"
    assert adapter.prepare_calls == prepare_calls_before == []


def test_repair_packet_13_repository_context_is_a_sealed_independent_snapshot(
    tmp_path,
):
    store = ArtifactStore(tmp_path / "artifacts")
    source, workspace = _repository_worktree(tmp_path)
    context = RuntimeRepositoryContext(source, "main")
    adapter = _PaseoRuntimeProviderAdapter(
        client=_RecordingPaseoCli(workspace),  # type: ignore[arg-type]
        artifacts=store,
        repository_contexts={"owner/repository": context},
        state_path=tmp_path / "paseo-actions.json",
    )
    alternate = tmp_path / "not-a-repository"
    alternate.mkdir()

    with pytest.raises(RuntimeGatewayError) as rejected:
        context.__init__(alternate, "other")

    assert rejected.value.code == "RUNTIME_CONFIGURATION_INVALID"
    captured = adapter._contexts["owner/repository"]
    assert captured is not context
    assert captured.path == source.resolve()
    assert captured.base_ref == "main"


@pytest.mark.parametrize(
    "variant",
    (
        "missing",
        "extra",
        "wrong_type",
        "invalid_transition",
        "parked_missing",
        "parked_wrong_type",
    ),
)
def test_repair_packet_13_paseo_action_journal_requires_closed_recovery_state(
    tmp_path,
    variant,
):
    store, source, _workspace, client, adapter, subject, _spec = _prepared_paseo_adapter(
        tmp_path
    )
    durable = json.loads(adapter._state_path.read_text(encoding="utf-8"))
    record = durable["actions"][subject.stable_action_id]
    if variant == "missing":
        record.pop("pending_start", None)
    elif variant == "extra":
        record["recovery_extension"] = True
    elif variant == "wrong_type":
        record["pending_start"] = "true"
    elif variant == "parked_missing":
        record.pop("parked", None)
    elif variant == "parked_wrong_type":
        record["parked"] = "false"
    else:
        record["bound_agent_id"] = "agent:one"
        record["pending_start"] = True
    adapter._state_path.write_bytes(gateway_module.canonical_bytes(durable))
    commands_before = deepcopy(client.commands)

    with pytest.raises(RuntimeGatewayError) as rejected:
        _PaseoRuntimeProviderAdapter(
            client=client,  # type: ignore[arg-type]
            artifacts=store,
            repository_contexts={
                "owner/repository": RuntimeRepositoryContext(source, "main")
            },
            state_path=adapter._state_path,
        )

    assert rejected.value.code == "RUNTIME_STORE_INVALID"
    assert client.commands == commands_before


def test_repair_packet_13_missing_pending_start_rejects_postdispatch_ack_loss_before_retry(
    tmp_path,
):
    """A missing start claim cannot turn one ambiguous `run` into a retry."""
    store = ArtifactStore(tmp_path / "artifacts")
    source, workspace = _repository_worktree(tmp_path)
    client = _RecordingPaseoCli(workspace, lose_start_ack_after_effect=True)
    state_path = tmp_path / "paseo-actions.json"
    adapter = _PaseoRuntimeProviderAdapter(
        client=client,  # type: ignore[arg-type]
        artifacts=store,
        repository_contexts={
            "owner/repository": RuntimeRepositoryContext(source, "main")
        },
        state_path=state_path,
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
    # The provider applied `run`, but the first post-dispatch label readback
    # remains absent.  The durable claim is the only duplicate-effect guard.
    client.hide_agent_queries = 1
    started = _adapter_command(adapter, subject.stable_action_id, RuntimeCommand.START)
    assert isinstance(started, _RuntimeFailure)
    assert adapter._actions[subject.stable_action_id]["pending_start"] is True
    absent = adapter.observe(subject.stable_action_id)
    assert isinstance(absent, _RuntimeFailure)
    assert absent.code == "RUNTIME_MATERIALIZATION_PENDING"
    assert sum(command[0] == "run" for command in client.commands) == 1

    durable = json.loads(state_path.read_text(encoding="utf-8"))
    durable["actions"][subject.stable_action_id].pop("pending_start", None)
    state_path.write_bytes(gateway_module.canonical_bytes(durable))
    commands_before = deepcopy(client.commands)

    with pytest.raises(RuntimeGatewayError) as rejected:
        _PaseoRuntimeProviderAdapter(
            client=client,  # type: ignore[arg-type]
            artifacts=store,
            repository_contexts={
                "owner/repository": RuntimeRepositoryContext(source, "main")
            },
            state_path=state_path,
        )

    assert rejected.value.code == "RUNTIME_STORE_INVALID"
    assert client.commands == commands_before
    assert sum(command[0] == "run" for command in client.commands) == 1


@pytest.mark.parametrize("mutation", ("missing", "none"))
def test_repair_packet_13_bound_agent_binding_marker_prevents_prepared_downgrade(
    tmp_path,
    mutation,
):
    """Deleting a confirmed binding must fail before a hidden label can re-START."""
    store, source, _workspace, client, adapter, subject, _spec = _prepared_paseo_adapter(
        tmp_path
    )
    action_id = subject.stable_action_id
    # `_prepared_paseo_adapter` has dispatched START; this readback commits
    # its immutable post-dispatch binding evidence.
    bound = adapter.observe(action_id)
    assert not isinstance(bound, _RuntimeFailure)
    assert adapter._actions[action_id]["binding_established"] is True
    durable = json.loads(adapter._state_path.read_text(encoding="utf-8"))
    record = durable["actions"][action_id]
    if mutation == "missing":
        record.pop("bound_agent_id", None)
    else:
        record["bound_agent_id"] = None
    adapter._state_path.write_bytes(gateway_module.canonical_bytes(durable))
    client.hide_agent_queries = 1
    commands_before = deepcopy(client.commands)

    with pytest.raises(RuntimeGatewayError) as rejected:
        _PaseoRuntimeProviderAdapter(
            client=client,  # type: ignore[arg-type]
            artifacts=store,
            repository_contexts={
                "owner/repository": RuntimeRepositoryContext(source, "main")
            },
            state_path=adapter._state_path,
        )

    assert rejected.value.code == "RUNTIME_STORE_INVALID"
    assert client.commands == commands_before
    assert sum(command[0] == "run" for command in client.commands) == 1


def test_repair_packet_13_paseo_parked_state_survives_restart_then_resumes(tmp_path):
    """A normal park readback is closed-schema durable, not a later poison row."""
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

    # The prepared helper has already established one running Agent.
    parked_command = _adapter_command(adapter, action_id, RuntimeCommand.PARK)
    assert not isinstance(parked_command, _RuntimeFailure)
    parked = adapter.observe(action_id)
    assert not isinstance(parked, _RuntimeFailure)
    assert parked.lifecycle == "parked"
    assert adapter._actions[action_id]["parked"] is True

    restarted = _PaseoRuntimeProviderAdapter(
        client=client,  # type: ignore[arg-type]
        artifacts=store,
        repository_contexts={
            "owner/repository": RuntimeRepositoryContext(source, "main")
        },
        state_path=adapter._state_path,
    )
    after_restart = restarted.observe(action_id)
    assert not isinstance(after_restart, _RuntimeFailure)
    assert after_restart.lifecycle == "parked"

    resumed = _adapter_command(restarted, action_id, RuntimeCommand.RESUME)
    assert not isinstance(resumed, _RuntimeFailure)
    running = restarted.observe(action_id)
    assert not isinstance(running, _RuntimeFailure)
    assert running.lifecycle == "running"
    assert restarted._actions[action_id]["parked"] is False
    assert restarted._actions[action_id]["pending_resume"] is False


def test_repair_packet_13_bound_result_path_is_rejected_before_filesystem_touch(
    tmp_path,
    monkeypatch,
):
    _store, _source, _workspace, _client, adapter, subject, _spec = (
        _prepared_paseo_adapter(tmp_path)
    )
    adapter._transact(
        lambda state: state["actions"][subject.stable_action_id].__setitem__(
            "result_file", r"\\untrusted-host\share\result.json"
        )
    )
    calls: list[str] = []

    def forbidden_is_file(path):
        calls.append(str(path))
        raise AssertionError("untrusted journal path reached Path.is_file")

    monkeypatch.setattr(Path, "is_file", forbidden_is_file)
    observed = adapter._reconcile_observation(subject.stable_action_id)
    verdict = gateway_module._ObservationProtocol.validate(
        observed,
        selected_stable_action_id=subject.stable_action_id,
    )

    assert verdict.kind == "failure"
    assert verdict.failure.code == "RUNTIME_WORKSPACE_UNSAFE"
    assert calls == []


def test_repair_packet_13_bound_workspace_path_family_is_rejected_before_filesystem_touch(
    tmp_path,
    monkeypatch,
):
    _store, _source, _workspace, _client, adapter, subject, _spec = (
        _prepared_paseo_adapter(tmp_path)
    )
    action_id = subject.stable_action_id
    unc_root = r"\\untrusted-host\share\runtime"

    def tamper_paths(state):
        record = state["actions"][action_id]
        action_digest = digest_value(
            {
                "repository": subject.repository,
                "stable_action_id": action_id,
            }
        )
        record["workspace_path"] = unc_root
        record["prompt_file"] = (
            unc_root
            + rf"\.gwo\runtime-artifacts\{record['prompt_artifact_digest']}.json"
        )
        record["result_file"] = (
            unc_root + rf"\.gwo\runtime-results\{action_digest}.json"
        )
        record["output_schema_file"] = (
            unc_root + rf"\.gwo\runtime-schemas\{action_digest}.json"
        )
        record["input_files"] = {
            digest: unc_root + rf"\.gwo\runtime-artifacts\{digest}.json"
            for digest in record["input_artifact_digests"]
        }

    adapter._transact(tamper_paths)
    resolve_calls: list[str] = []
    is_file_calls: list[str] = []
    lstat_calls: list[str] = []
    native_resolve = Path.resolve
    native_lstat = gateway_module.os.lstat

    def forbidden_resolve(path, *args, **kwargs):
        resolve_calls.append(str(path))
        return native_resolve(path, *args, **kwargs)

    def forbidden_is_file(path):
        is_file_calls.append(str(path))
        raise AssertionError("untrusted journal path reached Path.is_file")

    def tracked_lstat(path, *args, **kwargs):
        lstat_calls.append(str(path))
        return native_lstat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", forbidden_resolve)
    monkeypatch.setattr(Path, "is_file", forbidden_is_file)
    monkeypatch.setattr(gateway_module.os, "lstat", tracked_lstat)
    observed = adapter._reconcile_observation(action_id)
    verdict = gateway_module._ObservationProtocol.validate(
        observed,
        selected_stable_action_id=action_id,
    )

    assert verdict.kind == "failure"
    assert verdict.failure.code == "RUNTIME_IDENTITY_AMBIGUOUS"
    assert not any(path.startswith(unc_root) for path in resolve_calls)
    assert not any(path.startswith(unc_root) for path in is_file_calls)
    assert not any(path.startswith(unc_root) for path in lstat_calls)


def test_repair_packet_13_configuration_rejects_canonical_selector_collisions():
    profile = _profile()

    with pytest.raises(RuntimeGatewayError) as rejected:
        RuntimeConfiguration(
            profiles={profile.digest: profile},
            host_mappings={
                "worker": ProfileMapping(profile.digest),
                gateway_module.RuntimeSelector.worker(): ProfileMapping(profile.digest),
            },
        )

    assert rejected.value.code == "RUNTIME_CONFIGURATION_INVALID"


def test_repair_packet_13_work_run_purpose_requires_exact_strings_and_cannot_reinitialize():
    class TextSubclass(str):
        pass

    with pytest.raises(RuntimeGatewayError) as malformed:
        gateway_module.WorkRunPurpose(TextSubclass("implementation"))
    assert malformed.value.code == "RUNTIME_SUBJECT_INVALID"

    purpose = gateway_module.WorkRunPurpose.implementation()
    with pytest.raises(RuntimeGatewayError) as reentered:
        purpose.__init__("formal_review")
    assert reentered.value.code == "RUNTIME_SUBJECT_INVALID"
    assert purpose.canonical() == {"kind": "implementation", "policy_id": None}


def test_repair_packet_16_gateway_transition_uses_the_exact_public_keyword(tmp_path):
    assert tuple(inspect.signature(RuntimeGateway.transition).parameters) == (
        "self",
        "stable_action_id",
        "transition",
    )
    gateway, _store, _adapter = _gateway(tmp_path)

    with pytest.raises(RuntimeGatewayError) as unknown:
        gateway.transition(
            stable_action_id="missing:action",
            transition=RuntimeCommand.START,
        )

    assert unknown.value.code == "RUNTIME_ACTION_UNKNOWN"


def test_repair_packet_16_concurrent_memory_prepare_has_one_linearized_effect(
    tmp_path,
    monkeypatch,
):
    store = ArtifactStore(tmp_path / "artifacts", maximum_bytes=300_000)
    subject = _put_subject_artifacts(store, _subject())
    prompt = store.get(subject.planning_request_artifact_digest)
    spec = _RuntimeActionSpec(
        subject.stable_action_id,
        subject,
        _profile(),
        prompt,
        (prompt,),
    )
    adapter = _InMemoryRuntimeProviderAdapter(store)
    contenders = 8
    query_barrier = threading.Barrier(contenders)
    native_read_bytes = store.read_bytes

    def synchronize_after_absence_query(digest):
        try:
            query_barrier.wait(0.5)
        except threading.BrokenBarrierError:
            pass
        return native_read_bytes(digest)

    monkeypatch.setattr(store, "read_bytes", synchronize_after_absence_query)
    results: list[object] = []
    workers = [
        threading.Thread(target=lambda: results.append(adapter.prepare(spec)))
        for _ in range(contenders)
    ]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(10)

    assert all(not worker.is_alive() for worker in workers)
    assert len(results) == contenders
    assert all(
        type(result) is gateway_module._PrepareReceipt for result in results
    )
    assert adapter.prepare_calls == [subject.stable_action_id]
    assert adapter.staged_prompt_count == 1
    assert len(adapter._actions) == 1

    started = _adapter_command(adapter, subject.stable_action_id, RuntimeCommand.START)
    assert type(started) is _CommandReceipt
    completed = adapter.observe(subject.stable_action_id)
    assert not isinstance(completed, _RuntimeFailure)
    assert completed.lifecycle == "completed"
    binding_ref = completed.binding_ref

    replay = adapter.prepare(spec)
    after_replay = adapter.observe(subject.stable_action_id)

    assert type(replay) is gateway_module._PrepareReceipt
    assert not isinstance(after_replay, _RuntimeFailure)
    assert after_replay.lifecycle == "completed"
    assert after_replay.binding_ref == binding_ref
    assert adapter.created_agent_count == 1


@pytest.mark.parametrize(
    "mutation",
    (
        "unknown_lifecycle",
        "invalid_prompt_digest",
        "changed_prompt_digest",
        "incomplete_binding_identity",
        "completed_without_output",
        "running_with_output",
        "unobserved_bound_state",
        "bound_without_history",
        "snapshot_digest_mismatch",
        "snapshot_lifecycle_mismatch",
        "fallback_selected",
        "missing_output_proof",
    ),
)
def test_repair_packet_16_gateway_loader_rejects_impossible_action_state_before_adapter_read(
    tmp_path,
    mutation,
):
    gateway, store, _adapter = _gateway(tmp_path)
    subject = _put_subject_artifacts(store, _subject())
    preflight = gateway.planning_preflight(subject)
    completed = gateway.progress(subject, preflight)
    assert completed.status == "completed"
    journal_path = tmp_path / "gateway.journal"
    durable = json.loads(journal_path.read_text(encoding="utf-8"))
    record = durable["actions"][subject.stable_action_id]

    if mutation == "unknown_lifecycle":
        record["lifecycle"] = "provider-invented"
    elif mutation == "invalid_prompt_digest":
        record["prompt_artifact_digest"] = "not-a-digest"
    elif mutation == "changed_prompt_digest":
        record["prompt_artifact_digest"] = "f" * 64
    elif mutation == "incomplete_binding_identity":
        record["session_id"] = None
    elif mutation == "completed_without_output":
        record["planning_output_artifact_digest"] = None
    elif mutation == "running_with_output":
        record["lifecycle"] = "running"
    elif mutation == "unobserved_bound_state":
        record["materialization_observed"] = False
    elif mutation == "bound_without_history":
        record["ever_bound"] = False
    elif mutation == "snapshot_digest_mismatch":
        record["observation_digest"] = "f" * 64
    elif mutation == "snapshot_lifecycle_mismatch":
        record["last_observation"]["lifecycle"] = "retired"
        record["observation_digest"] = digest_value(
            record["last_observation"]
        )
    elif mutation == "fallback_selected":
        record["fallback_selected"] = True
        assignment = {
            key: record[key]
            for key in gateway_module._ASSIGNMENT_RECORD_KEYS
        }
        record["assignment_digest"] = gateway_module._assignment_digest(
            subject,
            assignment,
        )
    elif mutation == "missing_output_proof":
        record["planning_output_artifact_digest"] = "f" * 64
        record["last_observation"][
            "planning_output_artifact_digest"
        ] = "f" * 64
        record["observation_digest"] = digest_value(
            record["last_observation"]
        )
    else:
        raise AssertionError(f"unknown mutation {mutation}")
    journal_path.write_bytes(gateway_module.canonical_bytes(durable))
    tracking_adapter = _InMemoryRuntimeProviderAdapter(store)

    with pytest.raises(RuntimeGatewayError) as rejected:
        RuntimeGateway(
            store_path=journal_path,
            _adapter=tracking_adapter,
            configuration=gateway._configuration,
            _artifacts=store,
        )

    assert rejected.value.code == "RUNTIME_STORE_INVALID"
    assert (
        tracking_adapter.prepare_calls,
        tracking_adapter.observe_calls,
        tracking_adapter.command_calls,
    ) == ([], [], [])


def test_repair_packet_16_gateway_loader_reverse_binds_preflight_assignment(
    tmp_path,
):
    store = ArtifactStore(tmp_path / "artifacts", maximum_bytes=300_000)
    profile = _profile()
    configuration = _configuration_with_worker(profile)
    journal_path = tmp_path / "gateway.journal"
    gateway = RuntimeGateway(
        store_path=journal_path,
        _adapter=_InMemoryRuntimeProviderAdapter(store),
        configuration=configuration,
        _artifacts=store,
    )
    planning = _put_subject_artifacts(store, _subject())
    gateway.planning_preflight(planning)
    durable = json.loads(journal_path.read_text(encoding="utf-8"))
    preflight = durable["preflights"][planning.stable_action_id]
    preflight["assignment"]["selector"] = "worker"
    replacement = {
        **preflight["assignment"],
        "fallback_selected": False,
    }
    preflight["assignment_digest"] = gateway_module._assignment_digest(
        planning,
        replacement,
    )
    preflight["receipt_digest"] = gateway_module._preflight_receipt_digest(
        planning.stable_action_id,
        preflight,
    )
    durable["campaigns"][planning.campaign_handle][
        "preflight_bindings"
    ][planning.stable_action_id]["assignment_digest"] = preflight[
        "assignment_digest"
    ]
    journal_path.write_bytes(gateway_module.canonical_bytes(durable))
    tracking_adapter = _InMemoryRuntimeProviderAdapter(store)

    with pytest.raises(RuntimeGatewayError) as rejected:
        RuntimeGateway(
            store_path=journal_path,
            _adapter=tracking_adapter,
            configuration=configuration,
            _artifacts=store,
        )

    assert rejected.value.code == "RUNTIME_STORE_INVALID"
    assert (
        tracking_adapter.prepare_calls,
        tracking_adapter.observe_calls,
        tracking_adapter.command_calls,
    ) == ([], [], [])


def test_repair_packet_16_gateway_v1_migrates_atomically_without_adapter_read(
    tmp_path,
):
    store = ArtifactStore(tmp_path / "artifacts", maximum_bytes=300_000)
    profile = _profile()
    configuration = _configuration_with_worker(profile)
    adapter = _InMemoryRuntimeProviderAdapter(store)
    journal_path = tmp_path / "gateway.journal"
    gateway = RuntimeGateway(
        store_path=journal_path,
        _adapter=adapter,
        configuration=configuration,
        _artifacts=store,
    )
    planning = _put_subject_artifacts(store, _subject())
    preflight = gateway.planning_preflight(planning)
    work = _put_work_subject_artifacts(
        store,
        planning,
        stable_action_id="work:legacy-v1",
    )
    first = gateway.progress(work)
    assert first.status == "completed"
    durable = json.loads(journal_path.read_text(encoding="utf-8"))
    durable["schema_version"] = 1
    durable.pop("action_identities")
    journal_path.write_bytes(gateway_module.canonical_bytes(durable))
    operations_before = (
        list(adapter.prepare_calls),
        list(adapter.observe_calls),
        list(adapter.command_calls),
    )

    restarted = RuntimeGateway(
        store_path=journal_path,
        _adapter=adapter,
        configuration=configuration,
        _artifacts=store,
    )

    assert (
        adapter.prepare_calls,
        adapter.observe_calls,
        adapter.command_calls,
    ) == operations_before
    migrated = json.loads(journal_path.read_text(encoding="utf-8"))
    assert migrated["schema_version"] == 2
    assert migrated["action_identities"] == {
        planning.stable_action_id: {
            "subject_kind": "campaign_planning",
            "subject_digest": planning.digest,
        },
        work.stable_action_id: {
            "subject_kind": "work_run",
            "subject_digest": work.digest,
        },
    }
    assert restarted.planning_preflight(planning) == preflight
    created_before = adapter.created_agent_count
    replay = restarted.progress(work)
    assert replay.status == "completed"
    assert adapter.created_agent_count == created_before


def test_repair_packet_16_gateway_v1_conflict_fails_without_rewrite_or_adapter_read(
    tmp_path,
):
    store = ArtifactStore(tmp_path / "artifacts", maximum_bytes=300_000)
    profile = _profile()
    configuration = _configuration_with_worker(profile)
    journal_path = tmp_path / "gateway.journal"
    gateway = RuntimeGateway(
        store_path=journal_path,
        _adapter=_InMemoryRuntimeProviderAdapter(store),
        configuration=configuration,
        _artifacts=store,
    )
    planning = _put_subject_artifacts(store, _subject())
    gateway.planning_preflight(planning)
    work = _put_work_subject_artifacts(
        store,
        planning,
        stable_action_id="work:legacy-conflict-source",
    )
    gateway.progress(work)
    durable = json.loads(journal_path.read_text(encoding="utf-8"))
    conflicting_work = replace(
        work,
        stable_action_id=planning.stable_action_id,
    )
    action = durable["actions"].pop(work.stable_action_id)
    action["subject"] = conflicting_work.canonical()
    action["subject_digest"] = conflicting_work.digest
    assignment = {
        key: action[key] for key in gateway_module._ASSIGNMENT_RECORD_KEYS
    }
    action["assignment_digest"] = gateway_module._assignment_digest(
        conflicting_work,
        assignment,
    )
    durable["actions"][planning.stable_action_id] = action
    durable["schema_version"] = 1
    durable.pop("action_identities")
    legacy_bytes = gateway_module.canonical_bytes(durable)
    journal_path.write_bytes(legacy_bytes)
    tracking_adapter = _InMemoryRuntimeProviderAdapter(store)

    with pytest.raises(RuntimeGatewayError) as rejected:
        RuntimeGateway(
            store_path=journal_path,
            _adapter=tracking_adapter,
            configuration=configuration,
            _artifacts=store,
        )

    assert rejected.value.code == "RUNTIME_STORE_INVALID"
    assert journal_path.read_bytes() == legacy_bytes
    assert (
        tracking_adapter.prepare_calls,
        tracking_adapter.observe_calls,
        tracking_adapter.command_calls,
    ) == ([], [], [])


def test_repair_packet_16_paseo_terminal_marker_requires_its_wake_digest(
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
    bound = adapter.observe(action_id)
    assert not isinstance(bound, _RuntimeFailure)
    assert bound.binding_ref is not None
    state_path = adapter._state_path
    durable = json.loads(state_path.read_text(encoding="utf-8"))
    durable["actions"][action_id]["wake_terminal_emitted"] = True
    durable["actions"][action_id]["wake_state_digest"] = None
    state_path.write_bytes(gateway_module.canonical_bytes(durable))
    commands_before = deepcopy(client.commands)

    event_read = adapter.events(None)

    assert isinstance(event_read, _RuntimeFailure)
    assert event_read.code == "RUNTIME_STORE_INVALID"
    assert client.commands == commands_before
    with pytest.raises(RuntimeGatewayError) as restarted:
        _PaseoRuntimeProviderAdapter(
            client=client,  # type: ignore[arg-type]
            artifacts=store,
            repository_contexts={
                "owner/repository": RuntimeRepositoryContext(source, "main")
            },
            state_path=state_path,
        )
    assert restarted.value.code == "RUNTIME_STORE_INVALID"
    assert client.commands == commands_before


@pytest.mark.parametrize(
    "mutation",
    (
        "digest_mismatch",
        "nonterminal_snapshot",
        "pending_effect",
        "unknown_event_action",
        "unknown_event_kind",
        "action_intent_overlap",
    ),
)
def test_repair_packet_16_paseo_loader_closes_terminal_wake_state(
    tmp_path,
    mutation,
):
    (
        store,
        source,
        _workspace,
        client,
        adapter,
        subject,
        spec,
    ) = _prepared_paseo_adapter(tmp_path)
    action_id = subject.stable_action_id
    retired = _adapter_command(
        adapter,
        action_id,
        RuntimeCommand.RETIRE,
    )
    assert not isinstance(retired, _RuntimeFailure)
    page = adapter.events(None)
    assert not isinstance(page, _RuntimeFailure)
    record = adapter._actions[action_id]
    assert record["wake_terminal_emitted"] is True
    assert record["wake_state"]["lifecycle"] == "retired"
    state_path = adapter._state_path
    durable = json.loads(state_path.read_text(encoding="utf-8"))
    durable_record = durable["actions"][action_id]

    if mutation == "digest_mismatch":
        durable_record["wake_state_digest"] = "f" * 64
    elif mutation == "nonterminal_snapshot":
        durable_record["wake_state"]["lifecycle"] = "running"
        durable_record["wake_state_digest"] = digest_value(
            durable_record["wake_state"]
        )
    elif mutation == "pending_effect":
        durable_record["pending_retire"] = True
    elif mutation == "unknown_event_action":
        durable["events"][-1]["stable_action_id"] = "missing:action"
    elif mutation == "unknown_event_kind":
        durable["events"][-1]["kind"] = "state:provider-invented"
    elif mutation == "action_intent_overlap":
        durable["workspace_intents"][action_id] = {
            "repository_path": str(source.resolve()),
            "base_commit": durable_record["workspace_base_commit"],
            "slug": durable_record["workspace_slug"],
            "branch": f"gwo-{durable_record['workspace_slug']}",
            "spec_identity_digest": adapter._spec_identity_digest(spec),
            "ownership_nonce": durable_record["workspace_owner_nonce"],
            "layout_version": durable_record["workspace_layout_version"],
            "phase": "recorded",
        }
    else:
        raise AssertionError(f"unknown mutation {mutation}")
    state_path.write_bytes(gateway_module.canonical_bytes(durable))
    commands_before = deepcopy(client.commands)

    rejected = adapter.events(page.next_cursor)

    assert isinstance(rejected, _RuntimeFailure)
    assert rejected.code == "RUNTIME_STORE_INVALID"
    assert client.commands == commands_before
    with pytest.raises(RuntimeGatewayError) as restarted:
        _PaseoRuntimeProviderAdapter(
            client=client,  # type: ignore[arg-type]
            artifacts=store,
            repository_contexts={
                "owner/repository": RuntimeRepositoryContext(source, "main")
            },
            state_path=state_path,
        )
    assert restarted.value.code == "RUNTIME_STORE_INVALID"
    assert client.commands == commands_before


def test_repair_packet_16_paseo_branch_off_create_has_a_stable_new_branch(
    tmp_path,
):
    store = ArtifactStore(tmp_path / "artifacts")
    source, workspace = _repository_worktree(tmp_path)
    client = _RecordingPaseoCli(workspace)
    native_run = client._run
    durable_intents = []

    def enforce_paseo_branch_off_schema(args):
        if args[:2] == ["workspace", "create"]:
            if "--new-branch" not in args:
                raise RuntimeGatewayError(
                    "RUNTIME_VENDOR_ARGUMENT_INVALID",
                    "branch-off create omitted its deterministic branch identity",
                )
            branch = args[args.index("--new-branch") + 1]
            assert branch == "gwo-869429569d94fc6a5e8f63ab"
            durable_intents.append(
                json.loads(
                    (tmp_path / "paseo-actions.json").read_text(
                        encoding="utf-8"
                    )
                )["workspace_intents"]
            )
        return native_run(args)

    client._run = enforce_paseo_branch_off_schema  # type: ignore[method-assign]
    adapter = _PaseoRuntimeProviderAdapter(
        client=client,  # type: ignore[arg-type]
        artifacts=store,
        repository_contexts={
            "owner/repository": RuntimeRepositoryContext(source, "main")
        },
        state_path=tmp_path / "paseo-actions.json",
    )
    subject = _put_subject_artifacts(store, _subject())
    prompt = store.get(subject.planning_request_artifact_digest)

    prepared = adapter.prepare(
        _RuntimeActionSpec(
            subject.stable_action_id,
            subject,
            _profile(),
            prompt,
            (prompt,),
        )
    )

    assert type(prepared) is gateway_module._PrepareReceipt
    assert adapter.observe(subject.stable_action_id).lifecycle == "prepared"
    create = next(
        command
        for command in client.commands
        if command[:2] == ["workspace", "create"]
    )
    assert create[create.index("--mode") + 1] == "branch-off"
    assert create[create.index("--new-branch") + 1] == (
        "gwo-869429569d94fc6a5e8f63ab"
    )
    assert durable_intents == [
        {
            subject.stable_action_id: {
                "repository_path": str(source.resolve()),
                "base_commit": adapter._actions[
                    subject.stable_action_id
                ]["workspace_base_commit"],
                "slug": "869429569d94fc6a5e8f63ab",
                "branch": "gwo-869429569d94fc6a5e8f63ab",
                "spec_identity_digest": adapter._spec_identity_digest(
                    _RuntimeActionSpec(
                        subject.stable_action_id,
                        subject,
                        _profile(),
                        prompt,
                        (prompt,),
                    )
                ),
                "ownership_nonce": adapter._actions[
                    subject.stable_action_id
                ]["workspace_owner_nonce"],
                "layout_version": gateway_module._RUNTIME_WORKSPACE_LAYOUT_VERSION,
                "phase": "create_pending",
            }
        }
    ]
    assert Path(client.workspaces[0]["cwd"]).name == (
        "869429569d94fc6a5e8f63ab"
    )
    assert subject.stable_action_id not in adapter._workspace_intents


def test_repair_packet_16_paseo_ignores_unrelated_registry_duplicates(
    tmp_path,
):
    store = ArtifactStore(tmp_path / "artifacts")
    source, workspace = _repository_worktree(tmp_path)
    client = _RecordingPaseoCli(workspace)
    unrelated = tmp_path / "unrelated-workspace"
    client.workspaces = [
        {
            "workspaceId": "workspace:unrelated",
            "name": "unrelated-slug",
            "isolation": "worktree",
            "cwd": str(unrelated),
        },
        {
            "workspaceId": "workspace:unrelated",
            "name": "unrelated-slug",
            "isolation": "worktree",
            "cwd": str(unrelated),
        },
    ]
    adapter = _PaseoRuntimeProviderAdapter(
        client=client,  # type: ignore[arg-type]
        artifacts=store,
        repository_contexts={
            "owner/repository": RuntimeRepositoryContext(source, "main")
        },
        state_path=tmp_path / "paseo-actions.json",
    )
    subject = _put_subject_artifacts(store, _subject())
    prompt = store.get(subject.planning_request_artifact_digest)

    prepared = adapter.prepare(
        _RuntimeActionSpec(
            subject.stable_action_id,
            subject,
            _profile(),
            prompt,
            (prompt,),
        )
    )

    assert type(prepared) is gateway_module._PrepareReceipt
    assert sum(
        command[:2] == ["workspace", "create"]
        for command in client.commands
    ) == 1


def test_repair_packet_16_bound_result_leaf_is_lstat_checked_before_path_touch(
    tmp_path,
    monkeypatch,
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
    result_path = Path(
        adapter._actions[subject.stable_action_id]["result_file"]
    )
    outside = tmp_path / "outside-result.json"
    outside.write_text("{}", encoding="utf-8")
    os.symlink(outside, result_path)
    touches: list[str] = []
    native_is_file = Path.is_file
    native_stat = Path.stat
    native_open = Path.open
    native_resolve = Path.resolve

    def track(name, operation):
        def tracked(path, *args, **kwargs):
            if Path(path) == result_path:
                touches.append(name)
            return operation(path, *args, **kwargs)

        return tracked

    monkeypatch.setattr(Path, "is_file", track("is_file", native_is_file))
    monkeypatch.setattr(Path, "stat", track("stat", native_stat))
    monkeypatch.setattr(Path, "open", track("open", native_open))
    monkeypatch.setattr(Path, "resolve", track("resolve", native_resolve))

    observed = adapter.observe(subject.stable_action_id)

    assert isinstance(observed, _RuntimeFailure)
    assert observed.code == "RUNTIME_WORKSPACE_UNSAFE"
    assert touches == []


def test_repair_packet_16_gateway_persists_a_cross_bound_observation_snapshot(
    tmp_path,
):
    gateway, store, adapter = _gateway(tmp_path)
    subject = _put_subject_artifacts(store, _subject())
    preflight = gateway.planning_preflight(subject)
    completed = gateway.progress(subject, preflight)
    assert completed.status == "completed"
    journal_path = tmp_path / "gateway.journal"
    durable = json.loads(journal_path.read_text(encoding="utf-8"))
    record = durable["actions"][subject.stable_action_id]

    assert record["ever_bound"] is True
    assert type(record["last_observation"]) is dict
    assert record["observation_digest"] == digest_value(
        record["last_observation"]
    )
    assert record["last_observation"]["stable_action_id"] == (
        subject.stable_action_id
    )
    assert record["last_observation"]["lifecycle"] == record["lifecycle"]
    assert record["last_observation"]["binding_ref"] == record["binding_ref"]
    assert record["last_observation"]["workspace_id"] == record["workspace_id"]
    assert record["last_observation"][
        "planning_output_artifact_digest"
    ] == record["planning_output_artifact_digest"]

    record["lifecycle"] = "prepared"
    journal_path.write_bytes(gateway_module.canonical_bytes(durable))
    operations_before = (
        list(adapter.prepare_calls),
        list(adapter.observe_calls),
        list(adapter.command_calls),
    )

    with pytest.raises(RuntimeGatewayError) as rejected:
        RuntimeGateway(
            store_path=journal_path,
            _adapter=adapter,
            configuration=gateway._configuration,
            _artifacts=store,
        )

    assert rejected.value.code == "RUNTIME_STORE_INVALID"
    assert (
        adapter.prepare_calls,
        adapter.observe_calls,
        adapter.command_calls,
    ) == operations_before


def test_repair_packet_17_missing_completed_output_blocks_another_preflight_without_journal_mutation(
    tmp_path,
):
    gateway, store, _adapter = _gateway(tmp_path)
    completed_subject = _put_subject_artifacts(
        store,
        replace(_subject(), stable_action_id="planning:packet17-completed"),
    )
    completed_preflight = gateway.planning_preflight(completed_subject)
    completed = gateway.progress(completed_subject, completed_preflight)
    assert completed.planning_output_artifact_digest is not None

    store.path_for(completed.planning_output_artifact_digest).unlink()
    journal_before = gateway._store_path.read_bytes()
    later_subject = _put_subject_artifacts(
        store,
        replace(_subject(), stable_action_id="planning:packet17-later"),
    )

    with pytest.raises(RuntimeGatewayError) as rejected:
        gateway.planning_preflight(later_subject)

    assert rejected.value.code == "RUNTIME_ARTIFACT_MISSING"
    assert gateway._store_path.read_bytes() == journal_before


@pytest.mark.parametrize("adapter_kind", ("memory", "paseo"))
def test_repair_packet_17_command_gate_isolated_by_thread_lifecycle_not_reused_ident(
    tmp_path,
    monkeypatch,
    adapter_kind,
):
    """A new thread lifecycle needs its own fresh observe-gate.

    The current-thread lookup below deterministically models a recycled native
    thread id by changing only the lifecycle object while all calls occur on
    this test's one native thread.  This avoids relying on OS id reuse.
    """

    store = ArtifactStore(tmp_path / "artifacts", maximum_bytes=300_000)
    subject = _put_subject_artifacts(store, _subject())
    prompt = store.get(subject.planning_request_artifact_digest)
    spec = _RuntimeActionSpec(
        subject.stable_action_id,
        subject,
        _profile(),
        prompt,
        (prompt,),
    )
    client = None
    if adapter_kind == "memory":
        adapter = _InMemoryRuntimeProviderAdapter(store)
    else:
        source, workspace = _repository_worktree(tmp_path)
        client = _RecordingPaseoCli(workspace)
        adapter = _PaseoRuntimeProviderAdapter(
            client=client,  # type: ignore[arg-type]
            artifacts=store,
            repository_contexts={
                "owner/repository": RuntimeRepositoryContext(source, "main")
            },
            state_path=tmp_path / "paseo-actions.json",
        )
    assert type(adapter.prepare(spec)) is gateway_module._PrepareReceipt

    first_lifecycle = threading.Thread()
    second_lifecycle = threading.Thread()
    current_lifecycle = first_lifecycle
    monkeypatch.setattr(
        gateway_module.threading,
        "current_thread",
        lambda: current_lifecycle,
    )

    assert type(adapter.observe(subject.stable_action_id)) is gateway_module._PreparedRuntimeObservation
    current_lifecycle = second_lifecycle
    leaked = adapter.command(subject.stable_action_id, RuntimeCommand.START)

    assert type(leaked) is _RuntimeFailure
    assert leaked.code == "RUNTIME_ACTION_STATE_CHANGED"
    if adapter_kind == "memory":
        assert adapter.created_agent_count == 0
    else:
        assert client is not None
        assert not any(args and args[0] == "run" for args in client.commands)

    assert type(adapter.observe(subject.stable_action_id)) is gateway_module._PreparedRuntimeObservation
    started = adapter.command(subject.stable_action_id, RuntimeCommand.START)
    assert type(started) is _CommandReceipt


def _packet18_gateway_with_completed_output_and_running_action(
    tmp_path: Path,
    adapter_kind: str,
    *,
    static_assignment_validator=None,
):
    """Seed an independently completed output plus one live action.

    The completed output is deliberately produced through a different stable
    action.  It models action A becoming incomplete after action B has already
    refreshed the Gateway journal, without relying on a provider-specific
    completion shortcut.
    """

    store = ArtifactStore(tmp_path / "artifacts", maximum_bytes=300_000)
    profile = _profile()
    configuration = RuntimeConfiguration(
        profiles={profile.digest: profile},
        host_mappings={"coordinator": ProfileMapping(profile.digest)},
    )
    journal_path = tmp_path / "gateway.journal"
    completed_subject = _put_subject_artifacts(
        store,
        replace(_subject(), stable_action_id="planning:packet18-completed"),
    )
    seed = RuntimeGateway(
        store_path=journal_path,
        _adapter=_InMemoryRuntimeProviderAdapter(store),
        configuration=configuration,
        _artifacts=store,
    )
    completed_preflight = seed.planning_preflight(completed_subject)
    completed = seed.progress(completed_subject, completed_preflight)
    assert completed.planning_output_artifact_digest is not None

    running_subject = _put_subject_artifacts(
        store,
        replace(_subject(), stable_action_id="planning:packet18-running"),
    )
    client = None
    if adapter_kind == "memory":
        adapter = _InMemoryRuntimeProviderAdapter(
            store,
            pending_permissions={
                running_subject.stable_action_id: (
                    ("request:packet18", "write", "repository:packet18"),
                )
            },
        )
    else:
        source, workspace = _repository_worktree(tmp_path)
        client = _RecordingPaseoCli(workspace)
        adapter = _PaseoRuntimeProviderAdapter(
            client=client,  # type: ignore[arg-type]
            artifacts=store,
            repository_contexts={
                "owner/repository": RuntimeRepositoryContext(source, "main")
            },
            state_path=tmp_path / "paseo-actions.json",
        )
    gateway = RuntimeGateway(
        store_path=journal_path,
        _adapter=adapter,
        configuration=configuration,
        _artifacts=store,
        _static_assignment_validator=static_assignment_validator,
    )
    running_preflight = gateway.planning_preflight(running_subject)
    running = gateway.progress(running_subject, running_preflight)
    assert running.status == "running"
    return (
        gateway,
        store,
        adapter,
        client,
        completed.planning_output_artifact_digest,
        running_subject,
        running_preflight,
    )


class _Packet18CompletedOutputFault:
    """Delete one completed output at its next authoritative proof."""

    def __init__(
        self,
        *,
        store,
        gateway,
        completed_digest,
        paseo_journal=None,
    ):
        self._store = store
        self._gateway = gateway
        self._completed_digest = completed_digest
        self._paseo_journal = paseo_journal
        self._native_prove = store.prove_runtime_output
        self._armed = False
        self.tripped = False
        self.checkpoint: dict[str, bytes] = {}

    def arm(self):
        self._armed = True

    def __call__(self, digest, *args, **kwargs):
        if self._armed and not self.tripped and digest == self._completed_digest:
            self._armed = False
            self.tripped = True
            self.checkpoint["gateway"] = self._gateway._store_path.read_bytes()
            if self._paseo_journal is not None:
                self.checkpoint["paseo"] = self._paseo_journal.read_bytes()
            self._store.path_for(self._completed_digest).unlink()
        return self._native_prove(digest, *args, **kwargs)


@pytest.mark.parametrize("adapter_kind", ("memory", "paseo"))
@pytest.mark.parametrize("boundary", ("observe", "prepare", "command", "events"))
def test_repair_packet_18_completed_output_loss_blocks_each_adapter_boundary(
    tmp_path,
    monkeypatch,
    adapter_kind,
    boundary,
):
    """Every direct Adapter edge re-proves all older durable outputs first."""

    fault_holder = {}

    def arm_observe_boundary(_subject, _profile):
        fault = fault_holder.get("fault")
        if boundary == "observe" and fault is not None:
            fault.arm()

    (
        gateway,
        store,
        adapter,
        _client,
        completed_digest,
        running_subject,
        running_preflight,
    ) = _packet18_gateway_with_completed_output_and_running_action(
        tmp_path,
        adapter_kind,
        static_assignment_validator=arm_observe_boundary,
    )
    fault = _Packet18CompletedOutputFault(
        store=store,
        gateway=gateway,
        completed_digest=completed_digest,
        paseo_journal=(
            adapter._state_path if adapter_kind == "paseo" else None
        ),
    )
    fault_holder["fault"] = fault
    monkeypatch.setattr(store, "prove_runtime_output", fault)
    edge_counts = {
        "prepare": 0,
        "observe": 0,
        "command": 0,
        "events": 0,
    }

    native_prepare = adapter.prepare
    native_observe = adapter.observe
    native_command = adapter.command
    native_events = adapter.events

    def tracked_prepare(spec):
        edge_counts["prepare"] += 1
        return native_prepare(spec)

    def tracked_observe(stable_action_id):
        edge_counts["observe"] += 1
        result = native_observe(stable_action_id)
        if boundary in {"prepare", "command"}:
            fault.arm()
        return result

    def tracked_command(stable_action_id, command):
        edge_counts["command"] += 1
        return native_command(stable_action_id, command)

    def tracked_events(cursor):
        edge_counts["events"] += 1
        return native_events(cursor)

    monkeypatch.setattr(adapter, "prepare", tracked_prepare)
    monkeypatch.setattr(adapter, "observe", tracked_observe)
    monkeypatch.setattr(adapter, "command", tracked_command)
    monkeypatch.setattr(adapter, "events", tracked_events)

    if boundary == "observe":
        invoke = lambda: gateway.progress(running_subject, running_preflight)
    elif boundary == "prepare":
        preparing_subject = _put_subject_artifacts(
            store,
            replace(_subject(), stable_action_id="planning:packet18-prepare"),
        )
        preparing_preflight = gateway.planning_preflight(preparing_subject)
        invoke = lambda: gateway.progress(preparing_subject, preparing_preflight)
    elif boundary == "command":
        invoke = lambda: gateway.transition(
            running_subject.stable_action_id,
            RuntimeCommand.FENCE,
        )
    else:
        native_record = gateway._record_observation

        def arm_after_reconciliation(record, verdict):
            native_record(record, verdict)
            fault.arm()

        monkeypatch.setattr(gateway, "_record_observation", arm_after_reconciliation)
        invoke = lambda: gateway.progress(running_subject, running_preflight)

    receipt = None
    with pytest.raises(RuntimeGatewayError) as rejected:
        receipt = invoke()

    assert rejected.value.code == "RUNTIME_ARTIFACT_MISSING"
    assert receipt is None
    assert fault.tripped is True
    assert edge_counts[boundary] == 0
    assert gateway._store_path.read_bytes() == fault.checkpoint["gateway"]
    if adapter_kind == "paseo":
        assert adapter._state_path.read_bytes() == fault.checkpoint["paseo"]


@pytest.mark.parametrize(
    "unsafe_path",
    (
        r"\\untrusted-host\share\workspace",
        r"\\?\C:\device-workspace",
        r"\\.\C:\device-workspace",
        r"C:drive-relative",
        r"\root-relative",
        "\u212a:\\workspace",
        "\uff21:\\workspace",
    ),
)
@pytest.mark.parametrize("entry", ("target_registry", "durable_record", "inspect_cwd"))
def test_repair_packet_18_workspace_paths_fail_before_resolve_filesystem_or_git(
    tmp_path,
    monkeypatch,
    unsafe_path,
    entry,
):
    """Registry, durable, and inspect paths are lexical checks before I/O."""

    _store, source, workspace, client, adapter, subject, _spec = (
        _prepared_paseo_adapter(tmp_path)
    )
    record = adapter._actions[subject.stable_action_id]
    slug = record["workspace_slug"]
    workspace_id = record["workspace_id"]
    native_resolve = Path.resolve
    calls: list[tuple[str, str]] = []

    def forbidden(name, operation):
        def wrapped(path, *args, **kwargs):
            rendered = str(path)
            if rendered == unsafe_path or rendered.startswith(unsafe_path + "\\"):
                calls.append((name, rendered))
                raise AssertionError(
                    f"{name} touched an untrusted Workspace path"
                )
            return operation(path, *args, **kwargs)

        return wrapped

    monkeypatch.setattr(Path, "resolve", forbidden("resolve", native_resolve))
    monkeypatch.setattr(Path, "exists", forbidden("exists", Path.exists))
    monkeypatch.setattr(Path, "open", forbidden("open", Path.open))
    monkeypatch.setattr(gateway_module.os, "lstat", forbidden("lstat", os.lstat))

    def forbidden_git(*_args, **_kwargs):
        raise AssertionError("Git reached an untrusted Workspace path")

    monkeypatch.setattr(adapter, "_git_readback", forbidden_git)
    if entry == "target_registry":
        client.workspaces = [
            {
                "workspaceId": workspace_id,
                "name": slug,
                "isolation": "worktree",
                "cwd": unsafe_path,
            }
        ]
        invoke = lambda: adapter._workspace_by_identity(
            slug=slug,
            expected=(workspace_id, str(workspace)),
        )
    elif entry == "durable_record":
        invoke = lambda: adapter._workspace_by_identity(
            slug=slug,
            expected=(workspace_id, unsafe_path),
        )
    else:
        invoke = lambda: adapter._workspace_for_agent(
            record,
            RuntimeRepositoryContext(source, "main"),
            unsafe_path,
        )

    with pytest.raises(RuntimeGatewayError) as rejected:
        invoke()

    assert rejected.value.code == "RUNTIME_IDENTITY_AMBIGUOUS"
    assert calls == []


@pytest.mark.parametrize(
    "unsafe_path",
    (
        r"\\untrusted-host\share\git",
        r"\\?\C:\device-git",
        r"\\.\C:\device-git",
        r"C:drive-relative-git",
        r"\root-relative-git",
        "\u212a:\\git",
        "\uff21:\\git",
    ),
)
def test_repair_packet_18_git_common_dir_rejects_untrusted_readback_before_resolve(
    tmp_path,
    monkeypatch,
    unsafe_path,
):
    """Git must not turn a hostile common-dir readback into path I/O."""

    native_resolve = Path.resolve
    touched: list[str] = []

    def no_untrusted_resolve(path, *args, **kwargs):
        if str(path) == unsafe_path:
            touched.append(str(path))
            raise AssertionError("resolved an untrusted Git common directory")
        return native_resolve(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", no_untrusted_resolve)
    monkeypatch.setattr(
        _PaseoRuntimeProviderAdapter,
        "_git_readback",
        staticmethod(lambda *_args: unsafe_path),
    )

    with pytest.raises(RuntimeGatewayError) as rejected:
        _PaseoRuntimeProviderAdapter._git_common_dir(tmp_path)

    assert rejected.value.code == "RUNTIME_IDENTITY_AMBIGUOUS"
    assert touched == []


def test_repair_packet_18_noop_gateway_transaction_skips_journal_replacement(
    tmp_path,
    monkeypatch,
):
    """A fully verified Gateway no-op keeps real fsync work out of the hot path."""

    gateway, _store, _adapter = _gateway(tmp_path)
    native_replace = gateway._journal.replace_unlocked
    replacements = 0

    def record_replacement(value):
        nonlocal replacements
        replacements += 1
        return native_replace(value)

    monkeypatch.setattr(gateway._journal, "replace_unlocked", record_replacement)
    gateway._transact(lambda _data: None)

    assert replacements == 0


def test_repair_packet_18_noop_paseo_transaction_skips_journal_replacement(
    tmp_path,
    monkeypatch,
):
    """Paseo compares its real persisted projection, including event dataclasses."""

    _store, _source, _workspace, _client, adapter, _subject, _spec = (
        _prepared_paseo_adapter(tmp_path)
    )
    native_replace = adapter._journal.replace_unlocked
    replacements = 0

    def record_replacement(value):
        nonlocal replacements
        replacements += 1
        return native_replace(value)

    monkeypatch.setattr(adapter._journal, "replace_unlocked", record_replacement)
    adapter._transact(lambda _state: None)

    assert replacements == 0


@pytest.mark.parametrize("adapter_kind", ("memory", "paseo"))
def test_repair_packet_18_adapters_share_one_lifecycle_keyed_command_gate(
    tmp_path,
    adapter_kind,
):
    """The shared one-shot gate is cleared by event-only reads and consumes once."""

    store = ArtifactStore(tmp_path / "artifacts", maximum_bytes=300_000)
    subject = _put_subject_artifacts(store, _subject())
    prompt = store.get(subject.planning_request_artifact_digest)
    spec = _RuntimeActionSpec(subject.stable_action_id, subject, _profile(), prompt, (prompt,))
    if adapter_kind == "memory":
        adapter = _InMemoryRuntimeProviderAdapter(
            store,
            pending_permissions={
                subject.stable_action_id: (
                    ("request:packet18-gate", "write", "repository:packet18"),
                )
            },
        )
    else:
        source, workspace = _repository_worktree(tmp_path)
        adapter = _PaseoRuntimeProviderAdapter(
            client=_RecordingPaseoCli(workspace),  # type: ignore[arg-type]
            artifacts=store,
            repository_contexts={
                "owner/repository": RuntimeRepositoryContext(source, "main")
            },
            state_path=tmp_path / "paseo-actions.json",
        )
    assert type(adapter.prepare(spec)) is gateway_module._PrepareReceipt
    assert type(adapter.observe(subject.stable_action_id)) is gateway_module._PreparedRuntimeObservation
    assert not isinstance(adapter.events(None), _RuntimeFailure)
    event_cleared = adapter.command(subject.stable_action_id, RuntimeCommand.START)
    assert type(event_cleared) is _RuntimeFailure
    assert event_cleared.code == "RUNTIME_ACTION_STATE_CHANGED"
    assert type(adapter.observe(subject.stable_action_id)) is gateway_module._PreparedRuntimeObservation
    assert type(adapter.command(subject.stable_action_id, RuntimeCommand.START)) is _CommandReceipt
    consumed = adapter.command(subject.stable_action_id, RuntimeCommand.FENCE)
    assert type(consumed) is _RuntimeFailure
    assert consumed.code == "RUNTIME_ACTION_STATE_CHANGED"
    final = adapter.observe(subject.stable_action_id)
    assert type(final) is gateway_module._BoundRuntimeObservation
    assert final.lifecycle == "running"
    assert final.fenced is False


@pytest.mark.parametrize("entry", ("artifact_store", "workspace_result"))
@pytest.mark.parametrize("variant", ("missing_field", "extra_field", "cross_identity"))
def test_repair_packet_19_runtime_output_proof_rejects_the_same_closedness_drift(
    tmp_path,
    entry,
    variant,
):
    if entry == "artifact_store":
        store = ArtifactStore(tmp_path / "artifacts", maximum_bytes=300_000)
        subject = _put_subject_artifacts(store, _subject())
        adapter = None
        client = None
    else:
        store, _source, _workspace, client, adapter, subject, _spec = (
            _prepared_paseo_adapter(tmp_path)
        )
    output = {
        "schema_version": "gwo.runtime.output.v1",
        "subject_digest": subject.digest,
        "stable_action_id": subject.stable_action_id,
        "authority_digest": subject.authority_digest,
        "payload": {"completed": True},
    }
    if variant == "missing_field":
        output.pop("authority_digest")
    elif variant == "extra_field":
        output["unexpected"] = True
    else:
        output["stable_action_id"] = "planning:packet19-other"

    if entry == "artifact_store":
        reference = store.put_canonical(output)
        with pytest.raises(RuntimeGatewayError) as rejected:
            store.prove_runtime_output(
                reference.digest,
                subject_digest=subject.digest,
                stable_action_id=subject.stable_action_id,
                authority_digest=subject.authority_digest,
            )
        assert rejected.value.detail == (
            "Runtime output Artifact does not bind its exact action"
        )
        error = rejected.value
    else:
        assert adapter is not None
        assert client is not None
        record = adapter._actions[subject.stable_action_id]
        Path(record["result_file"]).write_bytes(
            gateway_module.canonical_bytes(output)
        )
        client.agent.lifecycle = "idle"
        rejected = adapter.observe(subject.stable_action_id)
        assert type(rejected) is _RuntimeFailure
        error = rejected
    assert error.code == "RUNTIME_OUTPUT_ARTIFACT_INVALID"


def test_repair_packet_19_paseo_stages_the_compatible_closed_output_schema(
    tmp_path,
):
    _store, _source, _workspace, _client, adapter, subject, _spec = (
        _prepared_paseo_adapter(tmp_path)
    )
    schema_path = Path(
        adapter._actions[subject.stable_action_id]["output_schema_file"]
    )
    expected = {
        "type": "object",
        "required": [
            "schema_version",
            "subject_digest",
            "stable_action_id",
            "authority_digest",
            "payload",
        ],
        "properties": {
            "schema_version": {"const": "gwo.runtime.output.v1"},
            "subject_digest": {"const": subject.digest},
            "stable_action_id": {"const": subject.stable_action_id},
            "authority_digest": {"const": subject.authority_digest},
            "payload": {},
        },
        "additionalProperties": False,
    }

    assert schema_path.read_bytes() == gateway_module.canonical_bytes(expected)
