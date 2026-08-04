from __future__ import annotations

from dataclasses import replace
from typing import Literal

from gwo_v8._canonical import digest_value
from gwo_v8.batch_integrator import (
    BatchDeliveryRequest,
    BatchIntegrator,
    BatchIntegratorConfiguration,
    BatchIntegratorError,
    BatchTarget,
    DeliveryAttributionAmbiguous,
    HostedSuiteDefinition,
    LocalSuiteDefinition,
)
from gwo_v8.candidate_gate import (
    AcceptedCandidateReceipt,
    CandidateGateError,
    InteractionClassification,
    InteractionKey,
)


def make_interaction_key(
    value: str = "api:ordinary",
    *,
    classification: InteractionClassification = InteractionClassification.ORDINARY,
) -> InteractionKey:
    return InteractionKey(namespace="test", value=value, classification=classification)


def make_accepted_candidate_receipt(
    *,
    repository: str = "owner/repo",
    campaign_key: str = "campaign:test",
    target_branch: str = "main",
    ticket_key: str = "issue:1",
    candidate_sha: str = "c" * 40,
    accepted_sequence: int = 1,
    base_sha: str = "b" * 40,
    base_tree_oid: str = "1" * 40,
    candidate_tree_oid: str | None = None,
    delivery_identity_digest: str = "d" * 64,
    evidence_digests: tuple[str, ...] = ("e" * 64,),
    assurance: Literal["standard", "strict"] = "standard",
    interaction_keys: tuple[InteractionKey, ...] | None = None,
    protected_surfaces: tuple[str, ...] = (),
    gitlink_change: bool = False,
) -> AcceptedCandidateReceipt:
    index = accepted_sequence
    actual_candidate_sha = (
        candidate_sha if candidate_sha != "c" * 40 else f"{index + 10:040x}"
    )
    actual_candidate_tree_oid = candidate_tree_oid or f"{index + 100:040x}"
    actual_interaction_keys = (
        interaction_keys
        if interaction_keys is not None
        else (make_interaction_key(f"api:{ticket_key}"),)
    )
    try:
        return AcceptedCandidateReceipt(
            repository=repository,
            campaign_key=campaign_key,
            plan_revision_digest="1" * 64,
            target_branch=target_branch,
            ticket_key=ticket_key,
            work_run_key=f"work-run:{index}",
            integration_node_key=f"integration:{index}",
            accepted_sequence=accepted_sequence,
            base_sha=base_sha,
            base_tree_oid=base_tree_oid,
            candidate_sha=actual_candidate_sha,
            candidate_tree_oid=actual_candidate_tree_oid,
            candidate_receipt_digest=digest_value(
                {
                    "kind": "candidate_receipt",
                    "ticket_key": ticket_key,
                    "candidate_sha": actual_candidate_sha,
                }
            ),
            diff_schema_version="CandidateDiffRecordV1",
            diff_record_digest="2" * 64,
            authority_subtree_digest="3" * 64,
            policy_witness_digest="4" * 64,
            review_subject_digest="5" * 64,
            assurance=assurance,
            assurance_requirement_digest=digest_value(
                {"assurance": assurance, "ticket_key": ticket_key}
            ),
            check_environment_digest="6" * 64,
            delivery_identity_digest=delivery_identity_digest,
            interaction_keys=actual_interaction_keys,
            protected_surfaces=tuple(sorted(protected_surfaces)),
            gitlink_change=gitlink_change,
            evidence_digests=evidence_digests,
            review_finding_ledger_digest="7" * 64,
        )
    except CandidateGateError as error:
        raise BatchIntegratorError(
            "BATCH_CANDIDATE_INVALID",
            str(error),
        ) from error


def make_batch_request(
    *,
    accepted_candidates: tuple[AcceptedCandidateReceipt, ...],
    stable_action_id: str = "delivery-action:1",
    target_head_sha: str = "7" * 40,
) -> BatchDeliveryRequest:
    return BatchDeliveryRequest(
        stable_action_id=stable_action_id,
        repository="owner/repo",
        campaign_key="campaign:test",
        plan_revision_digest="1" * 64,
        target=BatchTarget(
            repository="owner/repo",
            target_branch="main",
            target_head_sha=target_head_sha,
            target_tree_oid="8" * 40,
            target_facts_digest="9" * 64,
        ),
        accepted_candidates=accepted_candidates,
        local_suite=LocalSuiteDefinition(
            suite_id="local",
            definition_digest="a" * 64,
            command=("py", "-3.13", "-c", "print('batch-local-suite')"),
        ),
        hosted_suites=(
            HostedSuiteDefinition(
                suite_id="hosted",
                hosted_name="GWO Canary CI",
                definition_digest="b" * 64,
            ),
        ),
        writer_generation="writer:test",
        activation_id="activation:test",
    )
