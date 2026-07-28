from __future__ import annotations

import json
from pathlib import Path
import sys

import pytest


SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "orchestrator" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from gwo_v8 import (  # noqa: E402
    CampaignPlanningSubject,
    CampaignStartRuntimeOverrides,
    InMemoryRuntimeProviderAdapter,
    ProfileMapping,
    RuntimeConfiguration,
    RuntimeGateway,
    RuntimeGatewayError,
    RuntimeProfile,
    RuntimeActionSpec,
    RuntimeCommand,
    RuntimeSelector,
    WorkRunSubject,
)
from gwo_v8._canonical import digest_value  # noqa: E402
from gwo_v8.runtime_gateway import PaseoRuntimeProviderAdapter  # noqa: E402


def _digest(name: str) -> str:
    return digest_value({"artifact": name})


def _profile(name: str) -> RuntimeProfile:
    return RuntimeProfile(
        name=name,
        provider="test-provider",
        model="test-model",
        thinking="high",
        mode="safe",
        features={},
    )


def _planning_subject() -> CampaignPlanningSubject:
    return CampaignPlanningSubject(
        repository="owner/repository",
        campaign_key="campaign:one",
        campaign_handle="campaign-handle:one",
        expected_previous_plan_revision_digest=None,
        snapshot_artifact_digest=_digest("snapshot"),
        policy_witness_digest=_digest("policy"),
        planning_request_artifact_digest=_digest("planning-request"),
        stable_action_id="planning:campaign:one:initial",
    )


def _config(*, repository: ProfileMapping | None = None) -> RuntimeConfiguration:
    profile = _profile("coordinator")
    return RuntimeConfiguration(
        profiles={profile.digest: profile},
        host_mappings={RuntimeSelector.coordinator(): ProfileMapping(profile.digest)},
        repository_mappings=(
            {} if repository is None else {"owner/repository": {RuntimeSelector.coordinator(): repository}}
        ),
    )


def test_planning_preflight_uses_campaign_override_and_never_materializes(tmp_path):
    primary = _profile("primary")
    fallback = _profile("fallback")
    adapter = InMemoryRuntimeProviderAdapter()
    gateway = RuntimeGateway(
        store_path=tmp_path / "gateway.json",
        adapter=adapter,
        configuration=RuntimeConfiguration(
            profiles={primary.digest: primary, fallback.digest: fallback},
            host_mappings={
                RuntimeSelector.coordinator(): ProfileMapping(primary.digest),
            },
        ),
    )
    subject = _planning_subject()

    receipt = gateway.planning_preflight(
        subject,
        CampaignStartRuntimeOverrides(
            coordinator=ProfileMapping(primary.digest, fallback.digest),
        ),
    )

    assert receipt.subject_digest == subject.digest
    assert receipt.stable_action_id == subject.stable_action_id
    assert adapter.prepare_calls == []
    assert adapter.command_calls == []
    assert adapter.observe(subject.stable_action_id) is None
    assert json.loads((tmp_path / "gateway.json").read_text(encoding="utf-8"))["actions"] == {}
    restarted = RuntimeGateway(
        store_path=tmp_path / "gateway.json",
        adapter=adapter,
        configuration=RuntimeConfiguration(
            profiles={primary.digest: primary, fallback.digest: fallback},
            host_mappings={RuntimeSelector.coordinator(): ProfileMapping(primary.digest)},
        ),
    )
    assert restarted.planning_preflight(subject) == receipt


