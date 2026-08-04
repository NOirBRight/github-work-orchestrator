from __future__ import annotations

from dataclasses import replace
from typing import Literal

from gwo_v8._canonical import digest_value
from gwo_v8._batch_integrator_store import (
    HostedResultReceipt,
    SqliteBatchDeliveryJournal,
)
from gwo_v8.batch_integrator import (
    AncestorReadback,
    BatchDeliveryAction,
    BatchDeliveryRequest,
    BatchIntegrator,
    BatchIntegratorConfiguration,
    BatchIntegratorError,
    BatchTarget,
    DeliveryAttributionAmbiguous,
    HostedSuiteDefinition,
    LocalSuiteDefinition,
    TargetDeltaReadback,
)
from gwo_v8.batch_patch_identity import PatchIdentityEntry
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


def make_batch_target(
    *,
    repository: str = "owner/repo",
    target_branch: str = "main",
    target_head_sha: str = "b" * 40,
    target_tree_oid: str = "8" * 40,
    target_facts_digest: str = "9" * 64,
) -> BatchTarget:
    return BatchTarget(
        repository=repository,
        target_branch=target_branch,
        target_head_sha=target_head_sha,
        target_tree_oid=target_tree_oid,
        target_facts_digest=target_facts_digest,
    )


def make_three_standard_receipts() -> tuple[AcceptedCandidateReceipt, ...]:
    return tuple(
        make_accepted_candidate_receipt(
            ticket_key=f"issue:{index}",
            accepted_sequence=index,
        )
        for index in range(1, 4)
    )


def make_patch_entry(
    path: str,
    *,
    old_path: str | None = None,
    new_path: str | None = None,
    change_kind: Literal["add", "delete", "modify", "type-change"] = "modify",
    old_mode: str = "100644",
    new_mode: str = "100644",
    old_oid: str = "a" * 40,
    new_oid: str = "a" * 40,
    old_object_type: Literal["blob", "gitlink"] = "blob",
    new_object_type: Literal["blob", "gitlink"] = "blob",
) -> PatchIdentityEntry:
    return PatchIdentityEntry(
        old_path=old_path if old_path is not None else path,
        new_path=new_path if new_path is not None else path,
        change_kind=change_kind,
        old_mode=old_mode,
        new_mode=new_mode,
        old_object_type=old_object_type,
        new_object_type=new_object_type,
        old_oid=old_oid,
        new_oid=new_oid,
    )


def make_ancestor_readback(
    ancestor_sha: str,
    descendant_sha: str,
    *,
    is_ancestor: bool = True,
) -> AncestorReadback:
    body = {
        "ancestor_sha": ancestor_sha,
        "descendant_sha": descendant_sha,
        "is_ancestor": is_ancestor,
    }
    return AncestorReadback(
        **body,
        readback_digest=digest_value({"kind": "ancestor-readback.v1", **body}),
    )


def make_target_delta(
    base_sha: str,
    target_head_sha: str,
    *,
    interaction_keys: tuple[InteractionKey, ...] = (),
) -> TargetDeltaReadback:
    protected = tuple(key for key in interaction_keys if key.requires_singleton)
    body = {
        "base_sha": base_sha,
        "target_head_sha": target_head_sha,
        "interaction_keys": [key.canonical() for key in interaction_keys],
        "protected_interaction_keys": [key.canonical() for key in protected],
    }
    return TargetDeltaReadback(
        base_sha=base_sha,
        target_head_sha=target_head_sha,
        interaction_keys=interaction_keys,
        protected_interaction_keys=protected,
        facts_digest=digest_value(body),
        readback_digest=digest_value({"kind": "target-delta-readback.v1", **body}),
    )


def make_batch_action(
    *,
    stable_action_id: str = "delivery-action:1",
    request_digest: str = "a" * 64,
    batch_id: str = "b" * 64,
    batch_sha: str = "c" * 40,
    member_ticket_keys: tuple[str, ...] = ("issue:1",),
) -> BatchDeliveryAction:
    return BatchDeliveryAction(
        stable_action_id=stable_action_id,
        request_digest=request_digest,
        batch_id=batch_id,
        batch_sha=batch_sha,
        member_ticket_keys=member_ticket_keys,
    )


def make_hosted_result_receipt(
    *,
    stable_action_id: str = "delivery-action:1",
    batch_sha: str = "c" * 40,
    suite_id: str = "hosted",
    provider_check_id: str = "check:1",
    outcome: Literal["passed", "code_failure", "infrastructure_failure"] = "passed",
    observation_digest: str = "e" * 64,
) -> HostedResultReceipt:
    body = {
        "stable_action_id": stable_action_id,
        "batch_sha": batch_sha,
        "suite_id": suite_id,
        "provider_check_id": provider_check_id,
        "outcome": outcome,
        "observation_digest": observation_digest,
        "source_ref": "checks:hosted",
    }
    return HostedResultReceipt(
        **body,
        receipt_digest=digest_value(
            {"kind": "hosted_result_receipt.v1", **body}
        ),
    )
