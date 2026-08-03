from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import sys

import pytest


SCRIPTS = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "orchestrator"
    / "scripts"
)
sys.path.insert(0, str(SCRIPTS))


class _RawGateway:
    def __init__(self, capability):
        self.capability = capability
        self.subjects = []

    def _read_coordinator_capability(self, subject):
        self.subjects.append(subject)
        return self.capability


class _ControlDouble:
    instances = []

    def __init__(
        self,
        *,
        source,
        artifacts,
        gateway,
        repository,
        max_snapshot_bytes,
    ):
        self.source = source
        self.artifacts = artifacts
        self.gateway = gateway
        self.repository = repository
        self.max_snapshot_bytes = max_snapshot_bytes
        self.active_result = object()
        self.classification_result = object()
        self.activation_result = object()
        self.human_source_result = object()
        self.calls = []
        type(self).instances.append(self)

    def read_active(self, handle):
        self.calls.append(("read_active", handle))
        return self.active_result

    def classify_plan_invalidations(self, handle, invalidations, execution_snapshot):
        self.calls.append(
            ("classify_plan_invalidations", handle, invalidations, execution_snapshot)
        )
        return self.classification_result

    def activate_successor(self, handle, classification):
        self.calls.append(("activate_successor", handle, classification))
        return self.activation_result

    def read_human_decision_source(self, handle, decision, choice):
        self.calls.append(("read_human_decision_source", handle, decision, choice))
        return self.human_source_result


class _ProductionPlanningAdapter:
    """Small deterministic provider seam behind the real RuntimeGateway."""

    def __init__(self, artifacts, successor_payload):
        from gwo_v8.runtime_gateway import _InMemoryRuntimeProviderAdapter

        self._inner = _InMemoryRuntimeProviderAdapter(artifacts)
        self._artifacts = artifacts
        self._successor_payload = deepcopy(successor_payload)

    def __getattr__(self, name):
        return getattr(self._inner, name)

    def _complete_action(self, action):
        from gwo_v8.runtime_gateway import (
            _RUNTIME_OUTPUT_SCHEMA_VERSION,
            _RuntimeOutputIdentity,
        )

        subject = action.spec.subject
        if subject.stable_action_id.startswith("replan:"):
            payload = deepcopy(self._successor_payload)
        else:
            ticket_keys = [
                "issue:108",
                "issue:109",
                "issue:110",
            ]
            payload = {
                "admitted_work": ticket_keys,
                "dependency_additions": [],
                "exclusive_resources": {key: [] for key in ticket_keys},
                "capability_requirements": {
                    key: ["git", "local_check"] for key in ticket_keys
                },
                "decision_requirements": [],
            }
        identity = _RuntimeOutputIdentity(
            subject_digest=subject.digest,
            stable_action_id=subject.stable_action_id,
            authority_digest=subject.authority_digest,
        )
        action.output_artifact_digest = self._artifacts.put_canonical(
            {
                "schema_version": _RUNTIME_OUTPUT_SCHEMA_VERSION,
                **identity.canonical(),
                "payload": payload,
            }
        ).digest
        action.lifecycle = "completed"

    def prepare(self, spec):
        return self._inner.prepare(spec)

    def observe(self, stable_action_id):
        return self._inner.observe(stable_action_id)

    def command(self, stable_action_id, transition):
        original = self._inner._complete_action
        self._inner._complete_action = self._complete_action
        try:
            return self._inner.command(stable_action_id, transition)
        finally:
            self._inner._complete_action = original

    def read_wake(self, stable_action_id, wake_cursor=None):
        return self._inner.read_wake(stable_action_id, wake_cursor)