def test_ticket_override_is_exact_but_cannot_override_coordinator(tmp_path):
    profile = _profile("shared")
    config = RuntimeConfiguration(
        profiles={profile.digest: profile},
        host_mappings={
            RuntimeSelector.coordinator(): ProfileMapping(profile.digest),
            RuntimeSelector.worker(): ProfileMapping(profile.digest),
        },
    )
    adapter = InMemoryRuntimeProviderAdapter()
    gateway = RuntimeGateway(
        store_path=tmp_path / "gateway.json",
        adapter=adapter,
        configuration=config,
    )

    gateway.planning_preflight(
        _planning_subject(),
        CampaignStartRuntimeOverrides(
            ticket_overrides={
                ("issue:111", "worker"): ProfileMapping(profile.digest),
            },
        ),
    )

    work_subject = WorkRunSubject(
        repository="owner/repository",
        campaign_key="campaign:one",
        campaign_handle="campaign-handle:one",
        plan_revision_digest=_digest("revision"),
        work_run_key="work-run:111",
        ticket_key="issue:111",
        role="worker",
        prompt_artifact_digest=_digest("worker-prompt"),
        authority_subtree_digest=_digest("worker-authority"),
        stable_action_id="work-run:111:worker",
    )
    gateway.progress(work_subject)
    assert adapter.observe(work_subject.stable_action_id).profile_digest == profile.digest
    with pytest.raises(RuntimeGatewayError, match="coordinator"):
        CampaignStartRuntimeOverrides(
            ticket_overrides={
                ("issue:111", "coordinator"): ProfileMapping(profile.digest),
            },
        )
    with pytest.raises(RuntimeGatewayError, match="exact Ticket"):
        CampaignStartRuntimeOverrides(
            ticket_overrides={
                ("issue:111", "*"): ProfileMapping(profile.digest),
            },
        )


@pytest.mark.parametrize(
    ("role", "expected_source"),
    [
        ("worker", "campaign"),
        ("recovery_worker", "repository"),
        ("review_primary", "host"),
        ("review_strong", "host"),
        ("specialist:security", "host"),
    ],
)
def test_all_ticket_selectors_follow_exact_override_repository_host_precedence(
    tmp_path,
    role,
    expected_source,
):
    host = _profile("host")
    repository = _profile("repository")
    campaign = _profile("campaign")
    mapping = {selector: ProfileMapping(host.digest) for selector in (
        RuntimeSelector.worker(),
        RuntimeSelector.ticket("recovery_worker"),
        RuntimeSelector.ticket("review_primary"),
        RuntimeSelector.ticket("review_strong"),
        RuntimeSelector.ticket("specialist:security"),
        RuntimeSelector.coordinator(),
    )}
    adapter = InMemoryRuntimeProviderAdapter()
    gateway = RuntimeGateway(
        store_path=tmp_path / "gateway.json",
        adapter=adapter,
        configuration=RuntimeConfiguration(
            profiles={
                host.digest: host,
                repository.digest: repository,
                campaign.digest: campaign,
            },
            host_mappings=mapping,
            repository_mappings={
                "owner/repository": {
                    RuntimeSelector.ticket("recovery_worker"): ProfileMapping(
                        repository.digest
                    ),
                }
            },
        ),
    )
    planning = _planning_subject()
    gateway.planning_preflight(
        planning,
        CampaignStartRuntimeOverrides(
            ticket_overrides={
                ("issue:111", "worker"): ProfileMapping(campaign.digest),
            }
        ),
    )
    work = WorkRunSubject(
        repository=planning.repository,
        campaign_key=planning.campaign_key,
        campaign_handle=planning.campaign_handle,
        plan_revision_digest=_digest("revision"),
        work_run_key=f"work-run:{role}",
        ticket_key="issue:111",
        role=role,
        prompt_artifact_digest=_digest(f"prompt:{role}"),
        authority_subtree_digest=_digest(f"authority:{role}"),
        stable_action_id=f"action:{role}",
    )

    gateway.progress(work)

    profile = adapter.observe(work.stable_action_id).profile_digest
    expected = {
        "campaign": campaign.digest,
        "repository": repository.digest,
        "host": host.digest,
    }[expected_source]
    assert profile == expected


