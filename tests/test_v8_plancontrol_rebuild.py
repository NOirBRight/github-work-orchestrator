from __future__ import annotations

import sys
from pathlib import Path

import pytest


SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "orchestrator" / "scripts"
sys.path.insert(0, str(SCRIPTS))


def test_plan_control_preflights_before_claiming_or_planning():
    from gwo_v8.plan_control import InMemoryPlanRepository, PlanControl

    repository = InMemoryPlanRepository(writer_generation="writer:one")
    calls: list[str] = []

    class Source:
        def snapshot(self, name, refs):
            assert name == "owner/repository"
            assert refs == ("issue:109",)
            return _snapshot()

    artifacts = _Artifacts()

    class Gateway:
        def planning_preflight(self, subject):
            assert repository.claims == {}
            calls.append("preflight")
            return type(
                "Preflight",
                (),
                {
                    "subject_digest": subject.digest,
                    "stable_action_id": subject.stable_action_id,
                    "receipt_digest": "3" * 64,
                },
            )()

        def progress(self, subject, preflight):
            assert preflight is not None
            assert repository.claims == {}
            calls.append("planning")
            output = artifacts.put_canonical(
                {
                    "schema_version": "gwo.runtime.output.v1",
                    "subject_digest": subject.digest,
                    "stable_action_id": subject.stable_action_id,
                    "authority_digest": subject.authority_digest,
                    "payload": _intent(),
                }
            )
            return type(
                "Receipt",
                (),
                {
                    "subject_digest": subject.digest,
                    "stable_action_id": subject.stable_action_id,
                    "status": "completed",
                    "receipt_digest": "4" * 64,
                    "planning_output_artifact_digest": output.digest,
                },
            )()

    handle = PlanControl(
        source=Source(), artifacts=artifacts, gateway=Gateway(), repository=repository
    ).start("owner/repository", ["issue:109"])

    assert calls == ["preflight", "planning"]
    assert handle.repository == "owner/repository"
    assert repository.active_receipt(handle).revision_digest == repository.claims[
        ("owner/repository", "issue:109")
    ]