class _ProductionRuntimeGateway:
    """Real RuntimeGateway with only its provider adapter/counters controlled."""

    def __init__(self, *, store_path, configuration, artifacts, successor_payload):
        from gwo_v8.runtime_gateway import RuntimeGateway

        self._inner = RuntimeGateway(
            store_path=store_path,
            _adapter=_ProductionPlanningAdapter(artifacts, successor_payload),
            configuration=configuration,
            _artifacts=artifacts,
        )
        self.planning_progresses = 0
        self.replan_progresses = 0
        self.capability_subjects = []
        self._artifacts = artifacts

    def __getattr__(self, name):
        return getattr(self._inner, name)

    def planning_preflight(self, subject):
        return self._inner.planning_preflight(subject)

    def _read_coordinator_capability(self, subject):
        self.capability_subjects.append(subject)
        return self._inner._read_coordinator_capability(subject)

    def progress(self, subject, preflight):
        if subject.stable_action_id.startswith("replan:"):
            self.replan_progresses += 1
        else:
            self.planning_progresses += 1
        return self._inner.progress(subject, preflight)


class _ProductionSource:
    def __init__(self, snapshot):
        self._snapshot = deepcopy(snapshot)

    def snapshot(self, repository, ready_refs):
        assert repository == "owner/repository"
        assert tuple(ready_refs) == (
            "issue:108",
            "issue:109",
            "issue:110",
        )
        return {
            key: deepcopy(self._snapshot[key])
            for key in (
                "repository",
                "target_branch",
                "campaign_source",
                "policy",
                "tickets",
            )
        }


def _production_execution_snapshot(active):
    return {
        "runs": [
            {
                "ticket_key": key,
                "work_run_key": f"work-run:{key}",
                "phase": "quiescent",
                "slot_held": False,
                "reason": "PlanInvalidation",
                "next_check_at": None,
                "runtime_binding_id": f"binding:{key}",
                "claim_state": "released",
                "exclusive_resources": [],
            }
            for key in ("issue:108", "issue:109", "issue:110")
        ],
        "claims": [
            {
                "ticket_key": proof.ticket_key,
                "repository": proof.repository,
                "campaign_key": proof.campaign_key,
                "plan_revision_digest": proof.plan_revision_digest,
            }
            for proof in active.claim_proofs
        ],
        "accepted_results": [],
    }


def _production_invalidation(host, handle):
    from gwo_v8._canonical import load_canonical_json
    from gwo_v8.execution_kernel import PlanInvalidationObservation

    active = host.read_active(handle)
    plan = load_canonical_json(active.plan_spec_bytes)
    item = next(item for item in plan["work"] if item["key"] == "issue:109")
    return PlanInvalidationObservation(
        repository=handle.repository,
        campaign_key=handle.campaign_key,
        plan_revision_digest=active.current_revision_digest,
        ticket_key="issue:109",
        work_run_key="work-run:issue:109",
        runtime_binding_id="binding:issue:109",
        authority_subtree_digest=item["authority"]["worker"]["subtree_digest"],
        reporter_role="worker",
        report_digest="9" * 64,
        evidence_digest="9" * 64,
        dedup_identity="successor:production",
        invalidated_obligation="The invalidated work discovered one approved dependency.",
        required_effects=("workspace.write.v1",),
        workspace_identity="workspace:issue:109",
    )


@pytest.fixture
def production_successor_context(tmp_path):
    from gwo_v8._canonical import digest_value
    from gwo_v8.plan_control import CampaignHandle, InMemoryPlanRepository, _handle_ref
    from gwo_v8.runtime_gateway import (
        CampaignStartRuntimeOverrides,
        ProfileMapping,
        RuntimeConfiguration,
    )
    from gwo_v8.runtime_profile import RuntimeProfile
    from v8_successor_test_support import successor_payload, three_ticket_source_snapshot
    from gwo_v8.plan_control_host import ProductionPlanControlStartHost

    repository_name = "owner/repository"
    ready_refs = ("issue:108", "issue:109", "issue:110")
    campaign_key = "campaign:" + digest_value(
        {"repository": repository_name, "ready_refs": list(ready_refs)}
    )[:24]
    handle = CampaignHandle(repository_name, campaign_key)
    profile = RuntimeProfile(
        name="coordinator",
        provider="test",
        model="model:test",
        thinking="high",
        mode="safe",
        features={},
    )
    mapping = ProfileMapping(profile.digest)
    assertion = CampaignStartRuntimeOverrides(coordinator=mapping)
    assertion_key = (handle.repository, handle.campaign_key, _handle_ref(handle))
    configuration = RuntimeConfiguration(
        profiles={profile.digest: profile},
        host_mappings={"coordinator": mapping},
        campaign_assertions={assertion_key: assertion},
    )
    successor = successor_payload(
        owners=("issue:109",),
        dependencies=(
            (
                "issue:109",
                "issue:110",
                "The invalidated owner consumes the approved prerequisite.",
            ),
        ),
    )
    builder_calls = []
    gateway_holder = {}

    def builder(**kwargs):
        builder_calls.append(kwargs)
        if "gateway" not in gateway_holder:
            gateway_holder["gateway"] = _ProductionRuntimeGateway(
                store_path=kwargs["gateway_store_path"],
                configuration=kwargs["configuration"],
                artifacts=kwargs["artifacts"],
                successor_payload=successor,
            )
        return gateway_holder["gateway"]

    host = ProductionPlanControlStartHost(
        source=_ProductionSource(three_ticket_source_snapshot()),
        repository=InMemoryPlanRepository(writer_generation="writer:successor"),
        runtime_configuration=configuration,
        repository_contexts={},
        gateway_store_path=tmp_path / "runtime.json",
        artifact_root=tmp_path / "artifacts",
        _gateway_builder=builder,
    )
    started = host.start(repository_name, ready_refs)
    assert started == handle
    return host, handle, assertion, gateway_holder["gateway"], builder_calls


