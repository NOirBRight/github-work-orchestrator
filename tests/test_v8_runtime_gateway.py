from __future__ import annotations

from pathlib import Path
import sys

import pytest


SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "orchestrator" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from gwo_v8 import (  # noqa: E402
    CampaignPlanningSubject,
    PermissionRequired,
    PermissionResponse,
    RuntimeCommand,
    RuntimeConfiguration,
    RuntimeGateway,
    RuntimeGatewayError,
    WorkRunPurpose,
    WorkRunSubject,
)
from gwo_v8.runtime import RuntimeProfile  # noqa: E402
from gwo_v8.planning_protocol import planning_prompt  # noqa: E402
from gwo_v8.runtime_gateway import (  # noqa: E402
    ArtifactStore,
    CapabilityPolicy,
    CampaignStartRuntimeOverrides,
    ProfileMapping,
    PlanInvalidationReport,
    _FrozenPermissionAuthorityV1,
    _InMemoryRuntimeProviderAdapter,
    _RuntimeFailure,
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
    snapshot = store.put_canonical({"tickets": [{"key": "issue:111"}]})
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
        planning_prompt(
            subject_digest=provisional.prompt_binding_digest,
            authority_digest=provisional.authority_digest,
            snapshot_artifact_digest=snapshot.digest,
            policy_witness_artifact_digest=policy.digest,
        )
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


def _gateway(
    tmp_path: Path,
    configuration: RuntimeConfiguration,
    *,
    authority_readback=None,
    **adapter_options,
):
    artifacts = ArtifactStore(tmp_path / "artifacts", maximum_bytes=1_048_576)
    adapter = _InMemoryRuntimeProviderAdapter(artifacts, **adapter_options)
    return (
        RuntimeGateway(
            store_path=tmp_path / "gateway.journal",
            _adapter=adapter,
            configuration=configuration,
            _artifacts=artifacts,
            _authority_readback=authority_readback,
        ),
        artifacts,
        adapter,
    )


class _InMemoryAuthorityReadback:
    """Test-only published-authority seam; it never observes provider state."""

    def __init__(self, authority):
        self.authority = authority
        self.subjects = []

    def read(self, subject):
        self.subjects.append(subject)
        return self.authority


def test_plan_invalidation_requires_exact_evidence_and_bound_workspace(tmp_path):
    """#133 rejects junk Evidence and a report for a different Workspace."""

    profile = _profile("invalidation")
    configuration = RuntimeConfiguration(
        profiles={profile.digest: profile},
        host_mappings={
            "coordinator": ProfileMapping(profile.digest),
            "worker": ProfileMapping(profile.digest),
        },
    )
    gateway, artifacts, adapter = _gateway(tmp_path, configuration)
    planning = _planning(artifacts, action="planning:invalidation")
    work = _work(
        artifacts,
        planning,
        purpose=WorkRunPurpose.implementation(),
        action="work:invalidation",
    )
    authority = _FrozenPermissionAuthorityV1(
        plan_revision_digest=work.plan_revision_digest,
        ticket_key=work.ticket_key,
        purpose=work.purpose,
        authority_subtree_digest=work.authority_digest,
        policy_witness_digest="b" * 64,
        grant_pairs=frozenset({("workspace.write.v1", "work-run.workspace.v1")}),
        witness_pairs=frozenset({("workspace.write.v1", "work-run.workspace.v1")}),
        capability_policy=CapabilityPolicy(
            worker_can_edit_issues=False,
            worker_can_edit_blockers=False,
            worker_can_edit_campaign_membership=False,
            worker_can_activate_plan_revision=False,
            worker_can_merge=False,
            worker_can_expand_authority=False,
            worker_can_invoke_global_planning=False,
        ),
    )
    gateway._authority_readback = _InMemoryAuthorityReadback(authority)
    preflight = gateway.planning_preflight(planning)
    gateway.progress(planning, preflight)
    gateway.progress(work)
    observed = adapter.observe(work.stable_action_id)
    assert observed is not None

    def report_for(evidence_digest, *, workspace_identity=observed.workspace_id):
        return PlanInvalidationReport(
            repository=work.repository,
            campaign_key=work.campaign_key,
            plan_revision_digest=work.plan_revision_digest,
            ticket_key=work.ticket_key,
            work_run_key=work.work_run_key,
            runtime_binding_id=work.stable_action_id,
            authority_subtree_digest=work.authority_digest,
            reporter_role="worker",
            evidence_digest=evidence_digest,
            dedup_identity="invalidation:exact",
            invalidated_obligation="issue:111 requires an atomic write",
            required_effects=("workspace.write.v1",),
            workspace_identity=workspace_identity,
        )

    junk = artifacts.put_canonical({"junk": True})
    with pytest.raises(RuntimeGatewayError) as rejected:
        gateway._report_plan_invalidation(work, report_for(junk.digest))
    assert rejected.value.code == "PLAN_INVALIDATION_REPORT_INVALID"

    evidence = artifacts.put_canonical(
        {
            "schema_version": "gwo.evidence.v1",
            "kind": "plan_invalidation",
            "subject": work.canonical(),
            "discovered_facts": ["the write is not atomic"],
            "reproduction": "python -m repro",
            "invalidated_obligation": "issue:111 requires an atomic write",
            "required_effects": ["workspace.write.v1"],
            "workspace_identity": observed.workspace_id,
        }
    )
    with pytest.raises(RuntimeGatewayError) as rejected:
        gateway._report_plan_invalidation(
            work,
            report_for(evidence.digest, workspace_identity="workspace:foreign"),
        )
    assert rejected.value.code == "PLAN_INVALIDATION_RUNTIME_BINDING_INVALID"


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
        host_mappings={
            "coordinator": ProfileMapping(profile.digest),
            "worker": ProfileMapping(profile.digest),
        },
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


def test_progress_returns_parked_proof_without_implicitly_resuming(tmp_path):
    """Only the Slot-owning Kernel may request resume after reacquisition."""

    profile = _profile("coordinator")
    configuration = RuntimeConfiguration(
        profiles={profile.digest: profile},
        host_mappings={
            "coordinator": ProfileMapping(profile.digest),
            "worker": ProfileMapping(profile.digest),
        },
    )
    gateway, artifacts, adapter = _gateway(tmp_path, configuration)
    subject = _planning(artifacts)
    preflight = gateway.planning_preflight(subject)
    gateway.progress(subject, preflight)
    work = _work(
        artifacts,
        subject,
        purpose=WorkRunPurpose.implementation(),
        action="work:parked",
    )
    adapter._pending_permissions[work.stable_action_id] = [
        ("request:parked", "workspace.write.v1", "work-run.workspace.v1")
    ]
    gateway.progress(work)
    gateway.transition(work.stable_action_id, RuntimeCommand.PARK)

    parked = gateway.progress(work)

    assert parked.status == "parked"
    assert parked.command is None
    assert [command for _action, command in adapter.command_calls] == ["start", "start", "park"]


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


def test_progress_auto_allows_only_one_permission_covered_by_frozen_grant_and_witness(
    tmp_path,
):
    """Gateway policy approves the exact normalized request, never a broad grant."""

    profile = _profile("one")
    configuration = RuntimeConfiguration(
        profiles={profile.digest: profile},
        host_mappings={
            "coordinator": ProfileMapping(profile.digest),
            "worker": ProfileMapping(profile.digest),
        },
    )
    artifacts = ArtifactStore(tmp_path / "artifacts", maximum_bytes=1_048_576)
    planning = _planning(artifacts)
    authority_digest = artifacts.put_canonical({"authority": "exact"}).digest
    provisional = WorkRunSubject(
        repository=planning.repository,
        campaign_key=planning.campaign_key,
        campaign_handle=planning.campaign_handle,
        plan_revision_digest=artifacts.put_canonical({"revision": 1}).digest,
        work_run_key="work-run:exact-permission",
        ticket_key="issue:111",
        purpose=WorkRunPurpose.implementation(),
        prompt_artifact_digest="0" * 64,
        authority_subtree_digest=authority_digest,
        stable_action_id="work:exact-permission",
    )
    prompt = artifacts.put_canonical(
        {
            "schema_version": "gwo.runtime.prompt.v1",
            "subject_digest": provisional.prompt_binding_digest,
            "authority_digest": authority_digest,
            "payload": {"complete_contract": "exact permission"},
        }
    )
    work = WorkRunSubject(
        **{**provisional.__dict__, "prompt_artifact_digest": prompt.digest}
    )
    authority = _FrozenPermissionAuthorityV1(
        plan_revision_digest=work.plan_revision_digest,
        ticket_key=work.ticket_key,
        purpose=work.purpose,
        authority_subtree_digest=work.authority_digest,
        policy_witness_digest=artifacts.put_canonical({"policy": "frozen"}).digest,
        grant_pairs=frozenset({("write", "repository:one")}),
        witness_pairs=frozenset({("write", "repository:one")}),
    )
    authority_readback = _InMemoryAuthorityReadback(authority)
    adapter = _InMemoryRuntimeProviderAdapter(artifacts)
    gateway = RuntimeGateway(
        store_path=tmp_path / "gateway.journal",
        _adapter=adapter,
        configuration=configuration,
        _artifacts=artifacts,
        _authority_readback=authority_readback,
    )
    preflight = gateway.planning_preflight(planning)
    gateway.progress(planning, preflight)
    adapter._pending_permissions[work.stable_action_id] = [
        ("request:one", "write", "repository:one"),
        ("request:two", "write", "repository:two"),
    ]

    receipt = gateway.progress(work)

    assert receipt.command == PermissionResponse("request:one", "allow")
    assert [
        command
        for action, command in adapter.command_calls
        if action == work.stable_action_id
    ] == ["start", "permission_response"]
    observed = adapter.observe(work.stable_action_id)
    assert observed.permission_requests[0].request_id == "request:two"

    required = gateway.progress(work)

    assert required.command is None
    assert required.permission_required == PermissionRequired(
        stable_action_id=work.stable_action_id,
        request_id="request:two",
        descriptor_digest=required.permission_required.descriptor_digest,
    )


def test_progress_reads_published_planspec_and_policy_witness_for_auto_allow(tmp_path):
    """The production authority reader uses Artifacts, not Prompt permission text."""

    profile = _profile("one")
    configuration = RuntimeConfiguration(
        profiles={profile.digest: profile},
        host_mappings={
            "coordinator": ProfileMapping(profile.digest),
            "worker": ProfileMapping(profile.digest),
        },
    )
    artifacts = ArtifactStore(tmp_path / "artifacts", maximum_bytes=1_048_576)
    planning = _planning(artifacts)
    grant = {"operation_id": "workspace.write.v1", "resource_id": "work-run.workspace.v1"}
    witness = artifacts.put_canonical(
        {
            "schema_version": 1,
            "ref": "policy:one",
            "authority_grants": {
                "campaign": [],
                "worker": [grant],
                "recovery_worker": [],
                "review": [],
            },
            "allowed_capabilities": [],
            "exclusive_resources": [],
        }
    )
    subtree_core = {
        "policy_witness_digest": witness.digest,
        "grants": [grant],
    }
    subtree_digest = artifacts.put_canonical(subtree_core).digest
    plan = artifacts.put_canonical(
        {
            "schema_version": 3,
            "repository": planning.repository,
            "target_branch": "main",
            "campaign": {"key": planning.campaign_key},
            "policy": {"digest": witness.digest},
            "work": [
                {
                    "key": "issue:111",
                    "authority": {
                        "policy_witness_digest": witness.digest,
                        "worker": {**subtree_core, "subtree_digest": subtree_digest},
                    },
                }
            ],
        }
    )
    provisional = WorkRunSubject(
        repository=planning.repository,
        campaign_key=planning.campaign_key,
        campaign_handle=planning.campaign_handle,
        plan_revision_digest=plan.digest,
        work_run_key="work-run:artifact-authority",
        ticket_key="issue:111",
        purpose=WorkRunPurpose.implementation(),
        prompt_artifact_digest="0" * 64,
        authority_subtree_digest=subtree_digest,
        stable_action_id="work:artifact-authority",
    )
    prompt = artifacts.put_canonical(
        {
            "schema_version": "gwo.runtime.prompt.v1",
            "subject_digest": provisional.prompt_binding_digest,
            "authority_digest": subtree_digest,
            "payload": {"complete_contract": "no permissions here"},
        }
    )
    work = WorkRunSubject(
        **{**provisional.__dict__, "prompt_artifact_digest": prompt.digest}
    )
    adapter = _InMemoryRuntimeProviderAdapter(
        artifacts,
        pending_permissions={
            work.stable_action_id: (
                ("request:artifact", "workspace.write.v1", "work-run.workspace.v1"),
            )
        },
    )
    gateway = RuntimeGateway(
        store_path=tmp_path / "gateway.journal",
        _adapter=adapter,
        configuration=configuration,
        _artifacts=artifacts,
    )
    preflight = gateway.planning_preflight(planning)
    gateway.progress(planning, preflight)

    receipt = gateway.progress(work)

    assert receipt.command == PermissionResponse("request:artifact", "allow")
    assert "permission_response" in [
        command
        for action, command in adapter.command_calls
        if action == work.stable_action_id
    ]


def test_provider_unavailable_is_durable_deduplicated_and_never_replaces_binding(
    tmp_path,
):
    """Provider outages are typed observations, not a hidden retry state machine."""

    profile = _profile("one")
    configuration = RuntimeConfiguration(
        profiles={profile.digest: profile},
        host_mappings={
            "coordinator": ProfileMapping(profile.digest),
            "worker": ProfileMapping(profile.digest),
        },
    )
    artifacts = ArtifactStore(tmp_path / "artifacts", maximum_bytes=1_048_576)
    planning = _planning(artifacts)
    work = _work(
        artifacts,
        planning,
        purpose=WorkRunPurpose.implementation(),
        action="work:provider-unavailable",
    )
    adapter = _InMemoryRuntimeProviderAdapter(
        artifacts,
        pending_permissions={
            work.stable_action_id: (
                ("request:unavailable", "workspace.write.v1", "work-run.workspace.v1"),
            )
        },
    )
    no_authority = _InMemoryAuthorityReadback(None)
    gateway = RuntimeGateway(
        store_path=tmp_path / "gateway.journal",
        _adapter=adapter,
        configuration=configuration,
        _artifacts=artifacts,
        _authority_readback=no_authority,
    )
    preflight = gateway.planning_preflight(planning)
    gateway.progress(planning, preflight)
    started = gateway.progress(work)
    assert started.status == "running"
    assert adapter.created_agent_count == 2

    outcomes = []
    for observation_id in ("outage:one", "outage:two", "outage:three"):
        adapter.observe_failure = _RuntimeFailure.provider_unavailable(
            observation_id,
            stable_action_id=work.stable_action_id,
        )
        outcomes.append(gateway.progress(work).recovery_outcome)

    assert [outcome.kind for outcome in outcomes] == ["wait", "wait", "decision"]
    assert [outcome.reason for outcome in outcomes] == [
        "RuntimeProviderUnavailable",
        "RuntimeProviderUnavailable",
        "RuntimeProviderRecoveryRequired",
    ]
    assert outcomes[-1].next_check_at is None
    assert adapter.created_agent_count == 2

    restarted = RuntimeGateway(
        store_path=tmp_path / "gateway.journal",
        _adapter=adapter,
        configuration=configuration,
        _artifacts=artifacts,
        _authority_readback=no_authority,
    )
    duplicate = restarted.progress(work).recovery_outcome

    assert duplicate == outcomes[-1]
    assert len(restarted._data["actions"][work.stable_action_id]["recovery"]["provider_unavailable"]) == 3


def test_terminal_binding_evidence_requires_fence_and_retire_readback(tmp_path):
    """Gateway can prove termination, but cannot select a replacement action."""

    profile = _profile("one")
    configuration = RuntimeConfiguration(
        profiles={profile.digest: profile},
        host_mappings={
            "coordinator": ProfileMapping(profile.digest),
            "worker": ProfileMapping(profile.digest),
        },
    )
    gateway, artifacts, adapter = _gateway(tmp_path, configuration)
    planning = _planning(artifacts)
    preflight = gateway.planning_preflight(planning)
    gateway.progress(planning, preflight)
    work = _work(
        artifacts,
        planning,
        purpose=WorkRunPurpose.implementation(),
        action="work:terminal-evidence",
    )
    gateway.progress(work)

    fenced = gateway.transition(work.stable_action_id, RuntimeCommand.FENCE)
    retired = gateway.transition(work.stable_action_id, RuntimeCommand.RETIRE)

    assert fenced.terminal_binding_evidence is None
    assert retired.status == "retired"
    assert retired.terminal_binding_evidence is not None
    assert retired.terminal_binding_evidence.stable_action_id == work.stable_action_id
    assert adapter.created_agent_count == 2
    assert [command for _action, command in adapter.command_calls] == [
        "start",
        "start",
        "fence",
        "retire",
    ]


def test_pre_identity_provider_outage_selects_only_the_persisted_fallback(tmp_path):
    """Availability fallback is allowed once before any binding exists."""

    primary, fallback = _profile("primary"), _profile("fallback")
    configuration = RuntimeConfiguration(
        profiles={primary.digest: primary, fallback.digest: fallback},
        host_mappings={
            "coordinator": ProfileMapping(primary.digest),
            "worker": ProfileMapping(primary.digest, fallback.digest),
        },
    )
    gateway, artifacts, adapter = _gateway(tmp_path, configuration)
    planning = _planning(artifacts)
    preflight = gateway.planning_preflight(planning)
    gateway.progress(planning, preflight)
    work = _work(
        artifacts,
        planning,
        purpose=WorkRunPurpose.implementation(),
        action="work:pre-identity-fallback",
    )
    original_prepare = adapter.prepare
    attempts = 0

    def unavailable_once(spec):
        nonlocal attempts
        if spec.stable_action_id == work.stable_action_id and attempts == 0:
            attempts += 1
            return _RuntimeFailure.provider_unavailable(
                "outage:primary",
                stable_action_id=work.stable_action_id,
            )
        return original_prepare(spec)

    adapter.prepare = unavailable_once

    receipt = gateway.progress(work)

    assert receipt.recovery_outcome is None
    assert adapter.observe(work.stable_action_id).profile_digest == fallback.digest
    assert gateway._data["actions"][work.stable_action_id]["fallback_selected"] is True
    assert adapter.created_agent_count == 2


def test_command_provider_unavailable_returns_the_same_typed_recovery_fact(tmp_path):
    """A commanded park retains the binding when its provider is unavailable."""

    profile = _profile("one")
    configuration = RuntimeConfiguration(
        profiles={profile.digest: profile},
        host_mappings={
            "coordinator": ProfileMapping(profile.digest),
            "worker": ProfileMapping(profile.digest),
        },
    )
    artifacts = ArtifactStore(tmp_path / "artifacts", maximum_bytes=1_048_576)
    planning = _planning(artifacts)
    work = _work(
        artifacts,
        planning,
        purpose=WorkRunPurpose.implementation(),
        action="work:command-unavailable",
    )
    adapter = _InMemoryRuntimeProviderAdapter(
        artifacts,
        pending_permissions={
            work.stable_action_id: (
                ("request:command", "workspace.write.v1", "work-run.workspace.v1"),
            )
        },
    )
    gateway = RuntimeGateway(
        store_path=tmp_path / "gateway.journal",
        _adapter=adapter,
        configuration=configuration,
        _artifacts=artifacts,
        _authority_readback=_InMemoryAuthorityReadback(None),
    )
    preflight = gateway.planning_preflight(planning)
    gateway.progress(planning, preflight)
    gateway.progress(work)
    original_command = adapter.command

    def unavailable_park(stable_action_id, command):
        if command is RuntimeCommand.PARK:
            return _RuntimeFailure.provider_unavailable(
                "outage:park",
                stable_action_id=stable_action_id,
            )
        return original_command(stable_action_id, command)

    adapter.command = unavailable_park

    receipt = gateway.transition(work.stable_action_id, RuntimeCommand.PARK)

    assert receipt.recovery_outcome is not None
    assert receipt.recovery_outcome.kind == "wait"
    assert receipt.recovery_outcome.reason == "RuntimeProviderUnavailable"
    assert adapter.created_agent_count == 2
    assert adapter.observe(work.stable_action_id).lifecycle == "running"