def test_same_profile_can_be_primary_and_fallback_and_assignment_survives_restart(tmp_path):
    profile = _profile("same")
    store = tmp_path / "gateway.json"
    adapter = InMemoryRuntimeProviderAdapter()
    config = RuntimeConfiguration(
        profiles={profile.digest: profile},
        host_mappings={
            RuntimeSelector.coordinator(): ProfileMapping(
                profile.digest,
                profile.digest,
            ),
        },
    )
    subject = _planning_subject()
    first = RuntimeGateway(store_path=store, adapter=adapter, configuration=config)
    receipt = first.planning_preflight(subject)
    first.progress(subject, receipt)
    restarted = RuntimeGateway(store_path=store, adapter=adapter, configuration=config)

    recovered = restarted.progress(subject, receipt)
    persisted = json.loads(store.read_text(encoding="utf-8"))

    assert recovered.planning_output_artifact_digest is not None
    assert persisted["actions"][subject.stable_action_id]["profile_digest"] == profile.digest
    assert (
        persisted["actions"][subject.stable_action_id][
            "availability_fallback_profile_digest"
        ]
        == profile.digest
    )
    assert persisted["actions"][subject.stable_action_id]["fallback_selected"] is False
    assert adapter.created_agent_count == 1


def test_work_run_subject_requires_its_exact_plan_revision_and_work_run_tuple(tmp_path):
    profile = _profile("worker")
    adapter = InMemoryRuntimeProviderAdapter()
    gateway = RuntimeGateway(
        store_path=tmp_path / "gateway.json",
        adapter=adapter,
        configuration=RuntimeConfiguration(
            profiles={profile.digest: profile},
            host_mappings={
                RuntimeSelector.coordinator(): ProfileMapping(profile.digest),
                RuntimeSelector.worker(): ProfileMapping(profile.digest),
            },
        ),
    )
    planning = _planning_subject()
    gateway.planning_preflight(planning)
    work = WorkRunSubject(
        repository=planning.repository,
        campaign_key=planning.campaign_key,
        campaign_handle=planning.campaign_handle,
        plan_revision_digest=_digest("revision-one"),
        work_run_key="work-run:one",
        ticket_key="issue:111",
        role="worker",
        prompt_artifact_digest=_digest("prompt"),
        authority_subtree_digest=_digest("authority"),
        stable_action_id="work-run:stable",
    )
    gateway.progress(work)
    changed_revision = WorkRunSubject(
        repository=work.repository,
        campaign_key=work.campaign_key,
        campaign_handle=work.campaign_handle,
        plan_revision_digest=_digest("revision-two"),
        work_run_key=work.work_run_key,
        ticket_key=work.ticket_key,
        role=work.role,
        prompt_artifact_digest=work.prompt_artifact_digest,
        authority_subtree_digest=work.authority_subtree_digest,
        stable_action_id=work.stable_action_id,
    )

    with pytest.raises(RuntimeGatewayError) as stopped:
        gateway.progress(changed_revision)

    assert stopped.value.code == "RUNTIME_ACTION_IDENTITY_MISMATCH"
    assert adapter.created_agent_count == 1
    with pytest.raises(RuntimeGatewayError, match="Campaign-scoped"):
        WorkRunSubject(
            repository=work.repository,
            campaign_key=work.campaign_key,
            campaign_handle=work.campaign_handle,
            plan_revision_digest=work.plan_revision_digest,
            work_run_key=work.work_run_key,
            ticket_key=work.ticket_key,
            role="coordinator",
            prompt_artifact_digest=work.prompt_artifact_digest,
            authority_subtree_digest=work.authority_subtree_digest,
            stable_action_id="invalid:coordinator",
        )


def test_gateway_rejects_any_materialization_subject_outside_the_closed_union(tmp_path):
    gateway = RuntimeGateway(
        store_path=tmp_path / "gateway.json",
        adapter=InMemoryRuntimeProviderAdapter(),
        configuration=_config(),
    )

    with pytest.raises(RuntimeGatewayError) as stopped:
        gateway.progress(object())

    assert stopped.value.code == "RUNTIME_SUBJECT_INVALID"


