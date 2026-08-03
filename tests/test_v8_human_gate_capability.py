from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import sys

import pytest


SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "orchestrator" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


from gwo_v8._canonical import digest_value  # noqa: E402
from gwo_v8.planning_protocol import planning_prompt  # noqa: E402
from gwo_v8.runtime_gateway import (  # noqa: E402
    CapabilityPolicy,
    CapabilityPolicyProof,
    CampaignPlanningSubject,
    CoordinatorCapabilityProof,
    HumanGateCapabilityProof,
    RuntimeConfiguration,
    RuntimeGateway,
    RuntimeGatewayError,
    ProfileMapping,
    _InMemoryRuntimeProviderAdapter,
    ArtifactStore,
)
from gwo_v8.runtime_profile import RuntimeProfile  # noqa: E402


POLICY_FLAGS = (
    "worker_can_edit_issues",
    "worker_can_edit_blockers",
    "worker_can_edit_campaign_membership",
    "worker_can_activate_plan_revision",
    "worker_can_merge",
    "worker_can_expand_authority",
    "worker_can_invoke_global_planning",
)
COORDINATOR_FLAGS = (
    "can_edit_tracker",
    "can_edit_labels",
    "can_edit_campaign_membership",
    "can_expand_authority",
    "can_grant_authority",
    "can_activate_plan_revision",
    "can_merge",
    "delegation_enabled",
    "can_invoke_global_planning",
)


def _profile() -> RuntimeProfile:
    return RuntimeProfile(
        name="human-gate-capability",
        provider="test-provider",
        model="model:human-gate-capability",
        thinking="high",
        mode="safe",
        features={},
    )


def _subject(store: ArtifactStore) -> CampaignPlanningSubject:
    snapshot = store.put_canonical({"tickets": [{"key": "issue:136"}]})
    policy = store.put_canonical({"policy": "frozen"})
    provisional = CampaignPlanningSubject(
        repository="owner/repository",
        campaign_key="campaign:136",
        campaign_handle="handle:136",
        expected_previous_plan_revision_digest=None,
        snapshot_artifact_digest=snapshot.digest,
        policy_witness_digest=policy.digest,
        planning_request_artifact_digest="0" * 64,
        stable_action_id="planning:human-gate-capability",
    )
    prompt = store.put_canonical(
        planning_prompt(
            subject_digest=provisional.prompt_binding_digest,
            authority_digest=provisional.authority_digest,
            snapshot_artifact_digest=snapshot.digest,
            policy_witness_artifact_digest=policy.digest,
        )
    )
    return replace(provisional, planning_request_artifact_digest=prompt.digest)


def _policy(**overrides: bool) -> CapabilityPolicy:
    return CapabilityPolicy(
        **{flag: overrides.get(flag, False) for flag in POLICY_FLAGS}
    )


def _policy_proof(role: str, subject: CampaignPlanningSubject, policy: CapabilityPolicy):
    return CapabilityPolicyProof(
        capability_policy=policy,
        authority_record_digest=digest_value(
            {
                "role": role,
                "subject_digest": subject.digest,
                "policy_witness_digest": subject.policy_witness_digest,
                "capability_policy": policy.canonical(),
            }
        ),
    )


def _coordinator_proof(
    subject: CampaignPlanningSubject, **overrides: bool
) -> CoordinatorCapabilityProof:
    return CoordinatorCapabilityProof(
        subject_digest=subject.digest,
        repository_read_only=True,
        tracker_read_only=True,
        can_activate_plan_revision=overrides.get("can_activate_plan_revision", False),
        can_edit_tracker=overrides.get("can_edit_tracker", False),
        can_expand_authority=overrides.get("can_expand_authority", False),
        delegation_enabled=overrides.get("delegation_enabled", False),
        can_edit_labels=overrides.get("can_edit_labels", False),
        can_edit_campaign_membership=overrides.get(
            "can_edit_campaign_membership", False
        ),
        can_grant_authority=overrides.get("can_grant_authority", False),
        can_merge=overrides.get("can_merge", False),
        can_invoke_global_planning=overrides.get(
            "can_invoke_global_planning", False
        ),
    )


def _tampered_coordinator_proof(
    subject: CampaignPlanningSubject, flag: str
) -> CoordinatorCapabilityProof:
    proof = _coordinator_proof(subject)
    object.__setattr__(proof, flag, True)
    return proof


def _proof(
    subject: CampaignPlanningSubject,
    *,
    worker_policy: CapabilityPolicy | None = None,
    reviewer_policy: CapabilityPolicy | None = None,
    coordinator: CoordinatorCapabilityProof | None = None,
) -> HumanGateCapabilityProof:
    return HumanGateCapabilityProof(
        subject_digest=subject.digest,
        policy_witness_digest=subject.policy_witness_digest,
        gateway_configuration_digest="0" * 64,
        worker_capability_policy_proof=_policy_proof(
            "worker", subject, worker_policy or _policy()
        ),
        reviewer_capability_policy_proof=_policy_proof(
            "reviewer", subject, reviewer_policy or _policy()
        ),
        coordinator_capability_proof=coordinator or _coordinator_proof(subject),
    )


class _CapabilityReadback:
    """Read-only capability source; its test mutators are never Gateway calls."""

    def __init__(self, proof: HumanGateCapabilityProof):
        self.proof = proof
        self.reads = 0
        self.mutations = {
            "tracker": 0,
            "plan": 0,
            "merge": 0,
            "authority": 0,
        }

    def read_human_gate_capability(self, subject):
        self.reads += 1
        return self.proof

    def mutation_counts(self):
        return dict(self.mutations)


