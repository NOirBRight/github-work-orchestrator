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
            from gwo_v8.runtime_gateway import PlanningPreflightReceipt

            assert repository.claims == {}
            calls.append("preflight")
            return PlanningPreflightReceipt(
                subject_digest=subject.digest,
                stable_action_id=subject.stable_action_id,
                receipt_digest="3" * 64,
            )

        def progress(self, subject, preflight):
            from gwo_v8.runtime_gateway import PlanningReceipt

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
            return PlanningReceipt(
                subject_digest=subject.digest,
                stable_action_id=subject.stable_action_id,
                status="completed",
                receipt_digest="4" * 64,
                output_artifact_digest=output.digest,
                planning_output_artifact_digest=output.digest,
            )

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
            "campaign": [
                {
                    "operation_id": "repository.read.v1",
                    "resource_id": "campaign.snapshot.v1",
                }
            ],
            "worker": [
                {
                    "operation_id": "workspace.write.v1",
                    "resource_id": "work-run.workspace.v1",
                }
            ],
            "recovery_worker": [
                {
                    "operation_id": "workspace.write.v1",
                    "resource_id": "work-run.workspace.v1",
                }
            ],
            "review": [
                {
                    "operation_id": "repository.read.v1",
                    "resource_id": "review.subject.v1",
                }
            ],
        },
        "allowed_capabilities": ["git", "local_check"],
        "exclusive_resources": ["repository.target.v1"],
    }
    policy["digest"] = hashlib.sha256(
        json.dumps(policy, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    campaign_source = {
        "repository": "owner/repository",
        "input_ref": "refs/heads/main",
        "resolved_commit_oid": "a" * 40,
        "tree_oid": "b" * 40,
    }
    return {
        "repository": "owner/repository",
        "target_branch": "main",
        "campaign_source": {
            **campaign_source,
            "digest": hashlib.sha256(
                json.dumps(
                    campaign_source,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode()
            ).hexdigest(),
        },
        "policy": policy,
        "tickets": [
            {
                "key": "issue:109",
                "labels": ["ready-for-agent"],
                "source": {"ref": "issue:109", "digest": "2" * 64},
                "contract": {
                    "id": 109,
                    "node_id": "ISSUE_109",
                    "title": "Contract",
                    "body": "Do the work",
                    "state": "open",
                    "state_reason": None,
                    "type": None,
                    "repository": {
                        "full_name": "owner/repository",
                        "url": "https://api.github.com/repos/owner/repository",
                    },
                    "labels": [
                        {
                            "id": 1,
                            "node_id": "LABEL_1",
                            "url": "https://api.github.com/repos/owner/repository/labels/ready-for-agent",
                            "name": "ready-for-agent",
                            "color": "0052cc",
                            "default": False,
                            "description": "ready",
                        }
                    ],
                    "comments": [],
                    "updated_at": "2026-07-30T00:00:00Z",
                },
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
        from gwo_v8.runtime_gateway import PlanningPreflightReceipt

        self.preflights.append(subject)
        return PlanningPreflightReceipt(
            subject_digest="0" * 64 if self.bad_preflight else subject.digest,
            stable_action_id=subject.stable_action_id,
            receipt_digest="5" * 64,
        )

    def progress(self, subject, preflight):
        from gwo_v8.runtime_gateway import PlanningReceipt

        self.progresses.append(subject)
        if self.pending:
            return PlanningReceipt(
                subject_digest=subject.digest,
                stable_action_id=subject.stable_action_id,
                status="running",
                receipt_digest="6" * 64,
            )
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
        return PlanningReceipt(
            subject_digest=subject.digest,
            stable_action_id=subject.stable_action_id,
            status="completed",
            receipt_digest="7" * 64,
            output_artifact_digest=output.digest,
            planning_output_artifact_digest=output.digest,
        )


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


def test_real_gateway_executes_the_closed_five_field_planning_protocol(tmp_path):
    from gwo_v8.plan_control import InMemoryPlanRepository, PlanControl
    from gwo_v8.runtime_gateway import (
        ArtifactStore,
        ProfileMapping,
        RuntimeConfiguration,
        RuntimeGateway,
        _InMemoryRuntimeProviderAdapter,
    )
    from gwo_v8.runtime_profile import RuntimeProfile

    artifacts = ArtifactStore(tmp_path / "artifacts")
    profile = RuntimeProfile(
        name="coordinator",
        provider="deterministic",
        model="planning",
        thinking="high",
        mode="safe",
        features={},
    )
    gateway = RuntimeGateway(
        store_path=tmp_path / "gateway.json",
        _adapter=_InMemoryRuntimeProviderAdapter(artifacts),
        configuration=RuntimeConfiguration(
            profiles={profile.digest: profile},
            host_mappings={
                "coordinator": ProfileMapping(profile.digest),
            },
        ),
        _artifacts=artifacts,
    )
    repository = InMemoryPlanRepository(writer_generation="writer:one")
    control = PlanControl(
        source=_Source(),
        artifacts=artifacts,
        gateway=gateway,
        repository=repository,
    )

    handle = control.start("owner/repository", ["issue:109"])
    attempt = next(iter(repository.attempts.values()))
    request = artifacts.read_json(attempt.planning_request_artifact_digest)
    record = artifacts.read_json(attempt.compilation_record_artifact_digest)

    assert request["payload"]["schema_version"] == "gwo.plan.planning-request.v1"
    assert request["payload"]["action_id"] == "campaign.plan.v1"
    assert request["payload"]["protocol_id"] == "campaign.planning-output.v1"
    assert set(request["payload"]["input_artifacts"]) == {
        "snapshot_artifact_digest",
        "policy_witness_artifact_digest",
    }
    assert set(record["normalized_intent"]) == {
        "admitted_work",
        "dependency_additions",
        "exclusive_resources",
        "capability_requirements",
        "decision_requirements",
    }
    assert repository.active_receipt(handle) is not None


@pytest.mark.parametrize(
    "ready_ref",
    [
        "#109",
        "https://github.com/owner/repository/issues/109",
    ],
)
def test_ready_reference_is_distinct_from_the_canonical_ticket_key(ready_ref):
    snapshot = _snapshot()
    snapshot["tickets"][0]["source"]["ref"] = ready_ref
    control, _, _, repository = _control(source=_Source(snapshot))

    handle = control.start("owner/repository", [ready_ref])
    active = repository.active_receipt(handle)
    plan = control.read_active(handle).plan_spec_bytes

    from gwo_v8._canonical import load_canonical_json

    assert active.ready_refs == (ready_ref,)
    assert active.ticket_keys == ("issue:109",)
    assert load_canonical_json(plan)["work"][0]["key"] == "issue:109"


def test_snapshot_source_refs_cover_each_requested_ready_ref_exactly_once():
    from gwo_v8.plan_control import PlanControlError

    snapshot = _snapshot()
    second = {
        **snapshot["tickets"][0],
        "key": "issue:110",
        "source": {
            **snapshot["tickets"][0]["source"],
            "digest": "3" * 64,
        },
    }
    snapshot["tickets"].append(second)
    control, _, _, repository = _control(source=_Source(snapshot))

    with pytest.raises(PlanControlError) as invalid:
        control.start("owner/repository", ["#109", "#110"])
    assert invalid.value.code == "SNAPSHOT_OMISSION"
    assert repository.attempts == {}


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
        assert error.code == "ACTIVE_PLAN_CROSS_BINDING_INVALID"
    else:
        raise AssertionError("changed claim proof must fail closed")

    try:
        control.read_active(CampaignHandle("owner/repository", "campaign:pending"))
    except PlanControlError as error:
        assert error.code == "ACTIVATION_PENDING"
    else:
        raise AssertionError("unactivated Campaign must not have an active readback")


def test_active_fast_path_revalidates_exact_preflight_without_progressing_again():
    control, _, gateway, repository = _control()

    handle = control.start("owner/repository", ["issue:109"])
    restarted = control.start("owner/repository", ["issue:109"])

    assert restarted == handle
    assert repository.active_receipt(handle) is not None
    assert len(gateway.preflights) == 2
    assert len(gateway.progresses) == 1


def test_active_readback_rejects_cross_bound_receipt_plan_and_claim_proofs():
    from dataclasses import replace

    from gwo_v8.plan_control import PlanControl, PlanControlError

    control, artifacts, gateway, repository = _control()
    handle = control.start("owner/repository", ["issue:109"])
    active = repository.active_receipt(handle)

    class CrossBoundRepository:
        writer_generation = repository.writer_generation

        def __getattr__(self, name):
            return getattr(repository, name)

        def read_activation(self, requested):
            assert requested == handle
            return replace(active, ticket_keys=("issue:other",))

        def read_claim_proofs(self, requested, revision_digest):
            assert requested == handle
            return (
                type(
                    "ClaimLookalike",
                    (),
                    {
                        "ticket_key": "issue:other",
                        "repository": "other/repository",
                        "campaign_key": handle.campaign_key,
                        "plan_revision_digest": revision_digest,
                    },
                )(),
            )

    hardened = PlanControl(
        source=_Source(),
        artifacts=artifacts,
        gateway=gateway,
        repository=CrossBoundRepository(),
    )
    with pytest.raises(PlanControlError) as invalid:
        hardened.read_active(handle)
    assert invalid.value.code == "ACTIVE_PLAN_CROSS_BINDING_INVALID"


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
    assert len(gateway.preflights) == (2 if boundary == "activate" else 1)
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


def test_restart_recomputes_revision_and_rejects_self_consistent_repository_replacement():
    from dataclasses import replace

    from gwo_v8._canonical import canonical_bytes, digest_bytes
    from gwo_v8.plan_control import (
        InMemoryPlanRepository,
        PlanControlError,
        PlanRevision,
    )

    class CrashThenReplaceRevision:
        def __init__(self):
            self.inner = InMemoryPlanRepository(writer_generation="writer:one")
            self.writer_generation = self.inner.writer_generation
            self.crashed = False
            self.replace_on_read = False

        def __getattr__(self, name):
            return getattr(self.inner, name)

        def publish_revision(self, revision):
            self.inner.publish_revision(revision)
            if not self.crashed:
                self.crashed = True
                self.replace_on_read = True
                raise PlanControlError(
                    "DURABLE_STATE_AMBIGUOUS",
                    "synthetic crash after revision persistence",
                )

        def read_attempt(self, handle, expected_previous_revision_digest):
            attempt = self.inner.read_attempt(
                handle,
                expected_previous_revision_digest,
            )
            if attempt is None or not self.replace_on_read:
                return attempt
            malicious_plan = attempt.revision.plan_spec
            malicious_plan["target_branch"] = "attacker-controlled"
            payload = canonical_bytes(malicious_plan)
            malicious = PlanRevision(
                repository=attempt.revision.repository,
                campaign_key=attempt.revision.campaign_key,
                snapshot_digest=attempt.revision.snapshot_digest,
                canonical_bytes=payload,
                digest=digest_bytes(payload),
            )
            return replace(attempt, revision=malicious)

    artifacts = _Artifacts()
    gateway = _Gateway(artifacts)
    repository = CrashThenReplaceRevision()
    control, _, _, _ = _control(
        artifacts=artifacts,
        gateway=gateway,
        repository=repository,
    )

    with pytest.raises(PlanControlError) as crashed:
        control.start("owner/repository", ["issue:109"])
    assert crashed.value.code == "DURABLE_STATE_AMBIGUOUS"

    with pytest.raises(PlanControlError) as replaced:
        control.start("owner/repository", ["issue:109"])
    assert replaced.value.code == "PLAN_REVISION_PROVENANCE_INVALID"
    assert len(gateway.progresses) == 1
    assert repository.inner.activations == {}


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
        PlanningReservation,
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
        ready_refs=("issue:109",),
        ticket_keys=("issue:109",),
        planning_subject_digest="c" * 64,
        planning_stable_action_id="planning:loser",
        planning_preflight_receipt_digest="d" * 64,
    )
    winning = ActivationReceipt(
        repository=handle.repository,
        campaign_key=handle.campaign_key,
        revision_digest="b" * 64,
        expected_previous_revision_digest=previous,
        writer_generation=repository.writer_generation,
        ready_refs=("issue:109",),
        ticket_keys=("issue:109",),
        planning_subject_digest="e" * 64,
        planning_stable_action_id="planning:winner",
        planning_preflight_receipt_digest="f" * 64,
    )

    for receipt in (losing, winning):
        repository.reserve_planning(
            PlanningReservation(
                repository=receipt.repository,
                campaign_key=receipt.campaign_key,
                ticket_keys=receipt.ticket_keys,
                subject_digest=receipt.planning_subject_digest,
                stable_action_id=receipt.planning_stable_action_id,
                preflight_receipt_digest=(
                    receipt.planning_preflight_receipt_digest
                ),
            )
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
            source = {
                key: value["campaign_source"][key]
                for key in (
                    "repository",
                    "input_ref",
                    "resolved_commit_oid",
                    "tree_oid",
                )
            }
            source["repository"] = name
            import hashlib
            import json

            value["campaign_source"] = {
                **source,
                "digest": hashlib.sha256(
                    json.dumps(
                        source,
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode()
                ).hexdigest(),
            }
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


def test_active_overlap_fails_after_preflight_and_before_loser_planning():
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
    assert len(second_gateway.progresses) == 0
    assert len(repository.revisions) == 1


def test_pending_planning_reservation_blocks_overlap_and_can_be_released():
    from gwo_v8.plan_control import PlanControlError

    artifacts = _Artifacts()
    repository = __import__(
        "gwo_v8.plan_control", fromlist=["InMemoryPlanRepository"]
    ).InMemoryPlanRepository(writer_generation="writer:one")
    first_gateway = _Gateway(artifacts, pending=True)
    first, _, _, _ = _control(
        artifacts=artifacts,
        gateway=first_gateway,
        repository=repository,
    )
    first.start(
        "owner/repository",
        ["issue:109"],
        campaign_key="campaign:first",
    )

    second_gateway = _Gateway(artifacts)
    second, _, _, _ = _control(
        artifacts=artifacts,
        gateway=second_gateway,
        repository=repository,
    )
    with pytest.raises(PlanControlError) as overlap:
        second.start(
            "owner/repository",
            ["issue:109"],
            campaign_key="campaign:second",
        )

    assert overlap.value.code == "TICKET_CLAIM_CONFLICT"
    assert repository.claims == {}
    assert len(first_gateway.progresses) == 1
    assert len(second_gateway.preflights) == 1
    assert len(second_gateway.progresses) == 0
    reservation = next(iter(repository.planning_reservations.values()))
    repository.release_planning(reservation)

    assert (
        second.start(
            "owner/repository",
            ["issue:109"],
            campaign_key="campaign:second",
        ).campaign_key
        == "campaign:second"
    )


def test_decision_only_planning_output_releases_non_executable_reservation():
    from gwo_v8.plan_control import PlanControlDecision

    artifacts = _Artifacts()
    intent = _intent()
    intent["decision_requirements"] = [
        {
            "code": "HUMAN_CHOICE_REQUIRED",
            "detail": "Choose the target contract",
            "ticket_key": "issue:109",
        }
    ]
    control, _, gateway, repository = _control(
        artifacts=artifacts,
        gateway=_Gateway(artifacts, intent=intent),
    )

    with pytest.raises(PlanControlDecision):
        control.start("owner/repository", ["issue:109"])

    assert len(gateway.progresses) == 1
    assert repository.planning_reservations == {}
    assert repository.claims == {}


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


def test_planning_preflight_rejects_a_structural_receipt_lookalike():
    from gwo_v8.plan_control import PlanControlError

    artifacts = _Artifacts()

    class LookalikeGateway(_Gateway):
        def planning_preflight(self, subject):
            return type(
                "PreflightLookalike",
                (),
                {
                    "subject_digest": subject.digest,
                    "stable_action_id": subject.stable_action_id,
                    "receipt_digest": "5" * 64,
                },
            )()

        def progress(self, subject, preflight):
            raise AssertionError("a lookalike preflight must never reach progress")

    control, _, _, repository = _control(
        artifacts=artifacts,
        gateway=LookalikeGateway(artifacts),
    )
    with pytest.raises(PlanControlError) as invalid:
        control.start("owner/repository", ["issue:109"])
    assert invalid.value.code == "RUNTIME_PREFLIGHT_INVALID"
    assert repository.claims == {}


def test_completed_planning_receipt_requires_identical_output_digest_aliases():
    from dataclasses import replace

    from gwo_v8.plan_control import PlanControlError

    artifacts = _Artifacts()

    class MismatchedReceiptGateway(_Gateway):
        def progress(self, subject, preflight):
            receipt = super().progress(subject, preflight)
            return replace(receipt, output_artifact_digest="8" * 64)

    control, _, _, repository = _control(
        artifacts=artifacts,
        gateway=MismatchedReceiptGateway(artifacts),
    )
    with pytest.raises(PlanControlError) as invalid:
        control.start("owner/repository", ["issue:109"])
    assert invalid.value.code == "RUNTIME_PLANNING_RECEIPT_INVALID"
    assert repository.claims == {}


def test_compilation_record_preserves_the_complete_exact_planning_receipt():
    control, artifacts, _, repository = _control()

    control.start("owner/repository", ["issue:109"])
    attempt = next(iter(repository.attempts.values()))
    record = artifacts.read_json(attempt.compilation_record_artifact_digest)
    receipt = record["planning_receipt"]

    assert set(receipt) == {
        "subject_digest",
        "stable_action_id",
        "status",
        "receipt_digest",
        "command",
        "wake_cursor",
        "wake_hints",
        "output_artifact_digest",
        "planning_output_artifact_digest",
    }
    assert receipt["command"] is None
    assert receipt["wake_cursor"] is None
    assert receipt["wake_hints"] == []
    assert (
        receipt["output_artifact_digest"]
        == receipt["planning_output_artifact_digest"]
    )


@pytest.mark.parametrize(
    ("field", "hostile_value"),
    [
        ("admitted_work", [["issue:109"]]),
        (
            "dependency_additions",
            [{"from": ["issue:109"], "to": "issue:109", "reason": "loop"}],
        ),
        ("exclusive_resources", {"issue:109": [["repository.target.v1"]]}),
        ("capability_requirements", {"issue:109": [{"name": "git"}]}),
        (
            "decision_requirements",
            [
                {
                    "code": "CHOICE",
                    "detail": "Choose",
                    "ticket_key": ["issue:109"],
                }
            ],
        ),
    ],
)
def test_hostile_json_intent_values_always_fail_as_plancontrol_errors(
    field,
    hostile_value,
):
    from gwo_v8.plan_control import PlanControlError

    artifacts = _Artifacts()
    intent = _intent()
    intent[field] = hostile_value
    control, _, _, repository = _control(
        artifacts=artifacts,
        gateway=_Gateway(artifacts, intent=intent),
    )

    with pytest.raises(PlanControlError) as invalid:
        control.start("owner/repository", ["issue:109"])
    assert invalid.value.code in {"PLAN_INTENT_INVALID", "PLAN_INTENT_OMISSION"}
    assert repository.claims == {}


@pytest.mark.parametrize(
    ("role", "grant"),
    [
        (
            "campaign",
            {
                "operation_id": "workspace.write.v1",
                "resource_id": "work-run.workspace.v1",
            },
        ),
        (
            "worker",
            {
                "operation_id": "repository.read.v1",
                "resource_id": "campaign.snapshot.v1",
            },
        ),
        (
            "recovery_worker",
            {
                "operation_id": "repository.read.v1",
                "resource_id": "review.subject.v1",
            },
        ),
        (
            "review",
            {
                "operation_id": "workspace.write.v1",
                "resource_id": "work-run.workspace.v1",
            },
        ),
    ],
)
def test_policy_authority_grants_are_role_specific_exact_allowlists(role, grant):
    from gwo_v8._canonical import digest_value
    from gwo_v8.plan_control import PlanControlError

    snapshot = _snapshot()
    policy = snapshot["policy"]
    policy["authority_grants"][role] = [grant]
    policy["digest"] = digest_value(
        {key: value for key, value in policy.items() if key != "digest"}
    )
    control, _, gateway, repository = _control(source=_Source(snapshot))

    with pytest.raises(PlanControlError) as invalid:
        control.start("owner/repository", ["issue:109"])
    assert invalid.value.code == "POLICY_WITNESS_INVALID"
    assert gateway.preflights == []
    assert repository.claims == {}


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


def test_oversized_complete_snapshot_is_decided_before_constituent_artifact_writes(
    tmp_path,
):
    from gwo_v8._canonical import digest_value
    from gwo_v8.plan_control import (
        InMemoryPlanRepository,
        PlanControl,
        SplitCampaignDecision,
    )
    from gwo_v8.runtime_gateway import ArtifactStore

    snapshot = _snapshot()
    policy = snapshot["policy"]
    policy["allowed_capabilities"] = [
        f"capability_{index:03d}" for index in range(100)
    ]
    policy["digest"] = digest_value(
        {key: value for key, value in policy.items() if key != "digest"}
    )
    source = _Source(snapshot)
    artifacts = ArtifactStore(
        tmp_path / "artifacts",
        maximum_bytes=1024,
    )
    repository = InMemoryPlanRepository(writer_generation="writer:one")
    control = PlanControl(
        source=source,
        artifacts=artifacts,
        gateway=_Gateway(artifacts),
        repository=repository,
        max_snapshot_bytes=256,
    )

    with pytest.raises(SplitCampaignDecision) as first:
        control.start("owner/repository", ["issue:109"])
    with pytest.raises(SplitCampaignDecision) as restarted:
        control.start("owner/repository", ["issue:109"])

    assert restarted.value.decision_digest == first.value.decision_digest
    assert source.calls == 1
    assert [path.name for path in (tmp_path / "artifacts").iterdir()] == [
        first.value.decision_digest
    ]
    assert repository.planning_reservations == {}


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


def test_public_start_binds_explicit_empty_runtime_assertion_after_preflight(
    tmp_path,
):
    from gwo_v8.plan_control import InMemoryPlanRepository
    from gwo_v8.plan_control_host import ProductionPlanControlStartHost
    from gwo_v8.runtime_gateway import (
        CampaignStartRuntimeOverrides,
        ProfileMapping,
        RuntimeConfiguration,
    )
    from gwo_v8.runtime_profile import RuntimeProfile

    profile = RuntimeProfile(
        name="host",
        provider="test-provider",
        model="model:host",
        thinking="high",
        mode="safe",
        features={},
    )
    repository = InMemoryPlanRepository(writer_generation="writer:one")
    gateways = []

    def builder(*, artifacts, **_kwargs):
        gateway = _Gateway(artifacts)
        gateways.append(gateway)
        return gateway

    host = ProductionPlanControlStartHost(
        source=_Source(),
        repository=repository,
        runtime_configuration=RuntimeConfiguration(
            profiles={profile.digest: profile},
            host_mappings={"coordinator": ProfileMapping(profile.digest)},
        ),
        repository_contexts={},
        gateway_store_path=tmp_path / "gateway.json",
        artifact_root=tmp_path / "artifacts",
        _gateway_builder=builder,
    )

    handle = host.start("owner/repository", ["issue:109"])

    assert repository.read_runtime_assertion(handle) == (
        CampaignStartRuntimeOverrides().canonical()
    )
    assert len(gateways[0].preflights) == 1


@pytest.mark.parametrize("failure", ["composition", "preflight"])
def test_public_start_never_binds_runtime_assertion_before_exact_preflight(
    tmp_path,
    failure,
):
    from gwo_v8._canonical import digest_value
    from gwo_v8.plan_control import (
        CampaignHandle,
        InMemoryPlanRepository,
        PlanControlError,
    )
    from gwo_v8.plan_control_host import ProductionPlanControlStartHost
    from gwo_v8.runtime_gateway import ProfileMapping, RuntimeConfiguration
    from gwo_v8.runtime_profile import RuntimeProfile

    profile = RuntimeProfile(
        name="host",
        provider="test-provider",
        model="model:host",
        thinking="high",
        mode="safe",
        features={},
    )
    repository = InMemoryPlanRepository(writer_generation="writer:one")
    artifacts = _Artifacts()
    bad_gateway = _Gateway(artifacts, bad_preflight=True)

    def builder(**_kwargs):
        if failure == "composition":
            raise ValueError("synthetic composition failure")
        return bad_gateway

    host = ProductionPlanControlStartHost(
        source=_Source(),
        repository=repository,
        runtime_configuration=RuntimeConfiguration(
            profiles={profile.digest: profile},
            host_mappings={"coordinator": ProfileMapping(profile.digest)},
        ),
        repository_contexts={},
        gateway_store_path=tmp_path / "gateway.json",
        artifact_root=tmp_path / "artifacts",
        _gateway_builder=builder,
    )
    options = {
        "coordinator": {
            "primary_profile_digest": profile.digest,
            "availability_fallback_profile_digest": None,
        }
    }

    with pytest.raises(PlanControlError):
        host.start("owner/repository", ["issue:109"], options)
    handle = CampaignHandle(
        "owner/repository",
        "campaign:"
        + digest_value(
            {
                "repository": "owner/repository",
                "ready_refs": ["issue:109"],
            }
        )[:24],
    )
    assert repository.read_runtime_assertion(handle) is None


def test_concurrent_runtime_assertion_binding_has_one_exact_cas_winner():
    import threading

    from gwo_v8.plan_control import (
        CampaignHandle,
        InMemoryPlanRepository,
        PlanControlError,
    )

    repository = InMemoryPlanRepository(writer_generation="writer:one")
    handle = CampaignHandle("owner/repository", "campaign:one")
    barrier = threading.Barrier(2)
    assertions = [
        {"coordinator": None, "ticket_overrides": []},
        {
            "coordinator": {
                "primary_profile_digest": "a" * 64,
                "availability_fallback_profile_digest": None,
            },
            "ticket_overrides": [],
        },
    ]
    outcomes = []

    def bind(value):
        barrier.wait()
        try:
            outcomes.append(
                ("saved", repository.save_runtime_assertion(handle, value))
            )
        except PlanControlError as error:
            outcomes.append(("error", error.code))

    threads = [
        threading.Thread(target=bind, args=(value,))
        for value in assertions
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sorted(outcome[0] for outcome in outcomes) == ["error", "saved"]
    assert next(
        outcome[1] for outcome in outcomes if outcome[0] == "error"
    ) == "START_OPTIONS_CONFLICT"
    assert repository.read_runtime_assertion(handle) in assertions


class _GitHubPlanStateClient:
    def __init__(self, *, lose_ack_once=False):
        self.contents = {}
        self.writes = 0
        self.lose_ack_once = lose_ack_once

    def read(self, repository, branch, path):
        return self.contents.get((repository, branch, path))

    def compare_and_swap(
        self,
        repository,
        branch,
        path,
        content,
        *,
        expected_blob_sha,
        message,
    ):
        del message
        from gwo_v8.activation import GitHubContent

        key = (repository, branch, path)
        current = self.contents.get(key)
        current_sha = None if current is None else current.blob_sha
        if current_sha != expected_blob_sha:
            raise RuntimeError("synthetic GitHub CAS conflict")
        self.writes += 1
        written = GitHubContent(
            content=content,
            blob_sha=f"blob:{self.writes}",
        )
        self.contents[key] = written
        if self.lose_ack_once:
            self.lose_ack_once = False
            raise RuntimeError("synthetic acknowledgement loss")
        return written


class _WriterGeneration:
    def __init__(self, writer_generation="writer:one"):
        self.writer_generation = writer_generation

    def read_current(self, repository):
        from types import SimpleNamespace

        return SimpleNamespace(
            repository=repository,
            writer_generation=self.writer_generation,
            record_id="writer-record:one",
        )


def test_github_plan_repository_survives_restart_and_lost_cas_acknowledgement(
    tmp_path,
):
    from gwo_v8.plan_control import PlanControl
    from gwo_v8.plan_control_github import GitHubPlanRepository
    from gwo_v8.runtime_gateway import ArtifactStore

    client = _GitHubPlanStateClient(lose_ack_once=True)
    writer = _WriterGeneration()
    artifacts = ArtifactStore(tmp_path / "artifacts")
    first_repository = GitHubPlanRepository(
        client,
        repository="owner/repository",
        branch="gwo-control",
        writer_generation="writer:one",
        writer_control=writer,
    )
    first_gateway = _Gateway(artifacts)
    first = PlanControl(
        source=_Source(),
        artifacts=artifacts,
        gateway=first_gateway,
        repository=first_repository,
    )

    handle = first.start("owner/repository", ["issue:109"])

    restarted_repository = GitHubPlanRepository(
        client,
        repository="owner/repository",
        branch="gwo-control",
        writer_generation="writer:one",
        writer_control=writer,
    )
    restarted = PlanControl(
        source=_Source(),
        artifacts=artifacts,
        gateway=_Gateway(artifacts),
        repository=restarted_repository,
    )
    active = restarted.read_active(handle)

    assert active.activation_receipt.ticket_keys == ("issue:109",)
    assert [proof.ticket_key for proof in active.claim_proofs] == ["issue:109"]
    state = next(
        content
        for (repository, branch, path), content in client.contents.items()
        if (
            repository,
            branch,
            path,
        )
        == (
            "owner/repository",
            "gwo-control",
            ".gwo-v8/plan-control-v3.json",
        )
    )
    from gwo_v8._canonical import load_canonical_json

    assert set(load_canonical_json(state.content)) >= {
        "attempts",
        "runtime_assertions",
        "planning_reservations",
        "pending_reservations",
        "claims",
        "revisions",
        "activations",
    }


def test_two_github_repository_instances_share_global_planning_reservations(
    tmp_path,
):
    from gwo_v8.plan_control import PlanControl, PlanControlError
    from gwo_v8.plan_control_github import GitHubPlanRepository
    from gwo_v8.runtime_gateway import ArtifactStore

    client = _GitHubPlanStateClient()
    writer = _WriterGeneration()
    artifacts = ArtifactStore(tmp_path / "artifacts")

    def repository():
        return GitHubPlanRepository(
            client,
            repository="owner/repository",
            branch="gwo-control",
            writer_generation="writer:one",
            writer_control=writer,
        )

    first_gateway = _Gateway(artifacts, pending=True)
    PlanControl(
        source=_Source(),
        artifacts=artifacts,
        gateway=first_gateway,
        repository=repository(),
    ).start(
        "owner/repository",
        ["issue:109"],
        campaign_key="campaign:first",
    )
    second_gateway = _Gateway(artifacts)
    second = PlanControl(
        source=_Source(),
        artifacts=artifacts,
        gateway=second_gateway,
        repository=repository(),
    )

    with pytest.raises(PlanControlError) as conflict:
        second.start(
            "owner/repository",
            ["issue:109"],
            campaign_key="campaign:second",
        )
    assert conflict.value.code == "TICKET_CLAIM_CONFLICT"
    assert len(second_gateway.preflights) == 1
    assert second_gateway.progresses == []


def test_production_github_installer_builds_real_source_and_durable_repository(
    tmp_path,
):
    from gwo_v8._canonical import canonical_bytes
    from gwo_v8.activation import GitHubContent
    from gwo_v8.plan_control_host import install_github_plan_control_start
    from gwo_v8.runtime_gateway import ProfileMapping, RuntimeConfiguration
    from gwo_v8.runtime_profile import RuntimeProfile

    class IssueClient:
        def __init__(self):
            self.issue_reads = []

        def read_issue(self, repository, number):
            self.issue_reads.append((repository, number))
            return {
                "id": number,
                "node_id": f"ISSUE_{number}",
                "number": number,
                "title": "Contract",
                "body": "Do the work",
                "state": "open",
                "state_reason": None,
                "type": None,
                "updated_at": "2026-07-30T00:00:00Z",
                "repository_url": (
                    "https://api.github.com/repos/owner/repository"
                ),
                "url": (
                    "https://api.github.com/repos/owner/repository/"
                    f"issues/{number}"
                ),
                "html_url": (
                    "https://github.com/owner/repository/"
                    f"issues/{number}"
                ),
                "labels": [
                    {
                        "id": 1,
                        "node_id": "LABEL_1",
                        "url": "https://api.github.com/repos/owner/repository/labels/ready-for-agent",
                        "name": "ready-for-agent",
                        "color": "0052cc",
                        "default": False,
                        "description": "ready",
                    }
                ],
            }

        def read_comments(self, repository, number):
            return ()

        def read_blockers(self, repository, number):
            return ()

        def read_branch_source(self, repository, branch):
            return {
                "input_ref": "refs/heads/main",
                "resolved_commit_oid": "a" * 40,
                "tree_oid": "b" * 40,
            }

    client = _GitHubPlanStateClient()
    client.contents[
        (
            "owner/repository",
            "gwo-control",
            ".gwo-v8/policy-witness.json",
        )
    ] = GitHubContent(
        content=canonical_bytes(_snapshot()["policy"]),
        blob_sha="blob:policy",
    )
    issue_client = IssueClient()
    profile = RuntimeProfile(
        name="host",
        provider="test-provider",
        model="model:host",
        thinking="high",
        mode="safe",
        features={},
    )
    gateways = []

    def gateway_builder(*, artifacts, **_kwargs):
        gateway = _Gateway(artifacts)
        gateways.append(gateway)
        return gateway

    host = install_github_plan_control_start(
        repository="owner/repository",
        control_branch="gwo-control",
        target_branch="main",
        writer_generation="writer:one",
        runtime_configuration=RuntimeConfiguration(
            profiles={profile.digest: profile},
            host_mappings={"coordinator": ProfileMapping(profile.digest)},
        ),
        repository_contexts={},
        gateway_store_path=tmp_path / "gateway.json",
        artifact_root=tmp_path / "artifacts",
        _content_client=client,
        _issue_client=issue_client,
        _writer_control=_WriterGeneration(),
        _gateway_builder=gateway_builder,
    )

    options = {
        "ticket_overrides": [
            {
                "ticket_key": "issue:109",
                "role": "worker",
                "mapping": {
                    "primary_profile_digest": profile.digest,
                    "availability_fallback_profile_digest": None,
                },
            }
        ]
    }
    handle = host.start("owner/repository", ["#109"], options)
    equivalent_handle = host.start(
        "owner/repository",
        ["https://github.com/owner/repository/issues/109"],
        options,
    )

    assert issue_client.issue_reads == [
        ("owner/repository", 109),
        ("owner/repository", 109),
    ]
    assert equivalent_handle == handle
    assert handle.repository == "owner/repository"
    assert any(
        path == ".gwo-v8/plan-control-v3.json"
        for (_repository, _branch, path) in client.contents
    )
    assert len(gateways[0].preflights) == 1


def test_github_plan_repository_enforces_writer_generation_fence():
    from gwo_v8.plan_control import CampaignHandle, PlanControlError
    from gwo_v8.plan_control_github import GitHubPlanRepository

    repository = GitHubPlanRepository(
        _GitHubPlanStateClient(),
        repository="owner/repository",
        branch="gwo-control",
        writer_generation="writer:one",
        writer_control=_WriterGeneration("writer:other"),
    )

    with pytest.raises(PlanControlError) as fenced:
        repository.active_receipt(
            CampaignHandle("owner/repository", "campaign:one")
        )
    assert fenced.value.code == "WRITER_FENCE_CONFLICT"


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