def test_event_hints_and_large_artifact_content_never_authorize_or_enter_command_text(tmp_path):
    huge_artifact = _digest("x" * 500_000)
    subject = CampaignPlanningSubject(
        repository="owner/repository",
        campaign_key="campaign:large",
        campaign_handle="campaign-handle:large",
        expected_previous_plan_revision_digest=None,
        snapshot_artifact_digest=_digest("snapshot"),
        policy_witness_digest=_digest("policy"),
        planning_request_artifact_digest=huge_artifact,
        stable_action_id="planning:large",
    )
    adapter = InMemoryRuntimeProviderAdapter()
    gateway = RuntimeGateway(
        store_path=tmp_path / "gateway.json",
        adapter=adapter,
        configuration=_config(),
    )
    receipt = gateway.planning_preflight(subject)

    hints = gateway.wake_hints(None)

    assert hints.events == ()
    assert adapter.prepare_calls == []
    complete = gateway.progress(subject, receipt)
    assert complete.planning_output_artifact_digest is not None
    assert all(len(action) < 128 for action in adapter.prepare_calls)
    assert all(command in {item.value for item in RuntimeCommand} for _, command in adapter.command_calls)


def test_missing_required_configuration_fails_before_planning_action(tmp_path):
    adapter = InMemoryRuntimeProviderAdapter()
    gateway = RuntimeGateway(
        store_path=tmp_path / "gateway.json",
        adapter=adapter,
        configuration=RuntimeConfiguration(profiles={}, host_mappings={}),
    )

    with pytest.raises(RuntimeGatewayError) as stopped:
        gateway.planning_preflight(_planning_subject())

    assert stopped.value.code == "RUNTIME_CONFIGURATION_INVALID"
    assert adapter.prepare_calls == []
    assert adapter.observe_calls == []
    assert adapter.command_calls == []


def test_planning_execution_observes_before_start_and_reuses_the_same_action(tmp_path):
    adapter = InMemoryRuntimeProviderAdapter()
    gateway = RuntimeGateway(
        store_path=tmp_path / "gateway.json",
        adapter=adapter,
        configuration=_config(),
    )
    subject = _planning_subject()
    preflight = gateway.planning_preflight(subject)

    first = gateway.progress(subject, preflight)
    recovered = gateway.progress(subject, preflight)

    assert first.subject_digest == subject.digest
    assert first.planning_output_artifact_digest is not None
    assert recovered.planning_output_artifact_digest == first.planning_output_artifact_digest
    assert adapter.prepare_calls == [subject.stable_action_id]
    assert [command for _binding, command in adapter.command_calls] == ["start"]
    assert adapter.observe_calls[0] == subject.stable_action_id
    assert len(adapter.observe_calls) >= 3


def test_post_prepare_ack_loss_reads_back_and_never_creates_a_second_prompt_or_agent(tmp_path):
    adapter = InMemoryRuntimeProviderAdapter(lose_prepare_ack_once=True)
    gateway = RuntimeGateway(
        store_path=tmp_path / "gateway.json",
        adapter=adapter,
        configuration=_config(),
    )
    subject = _planning_subject()
    preflight = gateway.planning_preflight(subject)

    first = gateway.progress(subject, preflight)
    recovered = gateway.progress(subject, preflight)

    assert first.planning_output_artifact_digest is not None
    assert recovered.planning_output_artifact_digest is not None
    assert adapter.created_agent_count == 1
    assert adapter.staged_prompt_count == 1
    assert adapter.prepare_calls == [subject.stable_action_id]


def test_post_start_output_ack_loss_reads_back_without_a_second_planning_pass(tmp_path):
    adapter = InMemoryRuntimeProviderAdapter(
        lose_command_ack_once=RuntimeCommand.START,
    )
    gateway = RuntimeGateway(
        store_path=tmp_path / "gateway.json",
        adapter=adapter,
        configuration=_config(),
    )
    subject = _planning_subject()
    preflight = gateway.planning_preflight(subject)

    first = gateway.progress(subject, preflight)
    restarted = RuntimeGateway(
        store_path=tmp_path / "gateway.json",
        adapter=adapter,
        configuration=_config(),
    )
    recovered = restarted.progress(subject, preflight)

    assert first.planning_output_artifact_digest == recovered.planning_output_artifact_digest
    assert adapter.created_agent_count == 1
    assert adapter.staged_prompt_count == 1
    assert [command for _binding, command in adapter.command_calls] == ["start"]