class _CapabilityHarness:
    def __init__(self, tmp_path: Path):
        self.artifacts = ArtifactStore(tmp_path / "artifacts", maximum_bytes=1_048_576)
        self.subject = _subject(self.artifacts)
        profile = _profile()
        configuration = RuntimeConfiguration(
            profiles={profile.digest: profile},
            host_mappings={"coordinator": ProfileMapping(profile.digest)},
        )
        self.configuration_digest = digest_value(
            {
                "profiles": [profile.canonical()],
                "host_mappings": [
                    {
                        "selector": "coordinator",
                        "mapping": {
                            "primary_profile_digest": profile.digest,
                            "availability_fallback_profile_digest": None,
                        },
                    }
                ],
                "repository_mappings": [],
                "campaign_assertions": [],
            }
        )
        proof = _proof(self.subject)
        self.proof = replace(
            proof, gateway_configuration_digest=self.configuration_digest
        )
        self.source = _CapabilityReadback(self.proof)
        self.gateway = RuntimeGateway(
            store_path=tmp_path / "gateway.journal",
            _adapter=_InMemoryRuntimeProviderAdapter(self.artifacts),
            configuration=configuration,
            _artifacts=self.artifacts,
            _authority_readback=self.source,
        )
        self.proof = replace(
            self.proof,
            gateway_configuration_digest=self.gateway._configuration_identity,
            proof_digest=None,
        )
        self.source.proof = self.proof
        self.mutation_counts = self.source.mutation_counts

    def set_forbidden_capability(self, role: str, flag: str):
        if role in {"worker", "reviewer"}:
            policy = _policy(**{flag: True})
            kwargs = {
                "worker_policy": policy
                if role == "worker"
                else self.proof.worker_capability_policy_proof.capability_policy,
                "reviewer_policy": policy
                if role == "reviewer"
                else self.proof.reviewer_capability_policy_proof.capability_policy,
                "coordinator": self.proof.coordinator_capability_proof,
            }
        else:
            kwargs = {
                "worker_policy": self.proof.worker_capability_policy_proof.capability_policy,
                "reviewer_policy": self.proof.reviewer_capability_policy_proof.capability_policy,
                "coordinator": _tampered_coordinator_proof(self.subject, flag),
            }
        self.proof = _proof(self.subject, **kwargs)
        self.proof = replace(
            self.proof,
            gateway_configuration_digest=self.gateway._configuration_identity,
            proof_digest=None,
        )
        self.source.proof = self.proof


@pytest.fixture
def capability_harness(tmp_path):
    return _CapabilityHarness(tmp_path)


@pytest.mark.parametrize(
    ("role", "flag"),
    [
        *[("worker", flag) for flag in POLICY_FLAGS],
        *[("reviewer", flag) for flag in POLICY_FLAGS],
        *[("coordinator", flag) for flag in COORDINATOR_FLAGS],
    ],
)
def test_any_forbidden_worker_reviewer_or_coordinator_capability_fails_closed(
    capability_harness, role, flag
):
    capability_harness.set_forbidden_capability(role, flag)

    with pytest.raises(RuntimeGatewayError) as error:
        capability_harness.gateway.read_human_gate_capability(
            capability_harness.subject
        )

    assert error.value.code == "PLAN_INVALIDATION_CAPABILITY_PROOF_FAIL_CLOSED"
    assert all(value == 0 for value in capability_harness.mutation_counts().values())


def test_valid_capability_proof_binds_subject_policy_and_gateway_configuration(
    capability_harness,
):
    proof = capability_harness.gateway.read_human_gate_capability(
        capability_harness.subject
    )

    assert proof.subject_digest == capability_harness.subject.digest
    assert proof.policy_witness_digest == capability_harness.subject.policy_witness_digest
    assert (
        proof.gateway_configuration_digest
        == capability_harness.gateway._configuration_identity
    )
    assert proof.worker_capability_policy_proof.capability_policy.is_proven
    assert proof.reviewer_capability_policy_proof.capability_policy.is_proven
    assert proof.coordinator_capability_proof.is_proven
    assert proof.proof_digest == proof.digest


def test_tampered_human_gate_capability_proof_digest_fails_closed(
    capability_harness,
):
    proof = capability_harness.gateway.read_human_gate_capability(
        capability_harness.subject
    )
    tampered = replace(proof, proof_digest="0" * 64)

    with pytest.raises(RuntimeGatewayError) as error:
        capability_harness.gateway.validate_human_gate_capability(tampered)

    assert error.value.code == "PLAN_INVALIDATION_CAPABILITY_PROOF_FAIL_CLOSED"
    assert all(value == 0 for value in capability_harness.mutation_counts().values())


def test_capability_readback_is_zero_mutation_and_no_writer_conformant(
    capability_harness,
):
    gateway = capability_harness.gateway
    proof = gateway.read_human_gate_capability(capability_harness.subject)

    assert proof.proof_digest == proof.digest
    assert capability_harness.source.reads == 1
    assert all(value == 0 for value in capability_harness.mutation_counts().values())
    assert not any(
        name.startswith(("write", "create", "edit", "merge", "activate", "grant"))
        for name in dir(capability_harness.source)
    )
    assert not any(
        name in {"write_human_gate_capability", "mutate_human_gate_capability"}
        for name in dir(gateway)
    )