@pytest.fixture
def host_context(tmp_path, monkeypatch):
    from gwo_v8 import plan_control_host
    from gwo_v8.plan_control import CampaignHandle, _handle_ref
    from gwo_v8.runtime_gateway import (
        CampaignStartRuntimeOverrides,
        ProfileMapping,
        RuntimeConfiguration,
    )
    from gwo_v8.runtime_profile import RuntimeProfile

    profile = RuntimeProfile(
        name="coordinator",
        provider="test",
        model="model:test",
        thinking="high",
        mode="safe",
        features={},
    )
    mapping = ProfileMapping(profile.digest)
    assertion = CampaignStartRuntimeOverrides(coordinator=mapping)
    handle = CampaignHandle("owner/repository", "campaign:successor")
    assertion_key = (handle.repository, handle.campaign_key, _handle_ref(handle))
    configuration = RuntimeConfiguration(
        profiles={profile.digest: profile},
        host_mappings={"coordinator": mapping},
        campaign_assertions={assertion_key: assertion},
    )
    raw_gateway = _RawGateway(capability=object())
    builder_calls = []

    def builder(**kwargs):
        builder_calls.append(kwargs)
        return raw_gateway

    class Source:
        pass

    class Repository:
        pass

    _ControlDouble.instances = []
    monkeypatch.setattr(plan_control_host, "PlanControl", _ControlDouble)
    host = plan_control_host.ProductionPlanControlStartHost(
        source=Source(),
        repository=Repository(),
        runtime_configuration=configuration,
        repository_contexts={},
        gateway_store_path=tmp_path / "gateway.json",
        artifact_root=tmp_path / "artifacts",
        _gateway_builder=builder,
    )
    return host, handle, assertion, raw_gateway, builder_calls


def test_plan_control_gateway_forwards_private_capability_readback():
    from gwo_v8.plan_control_host import _PlanControlGateway

    capability = object()
    subject = object()
    gateway = _RawGateway(capability)

    assert (
        _PlanControlGateway(gateway=gateway)._read_coordinator_capability(subject)
        is capability
    )
    assert gateway.subjects == [subject]


def test_installed_host_activates_without_start_successor(host_context):
    host, handle, _assertion, _raw_gateway, _builder_calls = host_context
    assert not _ControlDouble.instances

    def forbidden(*_args, **_kwargs):
        raise AssertionError("classified successor must not call start_successor")

    host.start_successor = forbidden
    classification = object()
    result = host.activate_successor(handle, classification)

    assert len(_ControlDouble.instances) == 1
    composed = _ControlDouble.instances[0]
    assert result is composed.activation_result
    assert composed.calls == [("activate_successor", handle, classification)]


