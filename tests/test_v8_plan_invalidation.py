"""#133 Plan Revision invalidation report + Work Run quiescence.

These tests prove the public `start -> advance -> inspect` behavior described
by ADR-0062 and GitHub Issue #133:

- ``RuntimeGateway.report_plan_invalidation`` accepts one typed, Artifact-backed
  report bound to the exact Campaign, Plan Revision, Ticket, Work Run, Runtime
  Binding, authority-subtree digest, reporter role, and Evidence digest, and
  proves the reporting role cannot edit Issues, blockers, Campaign membership,
  authority, merge state, or the global route.
- ``ExecutionKernel.advance`` ingests the authoritative observation under a
  stable deduplication identity before changing Work Run state.  Only the
  affected Work Run becomes quiescent, releases its Worker Slot, performs no
  further Worker/Candidate/Review/Repair/delivery action, and is explained by
  ``inspect`` without a transcript.
- Unrelated Work Runs whose contracts, dependencies, claims, shared facts,
  resources, and authority remain valid continue and deterministically refill
  released capacity.
- Stale or mismatched Campaign/Plan Revision/Ticket/Work Run/Runtime
  Binding/authority/Evidence identity cannot stop current work; duplicate
  callbacks, restart, and repeated ``advance`` cannot repeat the transition.
- The public interface remains exactly ``start -> advance -> inspect`` with
  the existing five public statuses; no fourth operation or sixth status is
  introduced.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import sys
import threading

import pytest


SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "orchestrator" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from gwo_v8._canonical import canonical_bytes, digest_bytes, digest_value  # noqa: E402
from gwo_v8.execution_kernel import (  # noqa: E402
    ExecutionKernel,
    ExecutionKernelError,
    PlanInvalidationObservation,
    WorkRunAction,
    WorkRunObservation,
)
from gwo_v8.plan_control import (  # noqa: E402
    ActivationReceipt,
    ActivePlanReadback,
    CampaignHandle,
    TicketClaimProof,
)
from gwo_v8.runtime_gateway import (  # noqa: E402
    ArtifactStore,
    CapabilityPolicy,
    PlanInvalidationReceipt,
    PlanInvalidationReport,
    ProfileMapping,
    RuntimeConfiguration,
    RuntimeGateway,
    RuntimeGatewayError,
    RuntimeProfile,
    WorkRunPurpose,
    WorkRunSubject,
    _InMemoryRuntimeProviderAdapter,
)


# ---------------------------------------------------------------------------
# Shared fixtures (adapted from test_v8_execution_kernel.py and
# test_v8_runtime_gateway.py)
# ---------------------------------------------------------------------------


def _profile(name: str) -> RuntimeProfile:
    return RuntimeProfile(
        name=name,
        provider="test-provider",
        model=f"model:{name}",
        thinking="high",
        mode="safe",
        features={},
    )


def _configuration(profile: RuntimeProfile) -> RuntimeConfiguration:
    return RuntimeConfiguration(
        profiles={profile.digest: profile},
        host_mappings={
            "coordinator": ProfileMapping(profile.digest),
            "worker": ProfileMapping(profile.digest),
            "recovery_worker": ProfileMapping(profile.digest),
            "review_primary": ProfileMapping(profile.digest),
            "review_strong": ProfileMapping(profile.digest),
        },
    )


def _planning_subject(store: ArtifactStore, *, action: str = "planning:one"):
    from gwo_v8 import CampaignPlanningSubject
    from gwo_v8.planning_protocol import planning_prompt

    snapshot = store.put_canonical({"tickets": [{"key": "issue:111"}]})
    policy = store.put_canonical({"policy": "frozen"})
    provisional = CampaignPlanningSubject(
        repository="owner/repository",
        campaign_key="campaign:invalidation",
        campaign_handle="handle:invalidation",
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


def _work_subject(
    planning,
    store: ArtifactStore,
    *,
    ticket_key: str = "issue:1",
    action: str = "work:one",
    plan_revision_digest: str | None = None,
    authority_subtree_digest: str | None = None,
):
    provisional = WorkRunSubject(
        repository=planning.repository,
        campaign_key=planning.campaign_key,
        campaign_handle=planning.campaign_handle,
        plan_revision_digest=plan_revision_digest
        or store.put_canonical({"revision": 1}).digest,
        work_run_key=f"work-run:{ticket_key}",
        ticket_key=ticket_key,
        purpose=WorkRunPurpose.implementation(),
        prompt_artifact_digest="0" * 64,
        authority_subtree_digest=authority_subtree_digest
        or planning.policy_witness_digest,
        stable_action_id=action,
    )
    prompt = store.put_canonical(
        {
            "schema_version": "gwo.runtime.prompt.v1",
            "subject_digest": provisional.prompt_binding_digest,
            "authority_digest": provisional.authority_digest,
            "payload": {"complete_contract": "implementation context"},
        }
    )
    return WorkRunSubject(
        **{**provisional.__dict__, "prompt_artifact_digest": prompt.digest}
    )


# ---------------------------------------------------------------------------
# RuntimeGateway.report_plan_invalidation
# ---------------------------------------------------------------------------


def test_report_plan_invalidation_persists_one_artifact_backed_observation(tmp_path):
    """RED: RuntimeGateway accepts one typed Plan Invalidation report."""

    profile = _profile("worker")
    configuration = _configuration(profile)
    artifacts = ArtifactStore(tmp_path / "artifacts", maximum_bytes=1_048_576)
    planning = _planning_subject(artifacts)
    capability_policy = CapabilityPolicy(worker_can_edit_issues=False)
    authority_digest = artifacts.put_canonical(
        {"authority": "exact", "capability_policy": capability_policy.canonical()}
    ).digest
    work = _work_subject(
        planning,
        artifacts,
        action="work:report-once",
        authority_subtree_digest=authority_digest,
    )
    adapter = _InMemoryRuntimeProviderAdapter(artifacts)
    gateway = RuntimeGateway(
        store_path=tmp_path / "gateway.journal",
        _adapter=adapter,
        configuration=configuration,
        _artifacts=artifacts,
        _authority_readback=_CapabilityAuthorityReadback(
            artifacts,
            capability_policy=capability_policy,
        ),
    )

    preflight = gateway.planning_preflight(planning)
    gateway.progress(planning, preflight)
    gateway.progress(work)

    report = _valid_report(work, artifacts, dedup="invalidation:one")
    receipt = gateway.report_plan_invalidation(work, report)

    assert type(receipt) is PlanInvalidationReceipt
    assert receipt.report_digest == report.digest
    assert _DIGEST_RE.fullmatch(receipt.receipt_digest)
    assert receipt.capability_policy_proof.capability_policy.worker_can_edit_issues is False


def test_report_plan_invalidation_rejects_replacement_plan_or_owner_mutation(tmp_path):
    """The report cannot carry a replacement plan or Issue/owner edit."""

    profile = _profile("worker")
    configuration = _configuration(profile)
    artifacts = ArtifactStore(tmp_path / "artifacts", maximum_bytes=1_048_576)
    planning = _planning_subject(artifacts)
    capability_policy = CapabilityPolicy(worker_can_edit_issues=False)
    authority_digest = artifacts.put_canonical(
        {"authority": "exact", "capability_policy": capability_policy.canonical()}
    ).digest
    work = _work_subject(
        planning,
        artifacts,
        action="work:reject-replacement",
        authority_subtree_digest=authority_digest,
    )
    adapter = _InMemoryRuntimeProviderAdapter(artifacts)
    gateway = RuntimeGateway(
        store_path=tmp_path / "gateway.journal",
        _adapter=adapter,
        configuration=configuration,
        _artifacts=artifacts,
        _authority_readback=_CapabilityAuthorityReadback(
            artifacts,
            capability_policy=capability_policy,
        ),
    )

    preflight = gateway.planning_preflight(planning)
    gateway.progress(planning, preflight)
    gateway.progress(work)

    base_kwargs = dict(
        repository=work.repository,
        campaign_key=work.campaign_key,
        plan_revision_digest=work.plan_revision_digest,
        ticket_key=work.ticket_key,
        work_run_key=work.work_run_key,
        runtime_binding_id=work.stable_action_id,
        authority_subtree_digest=work.authority_subtree_digest,
        reporter_role="worker",
        evidence_digest=artifacts.put_canonical({"evidence": "one"}).digest,
        dedup_identity="invalidation:one",
        invalidated_obligation="issue:1 needs X",
        required_effects=("persistence.atomic.v1",),
        workspace_identity="workspace:one",
    )
    for forbidden_field, forbidden_value in (
        ("replacement_planspec", {"schema_version": 3}),
        ("ticket_owner", "issue:222"),
        ("dependency_edit", {"add": "issue:222"}),
        ("campaign_membership", ["issue:222"]),
        ("merge_request", {"pr": "123"}),
        ("campaign_order", ["issue:2", "issue:1"]),
    ):
        bad_kwargs = dict(base_kwargs)
        bad_kwargs[forbidden_field] = forbidden_value
        with pytest.raises(RuntimeGatewayError) as rejected:
            PlanInvalidationReport(**bad_kwargs)
        assert rejected.value.code == "PLAN_INVALIDATION_REPORT_INVALID"

    # A clean report with no forbidden fields succeeds.
    good_report = PlanInvalidationReport(**base_kwargs)
    receipt = gateway.report_plan_invalidation(work, good_report)
    assert receipt.report_digest == good_report.digest


def test_report_plan_invalidation_fails_closed_when_capability_proof_missing(tmp_path):
    """Inability to prove the Worker capability policy fails closed."""

    profile = _profile("worker")
    configuration = _configuration(profile)
    artifacts = ArtifactStore(tmp_path / "artifacts", maximum_bytes=1_048_576)
    planning = _planning_subject(artifacts)
    authority_digest = artifacts.put_canonical({"authority": "exact"}).digest
    work = _work_subject(
        planning,
        artifacts,
        action="work:capability-missing",
        authority_subtree_digest=authority_digest,
    )
    adapter = _InMemoryRuntimeProviderAdapter(artifacts)
    # No capability policy is published; the readback returns None.
    gateway = RuntimeGateway(
        store_path=tmp_path / "gateway.journal",
        _adapter=adapter,
        configuration=configuration,
        _artifacts=artifacts,
        _authority_readback=_CapabilityAuthorityReadback(artifacts),
    )

    preflight = gateway.planning_preflight(planning)
    gateway.progress(planning, preflight)
    gateway.progress(work)

    report = _valid_report(work, artifacts, dedup="invalidation:one")
    with pytest.raises(RuntimeGatewayError) as rejected:
        gateway.report_plan_invalidation(work, report)
    assert rejected.value.code == "PLAN_INVALIDATION_CAPABILITY_PROOF_FAIL_CLOSED"


def test_report_plan_invalidation_rejects_subject_bound_to_another_campaign(tmp_path):
    """A report bound to a foreign Campaign, Plan Revision, Ticket, Work Run,
    Runtime Binding, or authority digest cannot stop current work."""

    profile = _profile("worker")
    configuration = _configuration(profile)
    artifacts = ArtifactStore(tmp_path / "artifacts", maximum_bytes=1_048_576)
    planning = _planning_subject(artifacts)
    capability_policy = CapabilityPolicy(worker_can_edit_issues=False)
    authority_digest = artifacts.put_canonical(
        {"authority": "exact", "capability_policy": capability_policy.canonical()}
    ).digest
    work = _work_subject(
        planning,
        artifacts,
        action="work:foreign",
        authority_subtree_digest=authority_digest,
    )
    adapter = _InMemoryRuntimeProviderAdapter(artifacts)
    gateway = RuntimeGateway(
        store_path=tmp_path / "gateway.journal",
        _adapter=adapter,
        configuration=configuration,
        _artifacts=artifacts,
        _authority_readback=_CapabilityAuthorityReadback(
            artifacts,
            capability_policy=capability_policy,
        ),
    )

    preflight = gateway.planning_preflight(planning)
    gateway.progress(planning, preflight)
    gateway.progress(work)

    foreign = replace(
        work,
        campaign_key="campaign:foreign",
        work_run_key="work-run:foreign",
        stable_action_id="work:foreign",
    )
    report = _valid_report(foreign, artifacts, dedup="invalidation:foreign")
    with pytest.raises(RuntimeGatewayError) as rejected:
        gateway.report_plan_invalidation(work, report)
    assert rejected.value.code == "PLAN_INVALIDATION_SUBJECT_INVALID"


def test_report_plan_invalidation_deduplicates_repeated_submissions(tmp_path):
    """Duplicate callbacks cannot create repeated persisted observations."""

    profile = _profile("worker")
    configuration = _configuration(profile)
    artifacts = ArtifactStore(tmp_path / "artifacts", maximum_bytes=1_048_576)
    planning = _planning_subject(artifacts)
    capability_policy = CapabilityPolicy(worker_can_edit_issues=False)
    authority_digest = artifacts.put_canonical(
        {"authority": "exact", "capability_policy": capability_policy.canonical()}
    ).digest
    work = _work_subject(
        planning,
        artifacts,
        action="work:dedup",
        authority_subtree_digest=authority_digest,
    )
    adapter = _InMemoryRuntimeProviderAdapter(artifacts)
    gateway = RuntimeGateway(
        store_path=tmp_path / "gateway.journal",
        _adapter=adapter,
        configuration=configuration,
        _artifacts=artifacts,
        _authority_readback=_CapabilityAuthorityReadback(
            artifacts,
            capability_policy=capability_policy,
        ),
    )

    preflight = gateway.planning_preflight(planning)
    gateway.progress(planning, preflight)
    gateway.progress(work)

    report = _valid_report(work, artifacts, dedup="invalidation:one")
    first = gateway.report_plan_invalidation(work, report)
    second = gateway.report_plan_invalidation(work, report)

    assert second.receipt_digest == first.receipt_digest
    assert second.report_digest == first.report_digest


# ---------------------------------------------------------------------------
# ExecutionKernel quiescence
# ---------------------------------------------------------------------------


def test_advance_quiesces_only_the_affected_work_run(tmp_path):
    """RED: ExecutionKernel quiesces only the Work Run named in the report."""

    _Effects = _PlanInvalidationEffects

    active, handle = _active_campaign(("issue:1", "issue:2"))
    effects = _Effects()
    plans = _Plans(active)
    kernel = ExecutionKernel(
        store_path=tmp_path / "execution.sqlite3",
        plan_control=plans,
        effects=effects,
    )
    kernel.advance(handle)
    assert [action.ticket_key for action in effects.executed] == ["issue:1", "issue:2"]
    binding_id = effects.executed[0].stable_action_id

    observation = _invalidation_observation(
        active=active,
        ticket_key="issue:1",
        report_digest="a" * 64,
        dedup="invalidation:one",
        runtime_binding_id=binding_id,
    )

    outcome = kernel.advance(handle, plan_invalidation=observation)

    # The unrelated Work Run remains running, so the Campaign-level status is
    # Running (precedence: Running > Decision > Wait > Blocked).  The affected
    # Work Run is quiescent and exposes its Decision condition through inspect.
    assert outcome.status == "Running"
    diagnostics = kernel.inspect(handle)
    quiescent = _summary(diagnostics, "issue:1")
    assert quiescent.phase == "quiescent"
    assert quiescent.slot_held is False
    assert quiescent.plan_invalidation is not None
    assert quiescent.plan_invalidation.report_digest == "a" * 64
    assert quiescent.plan_invalidation.invalidated_obligation == "issue:1 needs X"
    assert quiescent.plan_invalidation.evidence_digest == "e" * 64
    unaffected = _summary(diagnostics, "issue:2")
    assert unaffected.phase == "running"
    assert unaffected.slot_held is True
    assert unaffected.plan_invalidation is None
    assert diagnostics.worker_slots == {"limit": 4, "held": 1, "available": 3}

    # Once the unrelated Work Run completes, the quiescent Work Run's pending
    # Decision surfaces as the Campaign-level status.
    effects.observe("issue:2", "completed")
    final = kernel.advance(handle, "runtime-wake:issue-2-complete")
    assert final.status == "Decision"
    assert "PlanInvalidation:issue:1" in final.reason


def test_quiesced_work_run_releases_slot_and_unrelated_run_refills(tmp_path):
    """Released capacity is refilled deterministically after quiescence."""

    _Effects = _PlanInvalidationEffects

    active, handle = _active_campaign(
        ("issue:1", "issue:2", "issue:3", "issue:4", "issue:5")
    )
    effects = _Effects()
    kernel = ExecutionKernel(
        store_path=tmp_path / "execution.sqlite3",
        plan_control=_Plans(active),
        effects=effects,
    )
    kernel.advance(handle)
    assert [action.ticket_key for action in effects.executed] == [
        "issue:1",
        "issue:2",
        "issue:3",
        "issue:4",
    ]
    binding_id = _binding_id_for(effects, "issue:1")

    observation = _invalidation_observation(
        active=active,
        ticket_key="issue:1",
        report_digest="a" * 64,
        dedup="invalidation:one",
        runtime_binding_id=binding_id,
    )

    outcome = kernel.advance(handle, plan_invalidation=observation)

    assert outcome.status == "Running"
    assert [action.ticket_key for action in effects.executed] == [
        "issue:1",
        "issue:2",
        "issue:3",
        "issue:4",
        "issue:5",
    ]
    diagnostics = kernel.inspect(handle)
    assert _summary(diagnostics, "issue:1").phase == "quiescent"
    assert _summary(diagnostics, "issue:5").phase == "running"
    assert diagnostics.worker_slots == {"limit": 4, "held": 4, "available": 0}


def test_quiesced_work_run_performs_no_further_semantic_action(tmp_path):
    """A quiescent Work Run cannot drive Worker/Candidate/Review/Repair/delivery."""

    _Effects = _PlanInvalidationEffects

    active, handle = _active_campaign(("issue:1", "issue:2"))
    effects = _Effects()
    kernel = ExecutionKernel(
        store_path=tmp_path / "execution.sqlite3",
        plan_control=_Plans(active),
        effects=effects,
    )
    kernel.advance(handle)
    binding_id = _binding_id_for(effects, "issue:1")

    observation = _invalidation_observation(
        active=active,
        ticket_key="issue:1",
        report_digest="a" * 64,
        dedup="invalidation:one",
        runtime_binding_id=binding_id,
    )
    kernel.advance(handle, plan_invalidation=observation)

    effects.observe("issue:1", "candidate_checks")
    effects.observe("issue:1", "formal_review")
    effects.observe("issue:1", "repair")
    for _ in range(3):
        kernel.advance(handle, "runtime-wake:quiescent")

    quiescent_actions = [
        action
        for action in effects.executed
        if action.ticket_key == "issue:1"
        and action.kind in {"semantic_execution", "semantic_resume"}
    ]
    assert len(quiescent_actions) == 1
    diagnostics = kernel.inspect(handle)
    assert _summary(diagnostics, "issue:1").phase == "quiescent"
    assert _summary(diagnostics, "issue:1").slot_held is False


def test_duplicate_invalidations_are_idempotent_under_advance(tmp_path):
    """Duplicate callbacks, restart, and repeated advance do not repeat."""

    _Effects = _PlanInvalidationEffects

    active, handle = _active_campaign(("issue:1", "issue:2"))
    effects = _Effects()
    kernel = ExecutionKernel(
        store_path=tmp_path / "execution.sqlite3",
        plan_control=_Plans(active),
        effects=effects,
    )
    kernel.advance(handle)
    before = len(effects.executed)
    binding_id = _binding_id_for(effects, "issue:1")

    observation = _invalidation_observation(
        active=active,
        ticket_key="issue:1",
        report_digest="a" * 64,
        dedup="invalidation:one",
        runtime_binding_id=binding_id,
    )
    kernel.advance(handle, plan_invalidation=observation)
    first_state = kernel._load(handle)

    kernel.advance(handle, plan_invalidation=observation)
    second_state = kernel._load(handle)

    assert first_state == second_state
    assert len(effects.executed) == before
    diagnostics = kernel.inspect(handle)
    assert _summary(diagnostics, "issue:1").phase == "quiescent"


def test_restart_replays_invalidated_work_run_quiescence_without_repetition(tmp_path):
    """A fresh Kernel instance reads persisted state without re-issuing effects."""

    _Effects = _PlanInvalidationEffects

    active, handle = _active_campaign(("issue:1", "issue:2"))
    effects = _Effects()
    kernel = ExecutionKernel(
        store_path=tmp_path / "execution.sqlite3",
        plan_control=_Plans(active),
        effects=effects,
    )
    kernel.advance(handle)
    binding_id = _binding_id_for(effects, "issue:1")
    observation = _invalidation_observation(
        active=active,
        ticket_key="issue:1",
        report_digest="a" * 64,
        dedup="invalidation:one",
        runtime_binding_id=binding_id,
    )
    kernel.advance(handle, plan_invalidation=observation)

    before = len(effects.executed)
    restarted = ExecutionKernel(
        store_path=tmp_path / "execution.sqlite3",
        plan_control=_Plans(active),
        effects=effects,
    )
    restarted.advance(handle, plan_invalidation=observation)
    restarted.advance(handle, "runtime-wake:restart")

    assert len(effects.executed) == before
    diagnostics = restarted.inspect(handle)
    assert _summary(diagnostics, "issue:1").phase == "quiescent"
    assert _summary(diagnostics, "issue:2").phase == "running"


def test_stale_identity_cannot_quiesce_current_work(tmp_path):
    """An observation bound to another Campaign/Plan/Ticket/Work Run fails closed."""

    _Effects = _PlanInvalidationEffects

    active, handle = _active_campaign(("issue:1", "issue:2"))
    effects = _Effects()
    kernel = ExecutionKernel(
        store_path=tmp_path / "execution.sqlite3",
        plan_control=_Plans(active),
        effects=effects,
    )
    kernel.advance(handle)
    binding_id = _binding_id_for(effects, "issue:1")

    foreign_campaign = replace(observation := _invalidation_observation(
        active=active,
        ticket_key="issue:1",
        report_digest="a" * 64,
        dedup="invalidation:one",
        runtime_binding_id=binding_id,
    ), campaign_key="campaign:foreign")
    with pytest.raises(ExecutionKernelError) as rejected:
        kernel.advance(handle, plan_invalidation=foreign_campaign)
    assert rejected.value.code == "INVALIDATION_IDENTITY_MISMATCH"

    foreign_plan = replace(
        observation,
        plan_revision_digest="0" * 64,
    )
    with pytest.raises(ExecutionKernelError) as rejected:
        kernel.advance(handle, plan_invalidation=foreign_plan)
    assert rejected.value.code == "INVALIDATION_IDENTITY_MISMATCH"

    foreign_ticket = replace(observation, ticket_key="issue:99")
    with pytest.raises(ExecutionKernelError) as rejected:
        kernel.advance(handle, plan_invalidation=foreign_ticket)
    assert rejected.value.code == "INVALIDATION_IDENTITY_MISMATCH"

    diagnostics = kernel.inspect(handle)
    assert _summary(diagnostics, "issue:1").phase == "running"
    assert _summary(diagnostics, "issue:2").phase == "running"


def test_invalid_report_payload_is_rejected_by_kernel(tmp_path):
    """A malformed observation payload fails closed and changes no state."""

    _Effects = _PlanInvalidationEffects

    active, handle = _active_campaign(("issue:1", "issue:2"))
    effects = _Effects()
    kernel = ExecutionKernel(
        store_path=tmp_path / "execution.sqlite3",
        plan_control=_Plans(active),
        effects=effects,
    )
    kernel.advance(handle)
    before = len(effects.executed)

    # The observation constructor rejects a malformed digest before any
    # state transition can occur; no Kernel state changes.
    with pytest.raises(ExecutionKernelError) as rejected:
        _invalidation_observation(
            active=active,
            ticket_key="issue:1",
            report_digest="not-a-digest",
            dedup="invalidation:one",
        )
    assert rejected.value.code == "PLAN_INVALIDATION_OBSERVATION_INVALID"
    # A valid observation bound to a foreign authority subtree fails closed
    # at the Kernel boundary without changing Work Run state.
    valid_foreign = _invalidation_observation(
        active=active,
        ticket_key="issue:1",
        report_digest="a" * 64,
        dedup="invalidation:bad-authority",
        authority_subtree_digest="f" * 64,
        runtime_binding_id=_binding_id_for(effects, "issue:1"),
    )
    with pytest.raises(ExecutionKernelError) as rejected:
        kernel.advance(handle, plan_invalidation=valid_foreign)
    # The Kernel persists the observation under its exact identity before
    # changing Work Run state; a mismatched authority subtree fails closed.
    assert rejected.value.code == "INVALIDATION_IDENTITY_MISMATCH"
    assert len(effects.executed) == before
    diagnostics = kernel.inspect(handle)
    assert _summary(diagnostics, "issue:1").phase == "running"
    assert _summary(diagnostics, "issue:2").phase == "running"


def test_concurrent_advance_with_invalidation_never_duplicates_quiescence(tmp_path):
    """Concurrent advance calls cannot double-quiesce or exceed capacity."""

    _Effects = _PlanInvalidationEffects

    active, handle = _active_campaign(("issue:1", "issue:2"))
    effects = _BlockingInvalidationEffects()
    kernel = ExecutionKernel(
        store_path=tmp_path / "execution.sqlite3",
        plan_control=_Plans(active),
        effects=effects,
    )
    kernel.advance(handle)
    binding_id = _binding_id_for(effects, "issue:1")
    observation = _invalidation_observation(
        active=active,
        ticket_key="issue:1",
        report_digest="a" * 64,
        dedup="invalidation:one",
        runtime_binding_id=binding_id,
    )

    failures: list[Exception] = []

    def invoke():
        try:
            kernel.advance(handle, plan_invalidation=observation)
        except Exception as error:  # pragma: no cover - asserted below
            failures.append(error)

    threads = [threading.Thread(target=invoke) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert failures == []
    diagnostics = kernel.inspect(handle)
    assert _summary(diagnostics, "issue:1").phase == "quiescent"
    assert diagnostics.worker_slots["held"] == 1


def test_inspect_exposes_invalidation_without_a_transcript(tmp_path):
    """inspect names the invalidated obligation, evidence, slot/claim state,
    retained diagnostic identity, and exact continuation condition."""

    _Effects = _PlanInvalidationEffects

    active, handle = _active_campaign(("issue:1",))
    effects = _Effects()
    kernel = ExecutionKernel(
        store_path=tmp_path / "execution.sqlite3",
        plan_control=_Plans(active),
        effects=effects,
    )
    kernel.advance(handle)
    binding_id = _binding_id_for(effects, "issue:1")
    observation = _invalidation_observation(
        active=active,
        ticket_key="issue:1",
        report_digest="a" * 64,
        dedup="invalidation:one",
        evidence_digest="e" * 64,
        invalidated_obligation="issue:1 must persist atomically",
        required_effects=("persistence.atomic.v1",),
        workspace_identity="workspace:diagnostic-1",
        runtime_binding_id=binding_id,
    )
    kernel.advance(handle, plan_invalidation=observation)

    diagnostics = kernel.inspect(handle)
    summary = _summary(diagnostics, "issue:1")
    assert summary.phase == "quiescent"
    assert summary.plan_invalidation is not None
    assert summary.plan_invalidation.invalidated_obligation == "issue:1 must persist atomically"
    assert summary.plan_invalidation.evidence_digest == "e" * 64
    assert summary.plan_invalidation.required_effects == ("persistence.atomic.v1",)
    assert summary.plan_invalidation.workspace_identity == "workspace:diagnostic-1"
    assert summary.plan_invalidation.continuation_condition == "PlanControlReplanRequired"
    assert summary.slot_held is False


def test_missing_authority_structure_fails_closed_in_the_kernel(tmp_path):
    """A PlanSpec without the frozen authority record for the reporter role
    fails closed: the Kernel never accepts an invalidation whose authority
    boundary it cannot independently prove."""

    _Effects = _PlanInvalidationEffects

    active, handle = _active_campaign(
        ("issue:1", "issue:2"),
        work_facts={"issue:1": {"authority": {}}},
    )
    effects = _Effects()
    kernel = ExecutionKernel(
        store_path=tmp_path / "execution.sqlite3",
        plan_control=_Plans(active),
        effects=effects,
    )
    kernel.advance(handle)
    binding_id = _binding_id_for(effects, "issue:1")
    before = len(effects.executed)

    observation = _invalidation_observation(
        active=active,
        ticket_key="issue:1",
        report_digest="a" * 64,
        dedup="invalidation:no-authority",
        runtime_binding_id=binding_id,
    )
    with pytest.raises(ExecutionKernelError) as rejected:
        kernel.advance(handle, plan_invalidation=observation)
    assert rejected.value.code == "INVALIDATION_IDENTITY_MISMATCH"
    assert len(effects.executed) == before
    diagnostics = kernel.inspect(handle)
    assert _summary(diagnostics, "issue:1").phase == "running"
    assert _summary(diagnostics, "issue:2").phase == "running"


# ---------------------------------------------------------------------------
# Public surface and status invariants
# ---------------------------------------------------------------------------


def test_no_fourth_public_operation_is_introduced():
    """Public surface remains start -> advance -> inspect with five statuses."""

    from gwo_v8.execution_kernel import CampaignStatus
    from gwo_v8.plan_control import start

    public_kernel_operations = {
        name for name in dir(ExecutionKernel)
        if not name.startswith("_") and callable(getattr(ExecutionKernel, name))
    }
    # The new ``report_plan_invalidation`` lives on RuntimeGateway, not on the
    # Kernel.  Kernel advance/inspect remain the only public workflow driver.
    assert {"advance", "inspect"}.issubset(public_kernel_operations)
    assert "report_plan_invalidation" not in public_kernel_operations
    assert {member.value for member in CampaignStatus} == {
        "Complete",
        "Running",
        "Decision",
        "Wait",
        "Blocked",
    }
    assert callable(start)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_DIGEST_RE = __import__("re").compile(r"[0-9a-f]{64}")


class _CapabilityAuthorityReadback:
    """Test-only authority readback that always returns a frozen policy view."""

    def __init__(self, artifacts, *, capability_policy=None):
        self._artifacts = artifacts
        self._capability_policy = capability_policy
        self.subjects = []

    def read(self, subject):
        self.subjects.append(subject)
        if self._capability_policy is None:
            return None
        from gwo_v8.runtime_gateway import _FrozenPermissionAuthorityV1

        return _FrozenPermissionAuthorityV1(
            plan_revision_digest=subject.plan_revision_digest,
            ticket_key=subject.ticket_key,
            purpose=subject.purpose,
            authority_subtree_digest=subject.authority_subtree_digest,
            policy_witness_digest=self._artifacts.put_canonical({"policy": "frozen"}).digest,
            grant_pairs=frozenset({("write", "repository:one")}),
            witness_pairs=frozenset({("write", "repository:one")}),
            capability_policy=self._capability_policy,
        )


def _valid_report(
    subject: WorkRunSubject,
    store: ArtifactStore,
    *,
    dedup: str,
) -> PlanInvalidationReport:
    evidence = store.put_canonical(
        {
            "schema_version": "gwo.evidence.v1",
            "kind": "plan_invalidation",
            "subject": subject.canonical(),
            "discovered_facts": ["catalog change requires atomic persistence"],
            "reproduction": "git checkout <sha>; python -m repro",
            "invalidated_obligation": "issue:1 needs X",
            "required_effects": ["persistence.atomic.v1"],
            "workspace_identity": f"workspace:{subject.work_run_key}",
        }
    )
    return PlanInvalidationReport(
        repository=subject.repository,
        campaign_key=subject.campaign_key,
        plan_revision_digest=subject.plan_revision_digest,
        ticket_key=subject.ticket_key,
        work_run_key=subject.work_run_key,
        runtime_binding_id=subject.stable_action_id,
        authority_subtree_digest=subject.authority_subtree_digest,
        reporter_role="worker",
        evidence_digest=evidence.digest,
        dedup_identity=dedup,
        invalidated_obligation="issue:1 needs X",
        required_effects=("persistence.atomic.v1",),
        workspace_identity=f"workspace:{subject.work_run_key}",
    )


def _binding_id_for(effects, ticket_key):
    """Return the stable semantic action id established for one Work Run."""

    return next(
        action.stable_action_id
        for action in effects.executed
        if action.ticket_key == ticket_key
    )


def _invalidation_observation(
    *,
    active,
    ticket_key,
    report_digest,
    dedup,
    evidence_digest="e" * 64,
    invalidated_obligation="issue:1 needs X",
    required_effects=("persistence.atomic.v1",),
    workspace_identity="workspace:diagnostic-1",
    authority_subtree_digest="a" * 64,
    runtime_binding_id=None,
) -> PlanInvalidationObservation:
    binding = runtime_binding_id if runtime_binding_id is not None else f"work:{ticket_key}"
    return PlanInvalidationObservation(
        repository=active.handle.repository,
        campaign_key=active.handle.campaign_key,
        plan_revision_digest=active.current_revision_digest,
        ticket_key=ticket_key,
        work_run_key=f"work-run:{ticket_key}",
        runtime_binding_id=binding,
        authority_subtree_digest=authority_subtree_digest,
        reporter_role="worker",
        report_digest=report_digest,
        evidence_digest=evidence_digest,
        dedup_identity=dedup,
        invalidated_obligation=invalidated_obligation,
        required_effects=tuple(required_effects),
        workspace_identity=workspace_identity,
    )


class _Plans:
    def __init__(self, active):
        self.active = active
        self.reads = 0

    def read_active(self, handle):
        self.reads += 1
        assert handle == self.active.handle
        return self.active


def _active_campaign(ticket_keys, *, work_facts=None):
    handle = CampaignHandle("owner/repository", "campaign:invalidation")
    work_facts = work_facts or {}
    work = [
        {
            "key": key,
            "depends_on": [],
            "exclusive_resources": [],
            "authority": {"worker": {"subtree_digest": "a" * 64}},
            **work_facts.get(key, {}),
        }
        for key in ticket_keys
    ]
    spec = {
        "schema_version": 3,
        "repository": handle.repository,
        "campaign": {"key": handle.campaign_key},
        "work": work,
    }
    payload = canonical_bytes(spec)
    revision = digest_bytes(payload)
    receipt = ActivationReceipt(
        repository=handle.repository,
        campaign_key=handle.campaign_key,
        revision_digest=revision,
        expected_previous_revision_digest=None,
        writer_generation="writer:one",
        ready_refs=ticket_keys,
        ticket_keys=ticket_keys,
        planning_subject_digest="b" * 64,
        planning_stable_action_id="campaign-plan:one",
        planning_preflight_receipt_digest="c" * 64,
        compilation_record_artifact_digest="d" * 64,
        planning_receipt_digest="e" * 64,
        planning_output_artifact_digest="f" * 64,
    )
    return (
        ActivePlanReadback(
            handle=handle,
            current_revision_digest=revision,
            plan_spec_bytes=payload,
            activation_receipt=receipt,
            claim_proofs=tuple(
                TicketClaimProof(
                    ticket_key=key,
                    repository=handle.repository,
                    campaign_key=handle.campaign_key,
                    plan_revision_digest=revision,
                )
                for key in ticket_keys
            ),
        ),
        handle,
    )


def _summary(diagnostics, ticket_key):
    return next(run for run in diagnostics.work_runs if run.ticket_key == ticket_key)


# ---------------------------------------------------------------------------
# Local effect doubles that exercise the Plan Invalidation path
# ---------------------------------------------------------------------------


class _PlanInvalidationEffects:
    """Minimal effects double mirroring ``_ScriptedEffects`` for #133."""

    def __init__(self):
        self.executed: list[WorkRunAction] = []
        self.observed: dict[str, str] = {}
        self.started: set[str] = set()

    def observe(self, ticket_key, phase):
        self.observed[ticket_key] = phase

    def readback(self, action):
        if action.kind == "semantic_resume":
            return None
        if action.ticket_key not in self.started:
            return None
        phase = self.observed.get(action.ticket_key)
        if phase is None:
            return None
        return _observation(phase, action.stable_action_id)

    def execute(self, action):
        self.executed.append(action)
        self.started.add(action.ticket_key)
        return _observation("running", action.stable_action_id)


class _BlockingInvalidationEffects(_PlanInvalidationEffects):
    def __init__(self):
        super().__init__()
        self._lock = threading.Lock()

    def execute(self, action):
        with self._lock:
            return super().execute(action)


def _observation(phase, action_id):
    return WorkRunObservation(
        phase=phase,
        stable_action_id=action_id,
        receipt_digest=digest_value({"phase": phase, "action": action_id}),
        binding_established=True,
    )