def _snapshot():
    import hashlib
    import json

    policy = {
        "schema_version": 1,
        "ref": "policy:one",
        "authority_grants": {
            role: [
                {
                    "operation_id": "repository.read.v1",
                    "resource_id": "campaign.snapshot.v1",
                }
            ]
            for role in ("campaign", "worker", "recovery_worker", "review")
        },
        "allowed_capabilities": ["git", "local_check"],
        "exclusive_resources": ["repository.target.v1"],
    }
    policy["digest"] = hashlib.sha256(
        json.dumps(policy, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return {
        "repository": "owner/repository",
        "target_branch": "main",
        "campaign_source": {"ref": "refs/heads/main", "digest": "1" * 64},
        "policy": policy,
        "tickets": [
            {
                "key": "issue:109",
                "labels": ["ready-for-agent"],
                "source": {"ref": "issue:109", "digest": "2" * 64},
                "contract": {"title": "Contract", "body": "Do the work"},
                "native_blockers": [],
            }
        ],
    }


def _intent(ticket_key="issue:109"):
    return {
        "admitted_work": [ticket_key],
        "dependency_additions": [],
        "exclusive_resources": {ticket_key: []},
        "capability_requirements": {ticket_key: ["git", "local_check"]},
        "decision_requirements": [],
    }


class _Artifacts:
    def __init__(self):
        self.values = {}

    def put_canonical(self, value):
        import hashlib
        import json

        raw = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
        digest = hashlib.sha256(raw).hexdigest()
        self.values[digest] = value
        return type("Ref", (), {"digest": digest})()

    def get(self, digest):
        value = self.values[digest]
        import json

        raw = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
        return type(
            "Ref",
            (),
            {"digest": digest, "byte_length": len(raw), "path": f"memory:{digest}"},
        )()

    def read_json(self, digest):
        import json

        return json.loads(
            json.dumps(
                self.values[digest],
                sort_keys=True,
                separators=(",", ":"),
            )
        )


class _Gateway:
    def __init__(self, artifacts, *, intent=None, bad_preflight=False, pending=False):
        self.artifacts = artifacts
        self.intent = _intent() if intent is None else intent
        self.bad_preflight = bad_preflight
        self.pending = pending
        self.preflights = []
        self.progresses = []
        self.output_digests = []

    def planning_preflight(self, subject):
        self.preflights.append(subject)
        return type(
            "Preflight",
            (),
            {
                "subject_digest": "0" * 64 if self.bad_preflight else subject.digest,
                "stable_action_id": subject.stable_action_id,
                "receipt_digest": "5" * 64,
            },
        )()

    def progress(self, subject, preflight):
        self.progresses.append(subject)
        if self.pending:
            return type(
                "PendingReceipt",
                (),
                {
                    "subject_digest": subject.digest,
                    "stable_action_id": subject.stable_action_id,
                    "status": "running",
                    "receipt_digest": "6" * 64,
                    "planning_output_artifact_digest": None,
                },
            )()
        output = self.artifacts.put_canonical(
            {
                "schema_version": "gwo.runtime.output.v1",
                "subject_digest": subject.digest,
                "stable_action_id": subject.stable_action_id,
                "authority_digest": subject.authority_digest,
                "payload": self.intent,
            }
        )
        self.output_digests.append(output.digest)
        return type(
            "Receipt",
            (),
            {
                "subject_digest": subject.digest,
                "stable_action_id": subject.stable_action_id,
                "status": "completed",
                "receipt_digest": "7" * 64,
                "planning_output_artifact_digest": output.digest,
            },
        )()


class _Source:
    def __init__(self, snapshot=None):
        self.value = _snapshot() if snapshot is None else snapshot
        self.calls = 0

    def snapshot(self, repository, refs):
        assert repository == "owner/repository"
        self.calls += 1
        return self.value


def _control(*, source=None, artifacts=None, gateway=None, repository=None):
    from gwo_v8.plan_control import InMemoryPlanRepository, PlanControl

    artifacts = artifacts or _Artifacts()
    gateway = gateway or _Gateway(artifacts)
    repository = repository or InMemoryPlanRepository(writer_generation="writer:one")
    return (
        PlanControl(
            source=source or _Source(),
            artifacts=artifacts,
            gateway=gateway,
            repository=repository,
        ),
        artifacts,
        gateway,
        repository,
    )


def test_real_artifact_store_contract_is_used_for_plancontrol_reads(tmp_path):
    from gwo_v8.plan_control import PlanControl
    from gwo_v8.runtime_gateway import ArtifactStore

    artifacts = ArtifactStore(tmp_path / "artifacts")
    gateway = _Gateway(artifacts)
    repository = __import__(
        "gwo_v8.plan_control", fromlist=["InMemoryPlanRepository"]
    ).InMemoryPlanRepository(writer_generation="writer:one")

    handle = PlanControl(
        source=_Source(),
        artifacts=artifacts,
        gateway=gateway,
        repository=repository,
    ).start("owner/repository", ["issue:109"])

    active = PlanControl(
        source=_Source(),
        artifacts=artifacts,
        gateway=gateway,
        repository=repository,
    ).read_active(handle)
    assert artifacts.get(active.current_revision_digest).digest == (
        active.current_revision_digest
    )
    assert artifacts.read_json(active.current_revision_digest)["schema_version"] == 3


def test_full_policy_and_planspec_artifacts_read_back_at_authority_digests():
    control, artifacts, _gateway, repository = _control()

    handle = control.start("owner/repository", ["issue:109"])
    receipt = repository.active_receipt(handle)
    revision = repository.read_revision(receipt.revision_digest)
    plan = artifacts.read_json(revision.digest)
    policy_digest = plan["policy"]["digest"]

    assert plan == revision.plan_spec
    assert artifacts.read_json(policy_digest) == {
        key: value for key, value in _snapshot()["policy"].items() if key != "digest"
    }
    authority = plan["work"][0]["authority"]
    assert set(authority) == {"policy_witness_digest", "worker", "recovery_worker", "review"}
    assert authority["policy_witness_digest"] == policy_digest
    for role in ("worker", "recovery_worker", "review"):
        assert set(authority[role]) == {"policy_witness_digest", "grants", "subtree_digest"}
        assert authority[role]["policy_witness_digest"] == policy_digest
        assert authority[role]["grants"]


def test_active_readback_is_immutable_and_fails_closed_for_pending_plan_or_claim_mismatch():
    from gwo_v8.plan_control import CampaignHandle, PlanControlError

    control, _artifacts, _gateway, repository = _control()
    handle = control.start("owner/repository", ["issue:109"])
    active = control.read_active(handle)

    assert active.current_revision_digest == active.activation_receipt.revision_digest
    assert active.plan_spec_bytes == repository.read_revision(active.current_revision_digest).canonical_bytes
    assert [proof.ticket_key for proof in active.claim_proofs] == ["issue:109"]

    repository.claims[("owner/repository", "issue:109")] = "0" * 64
    try:
        control.read_active(handle)
    except PlanControlError as error:
        assert error.code == "TICKET_CLAIM_READBACK_INVALID"
    else:
        raise AssertionError("changed claim proof must fail closed")

    try:
        control.read_active(CampaignHandle("owner/repository", "campaign:pending"))
    except PlanControlError as error:
        assert error.code == "ACTIVATION_PENDING"
    else:
        raise AssertionError("unactivated Campaign must not have an active readback")


@pytest.mark.parametrize("boundary", ["reserve_claims", "publish_revision", "activate"])
def test_restart_across_activation_boundaries_reuses_one_planning_receipt_and_intent(boundary):
    from gwo_v8.plan_control import PlanControlError

    class CrashOnceRepository:
        def __init__(self):
            from gwo_v8.plan_control import InMemoryPlanRepository

            self.inner = InMemoryPlanRepository(writer_generation="writer:one")
            self.writer_generation = self.inner.writer_generation
            self.crashed = False

        def __getattr__(self, name):
            return getattr(self.inner, name)

        def reserve_claims(self, *args):
            result = self.inner.reserve_claims(*args)
            self._crash("reserve_claims")
            return result

        def publish_revision(self, *args):
            result = self.inner.publish_revision(*args)
            self._crash("publish_revision")
            return result

        def activate(self, *args):
            result = self.inner.activate(*args)
            self._crash("activate")
            return result

        def _crash(self, current):
            if current == boundary and not self.crashed:
                self.crashed = True
                raise PlanControlError("DURABLE_STATE_AMBIGUOUS", "synthetic activation ambiguity")

    artifacts = _Artifacts()
    gateway = _Gateway(artifacts)
    repository = CrashOnceRepository()
    control, _, _, _ = _control(artifacts=artifacts, gateway=gateway, repository=repository)

    try:
        control.start("owner/repository", ["issue:109"])
    except PlanControlError as error:
        assert error.code == "DURABLE_STATE_AMBIGUOUS"
    else:
        raise AssertionError("first publication must crash")
    handle = control.start("owner/repository", ["issue:109"])

    assert handle.repository == "owner/repository"
    assert len(gateway.preflights) == 1
    assert len(gateway.progresses) == 1
    assert repository.active_receipt(handle) is not None


def test_restart_revalidates_the_bound_gateway_output_before_reusing_compilation():
    from gwo_v8.plan_control import InMemoryPlanRepository, PlanControlError

    class CrashAfterReservation:
        def __init__(self):
            self.inner = InMemoryPlanRepository(writer_generation="writer:one")
            self.writer_generation = self.inner.writer_generation
            self.crashed = False

        def __getattr__(self, name):
            return getattr(self.inner, name)

        def reserve_claims(self, receipt):
            self.inner.reserve_claims(receipt)
            if not self.crashed:
                self.crashed = True
                raise PlanControlError(
                    "DURABLE_STATE_AMBIGUOUS",
                    "synthetic reservation crash",
                )

    artifacts = _Artifacts()
    gateway = _Gateway(artifacts)
    repository = CrashAfterReservation()
    control, _, _, _ = _control(
        artifacts=artifacts,
        gateway=gateway,
        repository=repository,
    )

    with pytest.raises(PlanControlError) as crashed:
        control.start("owner/repository", ["issue:109"])
    assert crashed.value.code == "DURABLE_STATE_AMBIGUOUS"
    assert len(gateway.output_digests) == 1

    artifacts.values[gateway.output_digests[0]]["payload"]["admitted_work"] = [
        "issue:replacement"
    ]
    with pytest.raises(PlanControlError) as changed:
        control.start("owner/repository", ["issue:109"])
    assert changed.value.code == "COMPILATION_RECORD_INVALID"
    assert len(gateway.progresses) == 1
    assert repository.active_receipt(
        __import__(
            "gwo_v8.plan_control",
            fromlist=["CampaignHandle"],
        ).CampaignHandle(
            "owner/repository",
            "campaign:2cc514aa7aef8939eb3e8c86",
        )
    ) is None


def test_successor_revision_keeps_handle_and_uses_exact_previous_digest():
    control, _artifacts, gateway, repository = _control()
    first = control.start("owner/repository", ["issue:109"])
    previous = repository.active_receipt(first).revision_digest
    changed = _snapshot()
    changed["tickets"][0]["contract"]["body"] = "Changed frozen contract"
    control, _, _, _ = _control(
        source=_Source(changed),
        artifacts=_artifacts,
        gateway=gateway,
        repository=repository,
    )

    successor = control.start(
        "owner/repository",
        ["issue:109"],
        campaign_key=first.campaign_key,
        expected_previous_revision_digest=previous,
    )
    receipt = repository.active_receipt(successor)

    assert successor == first
    assert receipt.expected_previous_revision_digest == previous
    assert receipt.revision_digest != previous
    assert len(gateway.progresses) == 2


def test_interleaved_successors_keep_old_claims_until_winning_activation_readback():
    from gwo_v8.plan_control import (
        ActivationReceipt,
        InMemoryPlanRepository,
        PlanControlError,
    )

    artifacts = _Artifacts()
    repository = InMemoryPlanRepository(writer_generation="writer:one")
    first_gateway = _Gateway(artifacts)
    first, _, _, _ = _control(
        artifacts=artifacts,
        gateway=first_gateway,
        repository=repository,
    )
    handle = first.start("owner/repository", ["issue:109"])
    previous = repository.active_receipt(handle).revision_digest
    losing = ActivationReceipt(
        repository=handle.repository,
        campaign_key=handle.campaign_key,
        revision_digest="a" * 64,
        expected_previous_revision_digest=previous,
        writer_generation=repository.writer_generation,
        ticket_keys=("issue:109",),
    )
    winning = ActivationReceipt(
        repository=handle.repository,
        campaign_key=handle.campaign_key,
        revision_digest="b" * 64,
        expected_previous_revision_digest=previous,
        writer_generation=repository.writer_generation,
        ticket_keys=("issue:109",),
    )

    repository.reserve_claims(losing)
    repository.reserve_claims(winning)
    assert repository.claims[("owner/repository", "issue:109")] == previous

    repository.activate(winning)
    assert repository.read_activation(handle) == winning
    assert repository.claims[("owner/repository", "issue:109")] == previous
    repository.finalize_claims(winning)

    with pytest.raises(PlanControlError) as conflict:
        repository.activate(losing)
    assert conflict.value.code == "ACTIVATION_CAS_CONFLICT"
    assert repository.active_receipt(handle) == winning
    assert repository.claims[("owner/repository", "issue:109")] == winning.revision_digest


def test_claim_identity_is_repository_scoped_and_active_readback_is_campaign_scoped():
    from gwo_v8.plan_control import InMemoryPlanRepository, PlanControl

    artifacts = _Artifacts()
    repository = InMemoryPlanRepository(writer_generation="writer:one")

    class Source:
        def __init__(self, name, ticket_key):
            self.name = name
            self.ticket_key = ticket_key

        def snapshot(self, name, refs):
            assert name == self.name
            value = _snapshot()
            value["repository"] = name
            value["tickets"][0]["key"] = self.ticket_key
            value["tickets"][0]["source"]["ref"] = self.ticket_key
            return value

    def start(name, ticket_key, campaign_key):
        gateway = _Gateway(artifacts, intent=_intent(ticket_key))
        control = PlanControl(
            source=Source(name, ticket_key),
            artifacts=artifacts,
            gateway=gateway,
            repository=repository,
        )
        handle = control.start(name, [ticket_key], campaign_key=campaign_key)
        return control, handle

    first, first_handle = start(
        "owner/repository-a", "issue:109", "campaign:first"
    )
    _second, _second_handle = start(
        "owner/repository-b", "issue:109", "campaign:second"
    )
    _third, _third_handle = start(
        "owner/repository-a", "issue:110", "campaign:third"
    )

    active = first.read_active(first_handle)
    assert [(proof.repository, proof.ticket_key, proof.campaign_key) for proof in active.claim_proofs] == [
        ("owner/repository-a", "issue:109", "campaign:first")
    ]
    assert set(repository.claims) == {
        ("owner/repository-a", "issue:109"),
        ("owner/repository-b", "issue:109"),
        ("owner/repository-a", "issue:110"),
    }


def test_overlap_fails_closed_after_planning_and_before_publication():
    from gwo_v8.plan_control import PlanControlError

    control, artifacts, _gateway, repository = _control()
    control.start("owner/repository", ["issue:109"])
    second_gateway = _Gateway(artifacts)
    second, _, _, _ = _control(artifacts=artifacts, gateway=second_gateway, repository=repository)

    try:
        second.start("owner/repository", ["issue:109"], campaign_key="campaign:other")
    except PlanControlError as error:
        assert error.code == "TICKET_CLAIM_CONFLICT"
    else:
        raise AssertionError("overlapping Ticket claim must fail")
    assert len(second_gateway.preflights) == 1
    assert len(second_gateway.progresses) == 1
    assert len(repository.revisions) == 1


def test_invalid_preflight_or_intent_fails_before_claim_or_publication():
    from gwo_v8.plan_control import PlanControlError

    control, _artifacts, _gateway, repository = _control(gateway=_Gateway(_Artifacts(), bad_preflight=True))
    try:
        control.start("owner/repository", ["issue:109"])
    except PlanControlError as error:
        assert error.code == "RUNTIME_PREFLIGHT_INVALID"
    else:
        raise AssertionError("wrong preflight receipt must fail")
    assert repository.claims == {}
    assert repository.revisions == {}

    artifacts = _Artifacts()
    invalid = _intent()
    invalid["provider"] = "forbidden"
    control, _, _, repository = _control(artifacts=artifacts, gateway=_Gateway(artifacts, intent=invalid))
    try:
        control.start("owner/repository", ["issue:109"])
    except PlanControlError as error:
        assert error.code == "PLAN_INTENT_INVALID"
    else:
        raise AssertionError("widened Plan Intent must fail")
    assert repository.claims == {}
    assert repository.revisions == {}


def test_open_external_blocker_and_pending_planning_do_not_activate():
    from gwo_v8.plan_control import PlanControlError

    blocked = _snapshot()
    blocked["tickets"][0]["native_blockers"] = [{"key": "issue:108", "state": "open"}]
    control, _, _, repository = _control(source=_Source(blocked))
    try:
        control.start("owner/repository", ["issue:109"])
    except PlanControlError as error:
        assert error.code == "EXTERNAL_BLOCKER_OPEN"
    else:
        raise AssertionError("open external blockers must fail before planning")
    assert repository.claims == {}

    artifacts = _Artifacts()
    gateway = _Gateway(artifacts, pending=True)
    control, _, _, repository = _control(artifacts=artifacts, gateway=gateway)
    handle = control.start("owner/repository", ["issue:109"])
    assert repository.active_receipt(handle) is None
    assert repository.claims == {}
    assert len(gateway.progresses) == 1


def test_oversized_snapshot_persists_one_typed_split_campaign_decision():
    from gwo_v8.plan_control import (
        InMemoryPlanRepository,
        PlanControl,
        SplitCampaignDecision,
    )

    source = _Source()
    artifacts = _Artifacts()
    gateway = _Gateway(artifacts)
    repository = InMemoryPlanRepository(writer_generation="writer:one")
    control = PlanControl(
        source=source,
        artifacts=artifacts,
        gateway=gateway,
        repository=repository,
        max_snapshot_bytes=128,
    )

    with pytest.raises(SplitCampaignDecision) as first:
        control.start("owner/repository", ["issue:109"])
    with pytest.raises(SplitCampaignDecision) as restarted:
        control.start("owner/repository", ["issue:109"])

    assert restarted.value.decision_digest == first.value.decision_digest
    assert restarted.value.snapshot_digest == first.value.snapshot_digest
    assert restarted.value.handle == first.value.handle
    assert restarted.value.snapshot_byte_length > restarted.value.maximum_snapshot_bytes
    assert source.calls == 1
    assert gateway.preflights == []
    assert gateway.progresses == []
    assert repository.claims == {}


def test_installed_public_start_persists_exact_runtime_overrides_outside_planspec(
    tmp_path,
    monkeypatch,
):
    import gwo_v8.plan_control as plan_module
    from gwo_v8.plan_control import InMemoryPlanRepository, PlanControlError
    from gwo_v8.plan_control_host import install_plan_control_start
    from gwo_v8.runtime_profile import RuntimeProfile
    from gwo_v8.runtime_gateway import (
        ArtifactStore,
        CampaignStartRuntimeOverrides,
        ProfileMapping,
        RuntimeConfiguration,
    )

    monkeypatch.setattr(plan_module, "_default_start_host", None)
    profile = RuntimeProfile(
        name="host",
        provider="test-provider",
        model="model:host",
        thinking="high",
        mode="safe",
        features={},
    )
    alternate = RuntimeProfile(
        name="alternate",
        provider="test-provider",
        model="model:alternate",
        thinking="high",
        mode="safe",
        features={},
    )
    configuration = RuntimeConfiguration(
        profiles={profile.digest: profile, alternate.digest: alternate},
        host_mappings={"coordinator": ProfileMapping(profile.digest)},
    )
    repository = InMemoryPlanRepository(writer_generation="writer:one")
    composed = []

    def gateway_builder(*, configuration, artifacts, **_kwargs):
        assert type(artifacts) is ArtifactStore
        composed.append((configuration, artifacts))
        return _Gateway(artifacts)

    install_plan_control_start(
        source=_Source(),
        repository=repository,
        runtime_configuration=configuration,
        repository_contexts={},
        gateway_store_path=tmp_path / "gateway.json",
        artifact_root=tmp_path / "artifacts",
        _gateway_builder=gateway_builder,
    )
    options = {
        "coordinator": {
            "primary_profile_digest": alternate.digest,
            "availability_fallback_profile_digest": profile.digest,
        },
        "ticket_overrides": [
            {
                "ticket_key": "issue:109",
                "role": "worker",
                "mapping": {
                    "primary_profile_digest": alternate.digest,
                    "availability_fallback_profile_digest": None,
                },
            }
        ],
    }

    handle = plan_module.start(
        "owner/repository",
        ["issue:109"],
        options,
    )
    assertion = CampaignStartRuntimeOverrides(
        coordinator=ProfileMapping(alternate.digest, profile.digest),
        ticket_overrides={
            ("issue:109", "worker"): ProfileMapping(alternate.digest)
        },
    )
    assert repository.read_runtime_assertion(handle) == assertion.canonical()
    key = (
        handle.repository,
        handle.campaign_key,
        "campaign-handle:"
        + __import__("gwo_v8._canonical", fromlist=["digest_value"]).digest_value(
            handle.__dict__
        ),
    )
    assert composed[-1][0].campaign_assertions[key] == assertion

    active = repository.active_receipt(handle)
    plan = repository.read_revision(active.revision_digest).plan_spec
    assert alternate.digest not in repr(plan)
    assert profile.digest not in repr(plan)
    assert plan_module.start("owner/repository", ["issue:109"]) == handle

    with pytest.raises(PlanControlError) as invalid:
        plan_module.start(
            "owner/repository",
            ["issue:109"],
            {"coordinator": None, "ticket_overrides": [], "unknown": True},
        )
    assert invalid.value.code == "START_OPTIONS_INVALID"


def test_rebuild_has_no_predecessor_v3_runtime_or_profile_path():
    source = (
        Path(__file__).resolve().parents[1]
        / "skills"
        / "orchestrator"
        / "scripts"
        / "gwo_v8"
        / "plan_control.py"
    ).read_text(encoding="utf-8")

    for forbidden in (
        "_v3_",
        "RuntimeProfile",
        "Paseo",
        "from .runtime import",
        "RuntimeConfiguration",
    ):
        assert forbidden not in source