def test_host_reuses_existing_campaign_runtime_assertion_and_artifact_store(
    host_context,
):
    host, handle, assertion, raw_gateway, builder_calls = host_context
    invalidations = (object(),)
    execution_snapshot = {"runs": []}
    classification = object()

    active = host.read_active(handle)
    composed_classification = host.classify_plan_invalidations(
        handle,
        invalidations,
        execution_snapshot,
    )
    composed_activation = host.activate_successor(handle, classification)

    assert len(_ControlDouble.instances) == 3
    controls = _ControlDouble.instances
    assert active is controls[0].active_result
    assert composed_classification is controls[1].classification_result
    assert composed_activation is controls[2].activation_result
    assert controls[0].calls == [("read_active", handle)]
    assert controls[1].calls == [
        ("classify_plan_invalidations", handle, invalidations, execution_snapshot)
    ]
    assert controls[2].calls == [("activate_successor", handle, classification)]

    assert len(builder_calls) == 3
    assert all(call["artifacts"] is host._artifacts for call in builder_calls)
    for call in builder_calls:
        assert tuple(call["configuration"].campaign_assertions.values()) == (
            assertion,
        )
        assert call["configuration"].profiles == host._configuration.profiles

    assert all(control.gateway._gateway is raw_gateway for control in controls)
    assert all(control.artifacts is host._artifacts for control in controls)
    subject = object()
    assert controls[1].gateway._read_coordinator_capability(subject) is raw_gateway.capability
    assert raw_gateway.subjects == [subject]


def test_host_forwards_authoritative_human_source_readback_through_existing_control(
    host_context,
):
    host, handle, _assertion, _raw_gateway, _builder_calls = host_context
    decision = object()
    choice = object()

    result = host.read_human_decision_source(handle, decision, choice)

    assert len(_ControlDouble.instances) == 1
    composed = _ControlDouble.instances[0]
    assert result is composed.human_source_result
    assert composed.calls == [
        ("read_human_decision_source", handle, decision, choice)
    ]


def test_host_injects_read_only_human_source_into_each_recomposed_control(
    host_context,
):
    from gwo_v8.plan_control_host import ProductionPlanControlStartHost

    host, handle, _assertion, _raw_gateway, _builder_calls = host_context
    source = type("Source", (), {"read": lambda *_args: None})()
    composed_host = ProductionPlanControlStartHost(
        source=host._source,
        repository=host._repository,
        runtime_configuration=host._configuration,
        repository_contexts=host._repository_contexts,
        gateway_store_path=host._gateway_store_path,
        artifact_root=host._artifacts._root,
        max_snapshot_bytes=host._max_snapshot_bytes,
        human_source=source,
        _gateway_builder=host._gateway_builder,
    )

    composed_host.read_active(handle)

    composed = _ControlDouble.instances[-1]
    assert composed_host._human_source is source
    assert composed._human_source is source


def test_production_host_classifies_and_activates_through_real_runtime_gateway(
    production_successor_context,
):
    from gwo_v8.plan_control import _handle_ref

    host, handle, assertion, gateway, builder_calls = production_successor_context
    before = host.read_active(handle)
    observation = _production_invalidation(host, handle)

    classification = host.classify_plan_invalidations(
        handle,
        (observation,),
        _production_execution_snapshot(before),
    )

    assert classification is not None
    assert gateway.planning_progresses == 1
    assert gateway.replan_progresses == 1
    assert len(gateway.capability_subjects) == 1
    assert gateway.capability_subjects[0].stable_action_id == classification.action_id

    def forbidden(*_args, **_kwargs):
        raise AssertionError("classified successor must not call start_successor")

    host.start_successor = forbidden
    readback = host.activate_successor(handle, classification)
    replay = host.activate_successor(handle, classification)

    assert readback == replay
    assert readback.handle == handle
    assert readback.activation_receipt.expected_previous_revision_digest == (
        before.current_revision_digest
    )
    assert readback.current_revision_digest != before.current_revision_digest
    assert gateway.replan_progresses == 1
    assert gateway.planning_progresses == 1
    assert gateway._artifacts is host._artifacts

    assertion_key = (handle.repository, handle.campaign_key, _handle_ref(handle))
    assert len(builder_calls) == 6
    for call in builder_calls:
        assert call["artifacts"] is host._artifacts
        assert call["configuration"].campaign_assertions[assertion_key] == assertion
