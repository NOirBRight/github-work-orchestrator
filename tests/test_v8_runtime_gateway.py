from __future__ import annotations

from pathlib import Path
import sys

import pytest


SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "orchestrator" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from gwo_v8 import (  # noqa: E402
    CampaignPlanningSubject,
    RuntimeCommand,
    RuntimeConfiguration,
    RuntimeGateway,
    RuntimeGatewayError,
    WorkRunPurpose,
    WorkRunSubject,
)
from gwo_v8.runtime import RuntimeProfile  # noqa: E402
from gwo_v8.runtime_gateway import (  # noqa: E402
    ArtifactStore,
    CampaignStartRuntimeOverrides,
    ProfileMapping,
    _InMemoryRuntimeProviderAdapter,
)


def _profile(name: str) -> RuntimeProfile:
    return RuntimeProfile(
        name=name,
        provider="test-provider",
        model=f"model:{name}",
        thinking="high",
        mode="safe",
        features={},
    )


def _planning(store: ArtifactStore, *, action: str = "planning:one"):
    snapshot = store.put_canonical({"tickets": ["issue:111"]})
    policy = store.put_canonical({"policy": "frozen"})
    provisional = CampaignPlanningSubject(
        repository="owner/repository",
        campaign_key="campaign:one",
        campaign_handle="handle:one",
        expected_previous_plan_revision_digest=None,
        snapshot_artifact_digest=snapshot.digest,
        policy_witness_digest=policy.digest,
        planning_request_artifact_digest="0" * 64,
        stable_action_id=action,
    )
    prompt = store.put_canonical(
        {
            "schema_version": "gwo.runtime.prompt.v1",
            "subject_digest": provisional.prompt_binding_digest,
            "authority_digest": policy.digest,
            "payload": {"complete_ticket_contracts": ["x" * 100_000]},
        }
    )
    return CampaignPlanningSubject(
        **{
            **provisional.__dict__,
            "planning_request_artifact_digest": prompt.digest,
        }
    )


def _work(
    store: ArtifactStore,
    planning,
    *,
    purpose: WorkRunPurpose,
    action: str,
):
    provisional = WorkRunSubject(
        repository=planning.repository,
        campaign_key=planning.campaign_key,
        campaign_handle=planning.campaign_handle,
        plan_revision_digest=store.put_canonical({"revision": 1}).digest,
        work_run_key=f"work-run:{purpose.kind}",
        ticket_key="issue:111",
        purpose=purpose,
        prompt_artifact_digest="0" * 64,
        authority_subtree_digest=planning.policy_witness_digest,
        stable_action_id=action,
    )
    prompt = store.put_canonical(
        {
            "schema_version": "gwo.runtime.prompt.v1",
            "subject_digest": provisional.prompt_binding_digest,
            "authority_digest": provisional.authority_digest,
            "payload": {"complete_contract": "review context" * 20_000},
        }
    )
    return WorkRunSubject(
        **{**provisional.__dict__, "prompt_artifact_digest": prompt.digest}
    )


def _gateway(tmp_path: Path, configuration: RuntimeConfiguration, **adapter_options):
    artifacts = ArtifactStore(tmp_path / "artifacts", maximum_bytes=1_048_576)
    adapter = _InMemoryRuntimeProviderAdapter(artifacts, **adapter_options)
    return (
        RuntimeGateway(
            store_path=tmp_path / "gateway.journal",
            _adapter=adapter,
            configuration=configuration,
            _artifacts=artifacts,
        ),
        artifacts,
        adapter,
    )