def test_adapter_conformance_rejects_implicit_launch_and_bad_observation(tmp_path):
    subject = _planning_subject()
    adapter = InMemoryRuntimeProviderAdapter(
        initial_lifecycles={subject.stable_action_id: "running"},
    )
    gateway = RuntimeGateway(
        store_path=tmp_path / "gateway.json",
        adapter=adapter,
        configuration=_config(),
    )
    preflight = gateway.planning_preflight(subject)

    with pytest.raises(RuntimeGatewayError) as stopped:
        gateway.progress(subject, preflight)

    assert stopped.value.code == "RUNTIME_OBSERVATION_INVALID"
    assert adapter.command_calls == []


class _PaseoNativeBridge:
    """Contract double for the private native Paseo bridge, not Gateway policy."""

    def __init__(self, delegate):
        self.delegate = delegate

    def stage_runtime_action(self, spec):
        return self.delegate.prepare(spec)

    def observe_runtime_action(self, stable_action_id):
        return self.delegate.observe(stable_action_id)

    def send_runtime_command(self, binding_ref, command):
        return self.delegate.command(binding_ref, command)

    def runtime_events(self, after_cursor):
        return self.delegate.events(after_cursor)


@pytest.mark.parametrize("production", [False, True])
def test_production_and_memory_adapters_share_the_closed_command_conformance_suite(
    production,
):
    memory = InMemoryRuntimeProviderAdapter()
    adapter = (
        PaseoRuntimeProviderAdapter(_PaseoNativeBridge(memory))
        if production
        else memory
    )
    profile = _profile("conformance")

    for command in RuntimeCommand:
        subject = replace_planning_action(_planning_subject(), f"planning:{command.value}")
        prepared = adapter.prepare(
            RuntimeActionSpec(
                stable_action_id=subject.stable_action_id,
                subject=subject,
                profile=profile,
                prompt_artifact_digest=subject.planning_request_artifact_digest,
            )
        )
        assert adapter.observe(subject.stable_action_id).lifecycle == "prepared"
        if command is RuntimeCommand.RESUME:
            adapter.command(prepared.binding_ref, RuntimeCommand.PARK)
        receipt = adapter.command(prepared.binding_ref, command)
        assert receipt.command is command

    with pytest.raises(RuntimeGatewayError) as stopped:
        adapter.command(prepared.binding_ref, "launch")
    assert stopped.value.code == "RUNTIME_COMMAND_INVALID"
    assert adapter.events(None).next_cursor is not None


@pytest.mark.parametrize("production", [False, True])
def test_production_and_memory_adapters_share_readback_failure_conformance(
    tmp_path,
    production,
):
    memory = InMemoryRuntimeProviderAdapter(
        lose_prepare_ack_once=True,
        lose_command_ack_once=RuntimeCommand.START,
    )
    adapter = (
        PaseoRuntimeProviderAdapter(_PaseoNativeBridge(memory))
        if production
        else memory
    )
    gateway = RuntimeGateway(
        store_path=tmp_path / ("production.json" if production else "memory.json"),
        adapter=adapter,
        configuration=_config(),
    )
    subject = _planning_subject()
    preflight = gateway.planning_preflight(subject)

    receipt = gateway.progress(subject, preflight)

    assert receipt.status == "completed"
    assert memory.created_agent_count == 1
    assert memory.staged_prompt_count == 1
    assert [command for _binding, command in memory.command_calls] == ["start"]


def replace_planning_action(
    subject: CampaignPlanningSubject,
    stable_action_id: str,
) -> CampaignPlanningSubject:
    return CampaignPlanningSubject(
        repository=subject.repository,
        campaign_key=subject.campaign_key,
        campaign_handle=subject.campaign_handle,
        expected_previous_plan_revision_digest=subject.expected_previous_plan_revision_digest,
        snapshot_artifact_digest=subject.snapshot_artifact_digest,
        policy_witness_digest=subject.policy_witness_digest,
        planning_request_artifact_digest=subject.planning_request_artifact_digest,
        stable_action_id=stable_action_id,
    )