def test_exact_selector_precedence_and_same_profile_fallback_are_persisted(tmp_path):
    host, repository, override = (_profile("host"), _profile("repository"), _profile("override"))
    artifacts = ArtifactStore(tmp_path / "artifacts", maximum_bytes=1_048_576)
    planning = _planning(artifacts)
    assertion_key = (
        planning.repository,
        planning.campaign_key,
        planning.campaign_handle,
    )
    configuration = RuntimeConfiguration(
        profiles={item.digest: item for item in (host, repository, override)},
        host_mappings={
            "coordinator": ProfileMapping(host.digest, host.digest),
            "worker": ProfileMapping(host.digest),
            "recovery_worker": ProfileMapping(host.digest),
            "review_primary": ProfileMapping(host.digest),
            "review_strong": ProfileMapping(host.digest),
            "specialist:security": ProfileMapping(host.digest),
        },
        repository_mappings={
            "owner/repository": {
                "recovery_worker": ProfileMapping(repository.digest),
            }
        },
        campaign_assertions={
            assertion_key: CampaignStartRuntimeOverrides(
                coordinator=ProfileMapping(override.digest, override.digest),
                ticket_overrides={
                    ("issue:111", "worker"): ProfileMapping(override.digest),
                },
            )
        },
    )
    gateway, artifacts, adapter = _gateway(tmp_path, configuration)
    preflight = gateway.planning_preflight(planning)
    gateway.progress(planning, preflight)
    assert adapter.observe(planning.stable_action_id).profile_digest == override.digest

    worker = _work(
        artifacts,
        planning,
        purpose=WorkRunPurpose.implementation(),
        action="work:worker",
    )
    recovery = _work(
        artifacts,
        planning,
        purpose=WorkRunPurpose.terminal_recovery_implementation(),
        action="work:recovery",
    )
    specialist = _work(
        artifacts,
        planning,
        purpose=WorkRunPurpose.specialist_review("security"),
        action="work:specialist",
    )
    gateway.progress(worker)
    gateway.progress(recovery)
    gateway.progress(specialist)
    assert adapter.observe(worker.stable_action_id).profile_digest == override.digest
    assert adapter.observe(recovery.stable_action_id).profile_digest == repository.digest
    assert adapter.observe(specialist.stable_action_id).profile_digest == host.digest


def test_forbidden_shorthand_and_coordinator_ticket_override_fail_closed():
    profile = _profile("one")
    with pytest.raises(RuntimeGatewayError, match="coordinator"):
        CampaignStartRuntimeOverrides(
            ticket_overrides={("issue:111", "coordinator"): ProfileMapping(profile.digest)}
        )
    with pytest.raises(RuntimeGatewayError, match="exact Ticket"):
        CampaignStartRuntimeOverrides(
            ticket_overrides={("issue:111", "*"): ProfileMapping(profile.digest)}
        )


def test_restart_and_ack_loss_reuse_one_action_prompt_and_output(tmp_path):
    profile = _profile("coordinator")
    configuration = RuntimeConfiguration(
        profiles={profile.digest: profile},
        host_mappings={"coordinator": ProfileMapping(profile.digest)},
    )
    gateway, artifacts, adapter = _gateway(
        tmp_path,
        configuration,
        lose_prepare_ack_once=True,
        lose_command_ack_once=RuntimeCommand.START,
    )
    subject = _planning(artifacts)
    preflight = gateway.planning_preflight(subject)
    first = gateway.progress(subject, preflight)
    restarted = RuntimeGateway(
        store_path=tmp_path / "gateway.journal",
        _adapter=adapter,
        configuration=configuration,
        _artifacts=artifacts,
    )
    recovered = restarted.progress(subject, preflight)

    assert recovered.planning_output_artifact_digest == first.planning_output_artifact_digest
    assert adapter.created_agent_count == 1
    assert adapter.staged_prompt_count == 1
    assert [command for _binding, command in adapter.command_calls] == ["start"]


def test_work_run_subject_and_events_cannot_replace_authoritative_readback(tmp_path):
    profile = _profile("one")
    configuration = RuntimeConfiguration(
        profiles={profile.digest: profile},
        host_mappings={"coordinator": ProfileMapping(profile.digest), "worker": ProfileMapping(profile.digest)},
    )
    gateway, artifacts, adapter = _gateway(tmp_path, configuration)
    planning = _planning(artifacts)
    preflight = gateway.planning_preflight(planning)
    gateway.progress(planning, preflight)
    work = _work(
        artifacts,
        planning,
        purpose=WorkRunPurpose.implementation(),
        action="work:one",
    )
    first = gateway.progress(work)
    second = gateway.progress(work, wake_cursor=first.wake_cursor)

    assert first.output_artifact_digest is not None
    assert artifacts.get(first.output_artifact_digest)
    assert not hasattr(first, "provider")
    assert not hasattr(first, "binding_ref")
    # The terminal wake is pageable once; an exact returned cursor must not
    # replay it.  Progress remains authoritative through fresh observation.
    assert second.wake_hints == ()
    assert second.output_artifact_digest == first.output_artifact_digest
    assert adapter.observe_calls.count(work.stable_action_id) >= 2
    changed = WorkRunSubject(
        **{**work.__dict__, "plan_revision_digest": artifacts.put_canonical({"revision": 2}).digest}
    )
    with pytest.raises(RuntimeGatewayError) as stopped:
        gateway.progress(changed)
    assert stopped.value.code == "RUNTIME_ACTION_IDENTITY_MISMATCH"
