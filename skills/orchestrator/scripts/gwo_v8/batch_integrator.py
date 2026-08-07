"""V3 BatchIntegrator identity boundary and delivery action seam."""

from __future__ import annotations

import re
import json
from dataclasses import asdict, dataclass, replace
from enum import StrEnum
from types import MappingProxyType
from typing import TYPE_CHECKING, Literal

from ._canonical import digest_value
from .candidate_gate import (
    AcceptedCandidateReceipt,
    InteractionClassification,
    InteractionKey,
)

if TYPE_CHECKING:
    from ._batch_integrator_store import BatchDeliveryJournal


class BatchIntegratorError(RuntimeError):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


class DeliveryIdentityMismatch(BatchIntegratorError):
    def __init__(self, detail: str) -> None:
        super().__init__("DELIVERY_IDENTITY_MISMATCH", detail)


class DeliveryAttributionAmbiguous(BatchIntegratorError):
    def __init__(self, detail: str) -> None:
        super().__init__("DELIVERY_ATTRIBUTION_AMBIGUOUS", detail)


def _require_object_id(name: str, value: str) -> str:
    if re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", value) is None:
        raise BatchIntegratorError(
            "BATCH_OBJECT_ID_INVALID",
            f"{name} must be a lowercase Git object ID",
        )
    return value


def _require_digest(name: str, value: str) -> str:
    if re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise BatchIntegratorError(
            "BATCH_DIGEST_INVALID",
            f"{name} must be a lowercase SHA-256 digest",
        )
    return value


def _require_sorted_unique(name: str, values: tuple[str, ...]) -> tuple[str, ...]:
    frozen = tuple(values)
    if frozen != tuple(sorted(set(frozen))):
        raise BatchIntegratorError(
            "BATCH_CANONICAL_ORDER_INVALID",
            f"{name} must be sorted and unique",
        )
    return frozen


def _accepted_candidate_body(candidate: AcceptedCandidateReceipt) -> dict[str, object]:
    body = dict(candidate.canonical())
    if "result_digest" in body:
        raise BatchIntegratorError(
            "BATCH_RESULT_PREMATURE",
            "accepted Candidate cannot contain result_digest",
        )
    if candidate.accepted_sequence < 0:
        raise BatchIntegratorError(
            "BATCH_SEQUENCE_INVALID",
            "accepted_sequence must be non-negative",
        )
    for name in ("base_sha", "base_tree_oid", "candidate_sha", "candidate_tree_oid"):
        _require_object_id(name, getattr(candidate, name))
    for name in (
        "candidate_receipt_digest",
        "diff_record_digest",
        "authority_subtree_digest",
        "policy_witness_digest",
        "review_subject_digest",
        "assurance_requirement_digest",
        "check_environment_digest",
        "delivery_identity_digest",
        "review_finding_ledger_digest",
    ):
        _require_digest(name, getattr(candidate, name))
    evidence = _require_sorted_unique(
        "evidence_digests", tuple(candidate.evidence_digests)
    )
    for digest in evidence:
        _require_digest("evidence_digests item", digest)
    _require_sorted_unique("protected_surfaces", tuple(candidate.protected_surfaces))
    interaction_order = tuple(
        (key.namespace, key.value, key.classification.value)
        for key in candidate.interaction_keys
    )
    if interaction_order != tuple(sorted(set(interaction_order))):
        raise BatchIntegratorError(
            "BATCH_INTERACTION_ORDER_INVALID",
            "interaction_keys must be sorted and unique",
        )
    _require_digest("accepted_candidate_digest", candidate.digest)
    return body


@dataclass(frozen=True)
class BatchTarget:
    repository: str
    target_branch: str
    target_head_sha: str
    target_tree_oid: str
    target_facts_digest: str

    def __post_init__(self) -> None:
        _require_object_id("target_head_sha", self.target_head_sha)
        _require_object_id("target_tree_oid", self.target_tree_oid)
        _require_digest("target_facts_digest", self.target_facts_digest)

    def canonical(self) -> dict[str, str]:
        return {
            "repository": self.repository,
            "target_branch": self.target_branch,
            "target_head_sha": self.target_head_sha,
            "target_tree_oid": self.target_tree_oid,
            "target_facts_digest": self.target_facts_digest,
        }


@dataclass(frozen=True)
class LocalSuiteDefinition:
    suite_id: str
    definition_digest: str
    command: tuple[str, ...]

    def __post_init__(self) -> None:
        command = tuple(self.command)
        if not self.suite_id or not command or any(not item for item in command):
            raise BatchIntegratorError(
                "BATCH_LOCAL_SUITE_INVALID",
                "local suite ID and command are required",
            )
        _require_digest("local definition_digest", self.definition_digest)
        object.__setattr__(self, "command", command)

    def canonical(self) -> dict[str, object]:
        return {
            "suite_id": self.suite_id,
            "definition_digest": self.definition_digest,
            "command": list(self.command),
        }


@dataclass(frozen=True)
class HostedSuiteDefinition:
    suite_id: str
    hosted_name: str
    definition_digest: str

    def __post_init__(self) -> None:
        if not self.suite_id or not self.hosted_name:
            raise BatchIntegratorError(
                "BATCH_HOSTED_SUITE_INVALID",
                "hosted suite identity is required",
            )
        _require_digest("hosted definition_digest", self.definition_digest)

    def canonical(self) -> dict[str, str]:
        return {
            "suite_id": self.suite_id,
            "hosted_name": self.hosted_name,
            "definition_digest": self.definition_digest,
        }


@dataclass(frozen=True)
class BatchIntegratorConfiguration:
    host_member_limit: int = 4
    repository_member_limits: dict[str, int] | None = None
    infrastructure_retry_limit: int = 2

    def __post_init__(self) -> None:
        if not 1 <= self.host_member_limit <= 4:
            raise BatchIntegratorError(
                "BATCH_MEMBER_LIMIT_INVALID",
                "member limit must be between one and four",
            )
        if self.infrastructure_retry_limit != 2:
            raise BatchIntegratorError(
                "BATCH_RETRY_POLICY_INVALID",
                "infrastructure retry limit is fixed at two",
            )
        copied = dict(sorted((self.repository_member_limits or {}).items()))
        if any(not 1 <= limit <= 4 for limit in copied.values()):
            raise BatchIntegratorError(
                "BATCH_MEMBER_LIMIT_INVALID",
                "repository member limit must be between one and four",
            )
        object.__setattr__(self, "repository_member_limits", MappingProxyType(copied))

    def member_limit_for(self, repository: str) -> int:
        return self.repository_member_limits.get(repository, self.host_member_limit)


class CompatibilityDecision(StrEnum):
    COMPATIBLE = "compatible"
    SINGLETON_REQUIRED = "singleton_required"
    INCOMPATIBLE = "incompatible"
    CLEAN_BASE_ADVANCE = "clean_base_advance"


@dataclass(frozen=True)
class BatchDeliveryRequest:
    stable_action_id: str
    repository: str
    campaign_key: str
    plan_revision_digest: str
    target: BatchTarget
    accepted_candidates: tuple[AcceptedCandidateReceipt, ...]
    local_suite: LocalSuiteDefinition
    hosted_suites: tuple[HostedSuiteDefinition, ...]
    writer_generation: str
    activation_id: str

    def __post_init__(self) -> None:
        candidates = tuple(self.accepted_candidates)
        suites = tuple(self.hosted_suites)
        if not self.stable_action_id or not self.repository or not self.campaign_key:
            raise BatchIntegratorError(
                "BATCH_REQUEST_IDENTITY_INVALID",
                "request identity is required",
            )
        _require_digest("plan_revision_digest", self.plan_revision_digest)
        ordered = tuple(
            sorted(
                candidates,
                key=lambda item: (
                    item.accepted_sequence,
                    item.ticket_key,
                    item.candidate_sha,
                ),
            )
        )
        if candidates != ordered:
            raise BatchIntegratorError(
                "BATCH_CANDIDATE_ORDER_INVALID",
                "accepted_candidates must use canonical queue order",
            )
        sequences = tuple(item.accepted_sequence for item in candidates)
        if len(sequences) != len(set(sequences)):
            raise BatchIntegratorError(
                "BATCH_SEQUENCE_DUPLICATE",
                "accepted_sequence must be unique",
            )
        for candidate in candidates:
            _accepted_candidate_body(candidate)
            if (
                candidate.repository != self.repository
                or candidate.campaign_key != self.campaign_key
                or candidate.plan_revision_digest != self.plan_revision_digest
                or candidate.target_branch != self.target.target_branch
            ):
                raise BatchIntegratorError(
                    "BATCH_CANDIDATE_SCOPE_MISMATCH",
                    "accepted Candidate is outside the request scope",
                )
        suite_ids = tuple(suite.suite_id for suite in suites)
        if not suites or suite_ids != tuple(sorted(set(suite_ids))):
            raise BatchIntegratorError(
                "BATCH_HOSTED_SUITE_ORDER_INVALID",
                "hosted_suites must be non-empty, sorted, and unique",
            )
        object.__setattr__(self, "accepted_candidates", candidates)
        object.__setattr__(self, "hosted_suites", suites)

    def canonical(self) -> dict[str, object]:
        return {
            "kind": "batch-delivery-request.v1",
            "stable_action_id": self.stable_action_id,
            "repository": self.repository,
            "campaign_key": self.campaign_key,
            "plan_revision_digest": self.plan_revision_digest,
            "target": self.target.canonical(),
            "accepted_candidates": [
                _accepted_candidate_body(candidate)
                for candidate in self.accepted_candidates
            ],
            "local_suite": self.local_suite.canonical(),
            "hosted_suites": [suite.canonical() for suite in self.hosted_suites],
            "writer_generation": self.writer_generation,
            "activation_id": self.activation_id,
        }

    @property
    def request_digest(self) -> str:
        return digest_value(self.canonical())


@dataclass(frozen=True)
class BatchDeliveryAction:
    stable_action_id: str
    request_digest: str
    batch_id: str
    batch_sha: str
    member_ticket_keys: tuple[str, ...]

    def __post_init__(self) -> None:
        _require_digest("request_digest", self.request_digest)
        _require_digest("batch_id", self.batch_id)
        _require_object_id("batch_sha", self.batch_sha)
        member_ticket_keys = tuple(self.member_ticket_keys)
        if (
            not member_ticket_keys
            or any(not isinstance(key, str) or not key for key in member_ticket_keys)
            or len(set(member_ticket_keys)) != len(member_ticket_keys)
        ):
            raise BatchIntegratorError(
                "BATCH_MEMBER_ORDER_INVALID",
                "member_ticket_keys must be non-empty and unique",
            )
        object.__setattr__(self, "member_ticket_keys", member_ticket_keys)


@dataclass(frozen=True)
class MemberDeliveryObservation:
    ticket_key: str
    work_run_key: str
    candidate_sha: str
    status: Literal["integrated", "preserved", "resume_required", "blocked"]
    evidence_digests: tuple[str, ...]
    resume_reason: str | None = None


@dataclass(frozen=True)
class BatchDeliveryProof:
    delivery_stable_action_id: str
    delivery_request_digest: str
    batch_id: str
    batch_sha: str
    member_ticket_keys: tuple[str, ...]
    local_check_receipt_digest: str
    publication_receipt_digest: str
    pull_request_number: int
    pull_request_head_sha: str
    hosted_result_receipt_digest: str
    integration_lease_digest: str
    target_branch: str
    target_head_sha: str
    target_readback_digest: str
    target_contains_batch_sha: bool
    pull_request_merge_target_sha: str
    merge_method: Literal["merge"]
    proof_digest: str

    def body(self) -> dict[str, object]:
        return {
            "delivery_stable_action_id": self.delivery_stable_action_id,
            "delivery_request_digest": self.delivery_request_digest,
            "batch_id": self.batch_id,
            "batch_sha": self.batch_sha,
            "member_ticket_keys": list(self.member_ticket_keys),
            "local_check_receipt_digest": self.local_check_receipt_digest,
            "publication_receipt_digest": self.publication_receipt_digest,
            "pull_request_number": self.pull_request_number,
            "pull_request_head_sha": self.pull_request_head_sha,
            "hosted_result_receipt_digest": self.hosted_result_receipt_digest,
            "integration_lease_digest": self.integration_lease_digest,
            "target_branch": self.target_branch,
            "target_head_sha": self.target_head_sha,
            "target_readback_digest": self.target_readback_digest,
            "target_contains_batch_sha": self.target_contains_batch_sha,
            "pull_request_merge_target_sha": self.pull_request_merge_target_sha,
            "merge_method": self.merge_method,
        }

    @classmethod
    def create(cls, **facts: object) -> "BatchDeliveryProof":
        body = dict(facts)
        return cls(
            **body,
            proof_digest=digest_value(
                {"kind": "batch-delivery-proof.v1", **body}
            ),
        )

    def canonical(self) -> dict[str, object]:
        digest_values = (
            self.delivery_request_digest,
            self.batch_id,
            self.local_check_receipt_digest,
            self.publication_receipt_digest,
            self.hosted_result_receipt_digest,
            self.integration_lease_digest,
            self.target_readback_digest,
            self.proof_digest,
        )
        object_ids = (
            self.batch_sha,
            self.pull_request_head_sha,
            self.target_head_sha,
            self.pull_request_merge_target_sha,
        )
        if (
            not isinstance(self.delivery_stable_action_id, str)
            or not self.delivery_stable_action_id
            or not isinstance(self.target_branch, str)
            or not self.target_branch
            or type(self.member_ticket_keys) is not tuple
            or not self.member_ticket_keys
            or any(
                not isinstance(ticket_key, str) or not ticket_key
                for ticket_key in self.member_ticket_keys
            )
            or len(set(self.member_ticket_keys)) != len(self.member_ticket_keys)
            or type(self.pull_request_number) is not int
            or self.pull_request_number <= 0
            or any(
                not isinstance(value, str)
                or len(value) != 64
                or any(character not in "0123456789abcdef" for character in value)
                for value in digest_values
            )
            or any(
                not isinstance(value, str)
                or len(value) not in {40, 64}
                or any(character not in "0123456789abcdef" for character in value)
                for value in object_ids
            )
        ):
            raise DeliveryIdentityMismatch(
                "Batch delivery proof has an invalid action, digest, object, PR, or member identity"
            )
        expected = digest_value(
            {"kind": "batch-delivery-proof.v1", **self.body()}
        )
        if self.proof_digest != expected:
            raise DeliveryIdentityMismatch(
                "Batch delivery proof digest changed after readback"
            )
        if (
            self.pull_request_head_sha != self.batch_sha
            or self.target_contains_batch_sha is not True
            or self.pull_request_merge_target_sha != self.target_head_sha
            or self.merge_method != "merge"
        ):
            raise DeliveryIdentityMismatch(
                "Batch delivery proof does not preserve exact PR and target identity"
            )
        return {**self.body(), "proof_digest": self.proof_digest}


@dataclass(frozen=True)
class BatchDeliveryObservation:
    stable_action_id: str
    batch_id: str
    batch_sha: str
    phase: Literal["running", "wait", "complete", "decision", "blocked"]
    reason: str
    receipt_digest: str
    retry_count: int
    fallback_generation: int
    members: tuple[MemberDeliveryObservation, ...]
    delivery_proofs: tuple[BatchDeliveryProof, ...]

    def __post_init__(self) -> None:
        if (
            self.phase not in {"running", "wait", "complete", "decision", "blocked"}
            or type(self.retry_count) is not int
            or self.retry_count < 0
            or type(self.fallback_generation) is not int
            or self.fallback_generation not in {0, 1}
            or type(self.members) is not tuple
            or type(self.delivery_proofs) is not tuple
        ):
            raise DeliveryIdentityMismatch(
                "Batch observation phase, retry, fallback, members, or proofs are invalid"
            )
        if self.phase == "complete":
            if not self.delivery_proofs:
                raise DeliveryIdentityMismatch(
                    "complete Batch observation has no exact delivery proofs"
                )
            for proof in self.delivery_proofs:
                proof.canonical()
            member_ticket_keys = tuple(member.ticket_key for member in self.members)
            proof_ticket_keys = tuple(
                ticket_key
                for proof in self.delivery_proofs
                for ticket_key in proof.member_ticket_keys
            )
            if (
                any(member.status != "integrated" for member in self.members)
                or len(set(proof_ticket_keys)) != len(proof_ticket_keys)
                or proof_ticket_keys != member_ticket_keys
            ):
                raise DeliveryIdentityMismatch(
                    "complete Batch proof partition does not exactly cover integrated members"
                )
            if self.fallback_generation == 0:
                direct = self.delivery_proofs
                if (
                    len(direct) != 1
                    or direct[0].delivery_stable_action_id != self.stable_action_id
                    or direct[0].batch_id != self.batch_id
                    or direct[0].batch_sha != self.batch_sha
                ):
                    raise DeliveryIdentityMismatch(
                        "direct Batch proof does not match its action and Batch identity"
                    )
            elif self.fallback_generation == 1:
                if (
                    len(self.members) <= 1
                    or len(self.delivery_proofs) != len(self.members)
                    or any(len(proof.member_ticket_keys) != 1 for proof in self.delivery_proofs)
                    or any(
                        proof.delivery_stable_action_id == self.stable_action_id
                        for proof in self.delivery_proofs
                    )
                    or len(
                        {
                            proof.delivery_stable_action_id
                            for proof in self.delivery_proofs
                        }
                    )
                    != len(self.delivery_proofs)
                    or len({proof.batch_id for proof in self.delivery_proofs})
                    != len(self.delivery_proofs)
                    or len({proof.batch_sha for proof in self.delivery_proofs})
                    != len(self.delivery_proofs)
                ):
                    raise DeliveryIdentityMismatch(
                        "fallback parent must bind one distinct Singleton proof per member"
                    )
            else:
                raise DeliveryIdentityMismatch(
                    "complete Batch observation has an invalid fallback generation"
                )
        elif self.delivery_proofs:
            raise DeliveryIdentityMismatch(
                "non-complete Batch observation cannot claim delivery proofs"
            )
        if self.receipt_digest != digest_value(
            {"kind": "batch-observation.v1", **self.body()}
        ):
            raise DeliveryIdentityMismatch(
                "Batch observation receipt does not bind exact delivery proof"
            )

    def body(self) -> dict[str, object]:
        return {
            "stable_action_id": self.stable_action_id,
            "batch_id": self.batch_id,
            "batch_sha": self.batch_sha,
            "phase": self.phase,
            "reason": self.reason,
            "retry_count": self.retry_count,
            "fallback_generation": self.fallback_generation,
            "members": [asdict(member) for member in self.members],
            "delivery_proofs": [
                proof.canonical() for proof in self.delivery_proofs
            ],
        }

    def canonical(self) -> dict[str, object]:
        return {**self.body(), "receipt_digest": self.receipt_digest}


@dataclass(frozen=True)
class AncestorReadback:
    ancestor_sha: str
    descendant_sha: str
    is_ancestor: bool
    readback_digest: str

    def body(self) -> dict[str, object]:
        return {
            "ancestor_sha": self.ancestor_sha,
            "descendant_sha": self.descendant_sha,
            "is_ancestor": self.is_ancestor,
        }

    def validate(self) -> None:
        _require_object_id("ancestor_sha", self.ancestor_sha)
        _require_object_id("descendant_sha", self.descendant_sha)
        if type(self.is_ancestor) is not bool:
            raise BatchIntegratorError(
                "CLEAN_BASE_ANCESTOR_READBACK_MISMATCH",
                "ancestor readback has a non-boolean ancestry fact",
            )
        _require_digest("ancestor readback_digest", self.readback_digest)
        if digest_value({"kind": "ancestor-readback.v1", **self.body()}) != self.readback_digest:
            raise BatchIntegratorError(
                "CLEAN_BASE_ANCESTOR_READBACK_MISMATCH",
                "ancestor readback digest is not authoritative",
            )


@dataclass(frozen=True)
class TargetDeltaReadback:
    base_sha: str
    target_head_sha: str
    interaction_keys: tuple[InteractionKey, ...]
    protected_interaction_keys: tuple[InteractionKey, ...]
    facts_digest: str
    readback_digest: str

    def body(self) -> dict[str, object]:
        return {
            "base_sha": self.base_sha,
            "target_head_sha": self.target_head_sha,
            "interaction_keys": [key.canonical() for key in self.interaction_keys],
            "protected_interaction_keys": [
                key.canonical() for key in self.protected_interaction_keys
            ],
        }

    def canonical(self) -> dict[str, object]:
        _require_object_id("target delta base_sha", self.base_sha)
        _require_object_id("target delta target_head_sha", self.target_head_sha)
        _require_digest("target delta facts_digest", self.facts_digest)
        _require_digest("target delta readback_digest", self.readback_digest)
        derived_protected = tuple(
            key for key in self.interaction_keys if key.requires_singleton
        )
        if derived_protected != self.protected_interaction_keys:
            raise BatchIntegratorError(
                "TARGET_DELTA_PROTECTED_INTERACTION",
                "target delta protected InteractionKey partition is not canonical",
            )
        body = self.body()
        if digest_value(body) != self.facts_digest:
            raise BatchIntegratorError(
                "TARGET_DELTA_FACTS_DIGEST_MISMATCH",
                "target delta Interaction Key facts are not canonical",
            )
        if digest_value({"kind": "target-delta-readback.v1", **body}) != self.readback_digest:
            raise BatchIntegratorError(
                "TARGET_DELTA_READBACK_DIGEST_MISMATCH",
                "target delta readback is not authoritative",
            )
        return body


def _validate_hosted_receipt_identity(
    receipt: object,
    action: BatchDeliveryAction,
    suite: HostedSuiteDefinition,
) -> None:
    if (
        not hasattr(receipt, "stable_action_id")
        or not hasattr(receipt, "batch_sha")
        or not hasattr(receipt, "suite_id")
        or not hasattr(receipt, "provider_check_id")
        or receipt.stable_action_id != action.stable_action_id
        or receipt.batch_sha != action.batch_sha
        or receipt.suite_id != suite.suite_id
        or receipt.provider_check_id != "check:1"
    ):
        raise DeliveryIdentityMismatch(
            "hosted receipt did not match action, Batch SHA, suite, and provider check"
        )


def _singleton_action_id(
    parent_action: BatchDeliveryAction, member: AcceptedCandidateReceipt
) -> str:
    return digest_value(
        {
            "kind": "singleton-action.v1",
            "parent_action_id": parent_action.stable_action_id,
            "parent_batch_id": parent_action.batch_id,
            "ticket_key": member.ticket_key,
            "candidate_receipt_digest": member.digest,
        }
    )


def make_singleton_action(
    parent_action: BatchDeliveryAction,
    member: AcceptedCandidateReceipt,
    child_batch_id: str,
    child_batch_sha: str,
    *,
    request_digest: str | None = None,
) -> BatchDeliveryAction:
    """Derive the stable delivery identity for one fallback Singleton."""

    child_action_id = _singleton_action_id(parent_action, member)
    return BatchDeliveryAction(
        stable_action_id=child_action_id,
        request_digest=request_digest
        or digest_value(
            {
                "kind": "singleton-request.v1",
                "parent_request_digest": parent_action.request_digest,
                "ticket_key": member.ticket_key,
                "candidate_receipt_digest": member.digest,
            }
        ),
        batch_id=child_batch_id,
        batch_sha=child_batch_sha,
        member_ticket_keys=(member.ticket_key,),
    )


def form_batch_members(
    candidates: tuple[AcceptedCandidateReceipt, ...],
    target: BatchTarget,
    *,
    member_limit: int,
) -> tuple[AcceptedCandidateReceipt, ...]:
    if not 1 <= member_limit <= 4:
        raise BatchIntegratorError(
            "BATCH_MEMBER_LIMIT_INVALID",
            "member limit must be between one and four",
        )
    eligible = tuple(
        candidate
        for candidate in candidates
        if candidate.repository == target.repository
        and candidate.target_branch == target.target_branch
    )
    ordered = tuple(
        sorted(
            eligible,
            key=lambda item: (
                item.accepted_sequence,
                item.ticket_key,
                item.candidate_sha,
            ),
        )
    )
    sequences = [item.accepted_sequence for item in ordered]
    if len(sequences) != len(set(sequences)):
        raise BatchIntegratorError(
            "BATCH_SEQUENCE_DUPLICATE",
            "accepted_sequence must be unique",
        )
    if not ordered:
        return ()
    seed = ordered[0]
    selected: list[AcceptedCandidateReceipt] = [seed]
    if _requires_singleton(seed):
        return (seed,)
    for candidate in ordered[1:]:
        if len(selected) == member_limit:
            break
        if (
            candidate.repository != target.repository
            or candidate.target_branch != target.target_branch
            or candidate.campaign_key != seed.campaign_key
            or candidate.plan_revision_digest != seed.plan_revision_digest
        ):
            continue
        decision = _pairwise_compatibility(candidate, selected, target)
        if decision in {
            CompatibilityDecision.COMPATIBLE,
            CompatibilityDecision.CLEAN_BASE_ADVANCE,
        }:
            selected.append(candidate)
    return tuple(selected)


def _requires_singleton(candidate: AcceptedCandidateReceipt) -> bool:
    return candidate.assurance == "strict" or candidate.gitlink_change or any(
        key.requires_singleton for key in candidate.interaction_keys
    )


def _pairwise_compatibility(
    candidate: AcceptedCandidateReceipt,
    selected: list[AcceptedCandidateReceipt],
    target: BatchTarget,
) -> CompatibilityDecision:
    if _requires_singleton(candidate):
        return CompatibilityDecision.SINGLETON_REQUIRED
    clean_base_advance = False
    for member in selected:
        if (
            candidate.authority_subtree_digest != member.authority_subtree_digest
            or candidate.policy_witness_digest != member.policy_witness_digest
            or candidate.delivery_identity_digest != member.delivery_identity_digest
            or candidate.assurance_requirement_digest
            != member.assurance_requirement_digest
            or candidate.check_environment_digest != member.check_environment_digest
            or candidate.protected_surfaces != member.protected_surfaces
        ):
            return CompatibilityDecision.INCOMPATIBLE
        if (
            candidate.base_sha != member.base_sha
            or candidate.base_tree_oid != member.base_tree_oid
        ):
            clean_base_advance = True
        for left in candidate.interaction_keys:
            for right in member.interaction_keys:
                if (
                    left.classification == InteractionClassification.ORDINARY
                    and right.classification == InteractionClassification.ORDINARY
                    and left.namespace == right.namespace
                    and left.value == right.value
                ):
                    return CompatibilityDecision.INCOMPATIBLE
    if (
        candidate.base_sha != target.target_head_sha
        or candidate.base_tree_oid != target.target_tree_oid
    ):
        clean_base_advance = True
    if clean_base_advance:
        return CompatibilityDecision.CLEAN_BASE_ADVANCE
    return CompatibilityDecision.COMPATIBLE


class BatchIntegrator:
    def __init__(
        self,
        *,
        journal: "BatchDeliveryJournal",
        git: "GitBatchDriver",
        local: "LocalSuiteDriver",
        hosted: "HostedBatchDriver",
        configuration: BatchIntegratorConfiguration,
    ) -> None:
        self.journal = journal
        self.git = git
        self.local = local
        self.hosted = hosted
        self.configuration = configuration
        self.formation_calls = 0
        self._requests: dict[str, BatchDeliveryRequest] = {}

    def prepare(self, request: BatchDeliveryRequest) -> BatchDeliveryAction:
        self._validate_request(request)

        read_action = getattr(self.journal, "read_action", None)
        existing = (
            read_action(request.stable_action_id)
            if callable(read_action)
            else None
        )
        if existing is not None:
            if existing.request_digest != request.request_digest:
                raise DeliveryIdentityMismatch(
                    "journal action request digest differs from the new request"
                )
            read_ref = getattr(self.git, "read_ref", None)
            current_ref = (
                read_ref(f"refs/gwo-v8/integration-batches/{existing.batch_id}")
                if callable(read_ref)
                else existing.batch_sha
            )
            if current_ref is not None:
                if current_ref != existing.batch_sha:
                    raise DeliveryIdentityMismatch(
                        "journal action Batch ref changed after preparation"
                    )
            elif existing.phase != "prepared":
                current_ref = existing.batch_sha
            else:
                existing = None
        if existing is not None:
            action = BatchDeliveryAction(
                stable_action_id=existing.stable_action_id,
                request_digest=existing.request_digest,
                batch_id=existing.batch_id,
                batch_sha=existing.batch_sha,
                member_ticket_keys=self._member_ticket_keys(existing),
            )
            self._validate_action_record(existing, action)
            self._requests[request.stable_action_id] = request
            return action

        read_preparation = getattr(self.journal, "read_preparation", None)
        preparation = (
            read_preparation(request.stable_action_id)
            if callable(read_preparation)
            else None
        )
        if preparation is not None:
            if preparation.request_digest != request.request_digest:
                raise DeliveryIdentityMismatch(
                    "durable Batch preparation request digest differs from the new request"
                )
            preparation_state = self._decode_state(preparation)
            try:
                preparation_request = preparation_state["request"]
                target_state = preparation_request["target"]
                selected_state = preparation_state["parent_candidates"]
                if not isinstance(preparation_request, dict) or not isinstance(
                    target_state, dict
                ) or not isinstance(selected_state, list):
                    raise TypeError
                target = BatchTarget(**target_state)
                members = tuple(
                    self._receipt_from_canonical(item) for item in selected_state
                )
            except (KeyError, TypeError, ValueError) as error:
                raise DeliveryIdentityMismatch(
                    "durable Batch preparation snapshot is malformed"
                ) from error
            if target != request.target:
                raise DeliveryIdentityMismatch(
                    "durable Batch preparation target differs from the request"
                )
            batch_id = preparation.batch_id
            state = preparation_state
        else:
            self.formation_calls += 1
            target = self.git.read_target(request.target)
            if target != request.target:
                raise DeliveryIdentityMismatch(
                    "Batch target changed while freezing the selected members"
                )
            members = form_batch_members(
                request.accepted_candidates,
                target,
                member_limit=self.configuration.member_limit_for(request.repository),
            )
            state = json.loads(self._state_json_for_request(request, members))
            batch_id = digest_value(
                {
                    "kind": "batch-id.v1",
                    "campaign_key": request.campaign_key,
                    "plan_revision_digest": request.plan_revision_digest,
                    "target": target.target_facts_digest,
                    "members": [member.digest for member in members],
                    "local_suite": request.local_suite.definition_digest,
                    "hosted_suites": [
                        suite.definition_digest for suite in request.hosted_suites
                    ],
                }
            )
            persist_preparation = getattr(self.journal, "persist_preparation", None)
            if callable(persist_preparation):
                persist_preparation(
                    request.stable_action_id,
                    request.request_digest,
                    batch_id,
                    self._encode_state(state),
                )
        if not members:
            raise BatchIntegratorError("BATCH_EMPTY", "no eligible accepted Candidate")
        try:
            batch_sha = self.git.compose_batch(batch_id, target, members)
        except BatchIntegratorError as error:
            if error.code not in {
                "BATCH_COMPOSITION_CONFLICT",
                "BATCH_CLEAN_BASE_COMPOSITION_CONFLICT",
            } or len(members) <= 1:
                raise
            batch_sha = digest_value(
                {
                    "kind": "failed-composition-parent.v1",
                    "batch_id": batch_id,
                    "request_digest": request.request_digest,
                }
            )
            state["composition_failure"] = error.code
            action = BatchDeliveryAction(
                stable_action_id=request.stable_action_id,
                request_digest=request.request_digest,
                batch_id=batch_id,
                batch_sha=batch_sha,
                member_ticket_keys=tuple(member.ticket_key for member in members),
            )
            create_action = getattr(self.journal, "create_action", None)
            if not callable(create_action):
                raise BatchIntegratorError(
                    "BATCH_ACTION_PERSISTENCE_FAILED",
                    "composition failure cannot persist its selected members",
                )
            create_action(
                action,
                action.request_digest,
                phase="prepared",
                reason="composition failure requires Singleton fallback",
                retry_count=0,
                fallback_generation=0,
                state_json=self._encode_state(state),
            )
            record = self.journal.read_action(action.stable_action_id)
            if record is None:
                raise BatchIntegratorError(
                    "BATCH_ACTION_PERSISTENCE_FAILED",
                    "composition failure action was not readable after creation",
                )
            self._requests[request.stable_action_id] = request
            self._queue_singletons(record, action, request, state)
            clear_preparation = getattr(self.journal, "clear_preparation", None)
            if callable(clear_preparation):
                clear_preparation(request.stable_action_id)
            return action
        action = BatchDeliveryAction(
            stable_action_id=request.stable_action_id,
            request_digest=request.request_digest,
            batch_id=batch_id,
            batch_sha=batch_sha,
            member_ticket_keys=tuple(member.ticket_key for member in members),
        )
        create_action = getattr(self.journal, "create_action", None)
        if callable(create_action):
            create_action(
                action,
                action.request_digest,
                phase="prepared",
                reason="prepared",
                retry_count=0,
                fallback_generation=0,
                state_json=self._encode_state(state),
            )
        clear_preparation = getattr(self.journal, "clear_preparation", None)
        if callable(clear_preparation):
            clear_preparation(request.stable_action_id)
        self._requests[request.stable_action_id] = request
        return action

    def readback(
        self, action: BatchDeliveryAction
    ) -> "BatchDeliveryObservation | None":
        record = self.journal.read_action(action.stable_action_id)
        if record is None:
            return None
        self._validate_action_record(record, action)
        if record.phase not in {"complete", "decision", "blocked"}:
            return None
        return self._observation_from_record(record)

    def execute(self, action: BatchDeliveryAction) -> "BatchDeliveryObservation":
        existing = self.readback(action)
        if existing is not None:
            return existing

        record = self.journal.read_action(action.stable_action_id)
        if record is None:
            raise BatchIntegratorError(
                "BATCH_ACTION_MISSING",
                "prepare must persist the action before execute",
            )
        self._validate_action_record(record, action)
        request = self._requests.get(action.stable_action_id)
        if request is None:
            request = self._request_for_action(record)
            self._requests[action.stable_action_id] = request
        if request.request_digest != action.request_digest:
            raise DeliveryIdentityMismatch(
                "delivery action request digest differs from its durable request"
            )

        while True:
            try:
                next_record = self._advance_one_delivery_step(
                    record, action, request
                )
            except (DeliveryIdentityMismatch, DeliveryAttributionAmbiguous) as error:
                self._block_identity_failure(action, error)
                raise
            if next_record.version == record.version + 1:
                committed = self.journal.compare_and_swap_action(
                    action.stable_action_id,
                    expected_version=record.version,
                    expected_phase=record.phase,
                    next_record=next_record,
                )
            else:
                # Fallback queue construction durably advances the parent
                # several times while preserving each member's evidence and
                # creating the child actions.  Adopt that already-CASed
                # record rather than applying the outer step a second time.
                committed = self.journal.read_action(action.stable_action_id)
                if committed != next_record:
                    raise DeliveryIdentityMismatch(
                        "Batch recovery step changed its durable parent record"
                    )
            if committed.phase in {"wait", "decision", "complete", "blocked"}:
                return self._observation_from_record(committed)
            record = committed

    @staticmethod
    def _encode_state(state: dict[str, object]) -> str:
        return json.dumps(state, sort_keys=True, separators=(",", ":"))

    @staticmethod
    def _decode_state(record: object) -> dict[str, object]:
        state_json = getattr(record, "state_json", "{}")
        try:
            value = json.loads(state_json or "{}")
        except (TypeError, json.JSONDecodeError) as error:
            raise DeliveryIdentityMismatch(
                "journal action state is not canonical JSON"
            ) from error
        if not isinstance(value, dict):
            raise DeliveryIdentityMismatch("journal action state is not an object")
        return value

    @classmethod
    def _member_ticket_keys(cls, record: object) -> tuple[str, ...]:
        state = cls._decode_state(record)
        members = state.get("members")
        if not isinstance(members, list):
            raise DeliveryIdentityMismatch("journal member snapshot is missing")
        try:
            keys = tuple(item["ticket_key"] for item in members)
        except (KeyError, TypeError) as error:
            raise DeliveryIdentityMismatch(
                "journal member snapshot is malformed"
            ) from error
        if not keys or any(not isinstance(key, str) or not key for key in keys):
            raise DeliveryIdentityMismatch("journal member identity is malformed")
        return keys

    def _validate_action_record(
        self, record: object, action: BatchDeliveryAction
    ) -> None:
        if getattr(record, "stable_action_id", None) != action.stable_action_id:
            raise DeliveryIdentityMismatch("journal action ID differs from requested action")
        if (
            getattr(record, "request_digest", None) != action.request_digest
            or getattr(record, "batch_id", None) != action.batch_id
            or getattr(record, "batch_sha", None) != action.batch_sha
        ):
            raise DeliveryIdentityMismatch("journal action or Batch identity changed")
        if self._member_ticket_keys(record) != action.member_ticket_keys:
            raise DeliveryIdentityMismatch("journal member identity changed")

    @staticmethod
    def _receipt_from_canonical(
        body: dict[str, object]
    ) -> AcceptedCandidateReceipt:
        values = dict(body)
        values.pop("kind", None)
        values.pop("receipt_digest", None)
        values["interaction_keys"] = tuple(
            InteractionKey(
                namespace=item["namespace"],
                value=item["value"],
                classification=InteractionClassification(item["classification"]),
            )
            for item in values["interaction_keys"]
        )
        values["protected_surfaces"] = tuple(values["protected_surfaces"])
        values["evidence_digests"] = tuple(values["evidence_digests"])
        return AcceptedCandidateReceipt(**values)

    def _request_for_action(self, record: object) -> BatchDeliveryRequest:
        state = self._decode_state(record)
        request_state = state.get("request")
        if not isinstance(request_state, dict):
            raise DeliveryIdentityMismatch("journal request snapshot is missing")
        try:
            target_state = request_state["target"]
            local_state = request_state["local_suite"]
            hosted_state = request_state["hosted_suites"]
            member_state = state["members"]
            if not isinstance(target_state, dict) or not isinstance(
                local_state, dict
            ) or not isinstance(hosted_state, list) or not isinstance(
                member_state, list
            ):
                raise TypeError
            target = BatchTarget(**target_state)
            local_suite = LocalSuiteDefinition(
                suite_id=local_state["suite_id"],
                definition_digest=local_state["definition_digest"],
                command=tuple(local_state["command"]),
            )
            hosted_suites = tuple(
                HostedSuiteDefinition(**suite) for suite in hosted_state
            )
            candidate_state = state.get(
                "request_candidates", state.get("parent_candidates", member_state)
            )
            if not isinstance(candidate_state, list):
                raise TypeError
            candidates = tuple(self._receipt_from_canonical(item) for item in candidate_state)
            request = BatchDeliveryRequest(
                stable_action_id=record.stable_action_id,
                repository=request_state["repository"],
                campaign_key=request_state["campaign_key"],
                plan_revision_digest=request_state["plan_revision_digest"],
                target=target,
                accepted_candidates=candidates,
                local_suite=local_suite,
                hosted_suites=hosted_suites,
                writer_generation=request_state["writer_generation"],
                activation_id=request_state["activation_id"],
            )
            if request.request_digest != record.request_digest:
                raise DeliveryIdentityMismatch(
                    "journal request snapshot digest does not match the action"
                )
            return request
        except (KeyError, TypeError, ValueError) as error:
            raise DeliveryIdentityMismatch(
                "journal request snapshot is not a valid delivery request"
            ) from error

    def _observation_from_record(
        self, record: object
    ) -> BatchDeliveryObservation:
        state = self._decode_state(record)
        phase = {
            "prepared": "running",
            "composed": "running",
            "local_checked": "running",
            "published": "running",
            "hosted": "running",
            "integrating": "running",
            "wait": "wait",
            "decision": "decision",
            "complete": "complete",
            "blocked": "blocked",
        }.get(getattr(record, "phase", ""))
        if phase is None:
            raise DeliveryIdentityMismatch("journal action phase is not recognized")
        raw_members = state.get("members", [])
        if not isinstance(raw_members, list):
            raise DeliveryIdentityMismatch("journal member snapshot is malformed")
        try:
            members = tuple(
                MemberDeliveryObservation(
                    ticket_key=item["ticket_key"],
                    work_run_key=item["work_run_key"],
                    candidate_sha=item["candidate_sha"],
                    status=item.get("status", "preserved"),
                    evidence_digests=tuple(item["evidence_digests"]),
                    resume_reason=item.get("resume_reason"),
                )
                for item in raw_members
            )
        except (KeyError, TypeError) as error:
            raise DeliveryIdentityMismatch(
                "journal member observation is malformed"
            ) from error
        raw_proofs = state.get("delivery_proofs", [])
        if not isinstance(raw_proofs, list):
            raise DeliveryIdentityMismatch("journal delivery proofs are malformed")
        proofs = (
            tuple(
                BatchDeliveryProof(
                    **{
                        **item,
                        "member_ticket_keys": tuple(item["member_ticket_keys"]),
                    }
                )
                for item in raw_proofs
            )
            if phase == "complete"
            else ()
        )
        body = {
            "stable_action_id": record.stable_action_id,
            "batch_id": record.batch_id,
            "batch_sha": record.batch_sha,
            "phase": phase,
            "reason": record.reason,
            "retry_count": record.retry_count,
            "fallback_generation": record.fallback_generation,
            "members": [asdict(member) for member in members],
            "delivery_proofs": [proof.canonical() for proof in proofs],
        }
        return BatchDeliveryObservation(
            stable_action_id=record.stable_action_id,
            batch_id=record.batch_id,
            batch_sha=record.batch_sha,
            phase=phase,
            reason=record.reason,
            receipt_digest=digest_value(
                {"kind": "batch-observation.v1", **body}
            ),
            retry_count=record.retry_count,
            fallback_generation=record.fallback_generation,
            members=members,
            delivery_proofs=proofs,
        )

    @staticmethod
    def _next_record(
        record: object,
        *,
        phase: str,
        reason: str,
        state: dict[str, object],
    ) -> object:
        return replace(
            record,
            phase=phase,
            reason=reason,
            state_json=BatchIntegrator._encode_state(state),
            version=record.version + 1,
        )

    def _read_or_persist_hosted_result(
        self,
        action: BatchDeliveryAction,
        request: BatchDeliveryRequest,
        suite: HostedSuiteDefinition,
    ):
        from ._batch_integrator_store import HostedResultReceipt

        persisted = self.journal.read_terminal_hosted_result(
            action.stable_action_id,
            action.batch_sha,
            suite.suite_id,
        )
        if persisted is not None:
            _validate_hosted_receipt_identity(persisted, action, suite)
            return persisted

        observed = self.hosted.read_hosted_result(
            request.repository,
            action.batch_sha,
            suite,
        )
        if observed.outcome == "pending":
            return None
        if (
            observed.repository != request.repository
            or observed.batch_sha != action.batch_sha
            or observed.suite_id != suite.suite_id
            or observed.provider_check_id != "check:1"
        ):
            raise DeliveryIdentityMismatch(
                "provider observation did not match repository, Batch SHA, suite, and check"
            )
        receipt = HostedResultReceipt.create(
            stable_action_id=action.stable_action_id,
            batch_sha=action.batch_sha,
            suite_id=observed.suite_id,
            provider_check_id=observed.provider_check_id,
            outcome=observed.outcome,
            observation_digest=observed.observation_digest,
            source_ref=observed.source_ref,
        )
        if receipt.outcome == "infrastructure_failure":
            return receipt
        return self.journal.persist_hosted_result(receipt)

    @staticmethod
    def _frozen_members(
        state: dict[str, object], action: BatchDeliveryAction
    ) -> tuple[AcceptedCandidateReceipt, ...]:
        raw_members = state.get("parent_candidates")
        if not isinstance(raw_members, list):
            raise DeliveryIdentityMismatch(
                "journal selected Candidate snapshot is missing"
            )
        try:
            members = tuple(
                BatchIntegrator._receipt_from_canonical(item) for item in raw_members
            )
        except (KeyError, TypeError, ValueError) as error:
            raise DeliveryIdentityMismatch(
                "journal selected Candidate snapshot is malformed"
            ) from error
        if tuple(member.ticket_key for member in members) != action.member_ticket_keys:
            raise DeliveryIdentityMismatch(
                "journal selected Candidate snapshot differs from the action"
            )
        return members

    def _advance_hosted_result(
        self,
        record: object,
        action: BatchDeliveryAction,
        request: BatchDeliveryRequest,
        state: dict[str, object],
    ) -> object:
        suite = request.hosted_suites[0]
        receipt = self._read_or_persist_hosted_result(action, request, suite)
        if receipt is None:
            state["hosted_pending"] = {
                "stable_action_id": action.stable_action_id,
                "batch_sha": action.batch_sha,
                "suite_id": suite.suite_id,
                "provider_check_id": "check:1",
            }
            return self._next_record(
                record,
                phase="wait",
                reason="HostedResultPending",
                state=state,
            )

        _validate_hosted_receipt_identity(receipt, action, suite)
        state["hosted_receipt"] = {
            **receipt.body(),
            "receipt_digest": receipt.receipt_digest,
        }
        if receipt.outcome == "infrastructure_failure":
            target = self.git.read_target(request.target)
            if target != request.target:
                raise DeliveryIdentityMismatch(
                    "target facts changed before unchanged-SHA infrastructure retry"
                )
            state["next_check"] = {
                "stable_action_id": action.stable_action_id,
                "batch_sha": action.batch_sha,
                "suite_id": suite.suite_id,
                "provider_check_id": receipt.provider_check_id,
            }
            if record.retry_count >= self.configuration.infrastructure_retry_limit:
                return self._next_record(
                    record,
                    phase="blocked",
                    reason="InfrastructureRetryLimitExceeded",
                    state=state,
                )
            retry_number = record.retry_count + 1
            idempotency_key = digest_value(
                {
                    "kind": "batch-hosted-retry.v1",
                    "stable_action_id": action.stable_action_id,
                    "batch_sha": action.batch_sha,
                    "suite_id": suite.suite_id,
                    "provider_check_id": receipt.provider_check_id,
                    "retry_number": retry_number,
                }
            )
            state["retry_intent"] = {
                "stable_action_id": action.stable_action_id,
                "batch_sha": action.batch_sha,
                "suite_id": suite.suite_id,
                "provider_check_id": receipt.provider_check_id,
                "retry_number": retry_number,
                "idempotency_key": idempotency_key,
                "target_facts_digest": request.target.target_facts_digest,
            }
            return replace(
                self._next_record(
                    record,
                    phase="wait",
                    reason="InfrastructureRetryIntentPersisted",
                    state=state,
                ),
                retry_count=record.retry_count + 1,
            )

        if receipt.outcome == "code_failure":
            return self._classify_failure(
                record,
                action,
                request,
                receipt.outcome,
                state,
            )
        return self._next_record(
            record,
            phase="hosted",
            reason="hosted_receipt_verified",
            state=state,
        )

    def _perform_retry_intent(
        self,
        record: object,
        action: BatchDeliveryAction,
        request: BatchDeliveryRequest,
        state: dict[str, object],
    ) -> object:
        intent = state.get("retry_intent")
        if not isinstance(intent, dict):
            raise DeliveryIdentityMismatch("durable infrastructure retry intent is malformed")
        expected = {
            "stable_action_id": action.stable_action_id,
            "batch_sha": action.batch_sha,
            "suite_id": request.hosted_suites[0].suite_id,
            "provider_check_id": "check:1",
            "retry_number": record.retry_count,
            "target_facts_digest": request.target.target_facts_digest,
        }
        for key, value in expected.items():
            if intent.get(key) != value:
                raise DeliveryIdentityMismatch(
                    "durable infrastructure retry intent identity changed"
                )
        idempotency_key = intent.get("idempotency_key")
        if not isinstance(idempotency_key, str) or idempotency_key != digest_value(
            {
                "kind": "batch-hosted-retry.v1",
                "stable_action_id": action.stable_action_id,
                "batch_sha": action.batch_sha,
                "suite_id": request.hosted_suites[0].suite_id,
                "provider_check_id": "check:1",
                "retry_number": record.retry_count,
            }
        ):
            raise DeliveryIdentityMismatch(
                "durable infrastructure retry idempotency identity changed"
            )
        retry_idempotent = getattr(self.hosted, "retry_hosted_idempotent", None)
        if callable(retry_idempotent):
            retry_idempotent(
                request.repository,
                action.batch_sha,
                intent["provider_check_id"],
                idempotency_key,
            )
        else:
            self.hosted.retry_hosted(
                request.repository,
                action.batch_sha,
                intent["provider_check_id"],
                idempotency_key,
            )
        state.pop("retry_intent", None)
        state.pop("hosted_receipt", None)
        state["retry_idempotency_key"] = idempotency_key
        return self._next_record(
            record,
            phase="published",
            reason="InfrastructureRetryReady",
            state=state,
        )

    def _classify_failure(
        self,
        record: object,
        action: BatchDeliveryAction,
        request: BatchDeliveryRequest,
        outcome: str,
        state: dict[str, object],
    ) -> object:
        members = self._frozen_members(state, action)
        if len(members) > 1 and record.fallback_generation == 0:
            return self._queue_singletons(record, action, request, state)
        if len(members) != 1:
            return replace(
                record,
                phase="blocked",
                reason="BatchFailureAttributionRequired",
                state_json=self._encode_state(state),
                version=record.version + 1,
            )

        member = members[0]
        raw_members = state.get("members")
        if not isinstance(raw_members, list) or not raw_members:
            raise DeliveryIdentityMismatch(
                "journal member snapshot is missing for Singleton recovery"
            )
        singleton = raw_members[0]
        if not isinstance(singleton, dict):
            raise DeliveryIdentityMismatch(
                "journal Singleton member snapshot is malformed"
            )
        singleton["status"] = "resume_required"
        singleton["resume_reason"] = outcome
        state["resume_required"] = True
        if not state.get("resume_directive_emitted"):
            directive = (member.work_run_key, member.review_finding_ledger_digest)
            resume_directives = getattr(self.git, "resume_directives", None)
            if isinstance(resume_directives, list):
                resume_directives.append(directive)
            state["resume_directive_emitted"] = True
        return replace(
            record,
            phase="decision",
            reason="WorkerResumeRequired",
            state_json=self._encode_state(state),
            version=record.version + 1,
        )

    def _state_json_for_request(
        self,
        request: BatchDeliveryRequest,
        members: tuple[AcceptedCandidateReceipt, ...],
    ) -> str:
        return self._encode_state(
            {
                "request": {
                    "request_digest": request.request_digest,
                    "repository": request.repository,
                    "campaign_key": request.campaign_key,
                    "plan_revision_digest": request.plan_revision_digest,
                    "target": request.target.canonical(),
                    "local_suite": request.local_suite.canonical(),
                    "hosted_suites": [
                        suite.canonical() for suite in request.hosted_suites
                    ],
                    "writer_generation": request.writer_generation,
                    "activation_id": request.activation_id,
                },
                "request_candidates": [
                    member.canonical() for member in request.accepted_candidates
                ],
                "parent_candidates": [member.canonical() for member in members],
                "members": [member.canonical() for member in members],
            }
        )

    def _queue_singletons(
        self,
        record: object,
        parent_action: BatchDeliveryAction,
        request: BatchDeliveryRequest,
        state: dict[str, object],
    ) -> object:
        parent_members = self._frozen_members(state, parent_action)
        state["parent_candidates"] = [member.canonical() for member in parent_members]
        state["members"] = [
            {
                "ticket_key": member.ticket_key,
                "work_run_key": member.work_run_key,
                "candidate_sha": member.candidate_sha,
                "candidate_receipt_digest": member.digest,
                "evidence_digests": list(member.evidence_digests),
                "review_finding_ledger_digest": member.review_finding_ledger_digest,
                "status": "preserved",
            }
            for member in parent_members
        ]
        state["singleton_queue"] = []
        state["singleton_materialization_complete"] = False
        state["singleton_materialization_index"] = 0
        state["next_singleton_index"] = 0
        state["delivery_proofs"] = []
        state["member_evidence"] = {
            member.ticket_key: {
                "candidate_sha": member.candidate_sha,
                "evidence_digests": list(member.evidence_digests),
                "review_finding_ledger_digest": member.review_finding_ledger_digest,
            }
            for member in parent_members
        }
        state.pop("resume_required", None)
        queued = self.journal.compare_and_swap_action(
            parent_action.stable_action_id,
            expected_version=record.version,
            expected_phase=record.phase,
            next_record=replace(
                record,
                phase="wait",
                reason="SingletonFallbackQueued",
                fallback_generation=1,
                state_json=self._encode_state(state),
                version=record.version + 1,
            ),
        )
        return self._materialize_singleton_queue(
            queued, parent_action, request
        )

    def _materialize_singleton_queue(
        self,
        record: object,
        parent_action: BatchDeliveryAction,
        request: BatchDeliveryRequest,
    ) -> object:
        state = self._decode_state(record)
        queue = state.get("singleton_queue")
        raw_members = state.get("parent_candidates")
        if not isinstance(queue, list) or not isinstance(raw_members, list):
            raise DeliveryIdentityMismatch(
                "Singleton fallback materialization snapshot is malformed"
            )
        try:
            parent_members = tuple(
                self._receipt_from_canonical(item) for item in raw_members
            )
        except (KeyError, TypeError, ValueError) as error:
            raise DeliveryIdentityMismatch(
                "Singleton fallback parent Candidate snapshot is malformed"
            ) from error
        if tuple(member.ticket_key for member in parent_members) != parent_action.member_ticket_keys:
            raise DeliveryIdentityMismatch(
                "Singleton fallback parent Candidate partition changed"
            )
        complete = state.get("singleton_materialization_complete")
        if type(complete) is not bool:
            raise DeliveryIdentityMismatch(
                "Singleton fallback materialization completion is malformed"
            )
        index = state.get("singleton_materialization_index", 0)
        if type(index) is not int or not 0 <= index <= len(parent_members):
            raise DeliveryIdentityMismatch(
                "Singleton fallback materialization index is malformed"
            )
        while True:
            intent = state.get("singleton_materialization_intent")
            if intent is not None:
                if not isinstance(intent, dict):
                    raise DeliveryIdentityMismatch(
                        "Singleton fallback materialization intent is malformed"
                    )
                required_intent = {
                    "index",
                    "member",
                    "target",
                    "child_batch_id",
                    "child_action_id",
                }
                if set(intent) != required_intent or intent.get("index") != index:
                    raise DeliveryIdentityMismatch(
                        "Singleton fallback materialization intent identity changed"
                    )
                try:
                    member = self._receipt_from_canonical(intent["member"])
                    current_target = BatchTarget(**intent["target"])
                except (KeyError, TypeError, ValueError) as error:
                    raise DeliveryIdentityMismatch(
                        "Singleton fallback materialization intent is not canonical"
                    ) from error
                if member != parent_members[index]:
                    raise DeliveryIdentityMismatch(
                        "Singleton fallback materialization member changed"
                    )
                child_batch_id = intent["child_batch_id"]
                if not isinstance(child_batch_id, str) or digest_value(
                    {
                        "kind": "singleton-batch.v1",
                        "parent_batch_id": parent_action.batch_id,
                        "ticket_key": member.ticket_key,
                        "candidate_receipt_digest": member.digest,
                    }
                ) != child_batch_id:
                    raise DeliveryIdentityMismatch(
                        "Singleton fallback child Batch identity changed"
                    )
                child_action_id = _singleton_action_id(parent_action, member)
                if intent["child_action_id"] != child_action_id:
                    raise DeliveryIdentityMismatch(
                        "Singleton fallback child action identity changed"
                    )
                child_batch_sha = self.git.compose_batch(
                    child_batch_id, current_target, (member,)
                )
                child_request = replace(
                    request,
                    stable_action_id=child_action_id,
                    target=current_target,
                    accepted_candidates=(member,),
                )
                child_action = make_singleton_action(
                    parent_action,
                    member,
                    child_batch_id,
                    child_batch_sha,
                    request_digest=child_request.request_digest,
                )
                self._requests[child_action.stable_action_id] = child_request
                self.journal.create_action(
                    child_action,
                    child_request.request_digest,
                    state_json=self._state_json_for_request(child_request, (member,)),
                )
                entry = json.loads(
                    self._encode_state(
                        {
                            "action": asdict(child_action),
                            "member": member.canonical(),
                            "target": current_target.canonical(),
                        }
                    )
                )
                if len(queue) == index:
                    queue.append(entry)
                elif len(queue) == index + 1 and queue[index] == entry:
                    pass
                else:
                    raise DeliveryIdentityMismatch(
                        "Singleton fallback child queue entry changed or has a gap"
                    )
                state.pop("singleton_materialization_intent", None)
                index += 1
                state["singleton_materialization_index"] = index
                record = self.journal.compare_and_swap_action(
                    parent_action.stable_action_id,
                    expected_version=record.version,
                    expected_phase=record.phase,
                    next_record=replace(
                        record,
                        state_json=self._encode_state(state),
                        version=record.version + 1,
                    ),
                )
                state = self._decode_state(record)
                queue = state["singleton_queue"]
                continue

            if index >= len(parent_members):
                if not queue or len(queue) != len(parent_members):
                    raise DeliveryIdentityMismatch(
                        "Singleton fallback cannot complete with an empty or partial queue"
                    )
                if complete:
                    return record
                state["singleton_materialization_complete"] = True
                state["next_singleton_index"] = 0
                state["delivery_proofs"] = []
                return self.journal.compare_and_swap_action(
                    parent_action.stable_action_id,
                    expected_version=record.version,
                    expected_phase=record.phase,
                    next_record=replace(
                        record,
                        reason="SingletonFallbackReady",
                        state_json=self._encode_state(state),
                        version=record.version + 1,
                    ),
                )

            member = parent_members[index]
            child_batch_id = digest_value(
                {
                    "kind": "singleton-batch.v1",
                    "parent_batch_id": parent_action.batch_id,
                    "ticket_key": member.ticket_key,
                    "candidate_receipt_digest": member.digest,
                }
            )
            current_target = self.git.read_target(request.target, allow_advance=True)
            state["singleton_materialization_intent"] = {
                "index": index,
                "member": member.canonical(),
                "target": current_target.canonical(),
                "child_batch_id": child_batch_id,
                "child_action_id": _singleton_action_id(parent_action, member),
            }
            record = self.journal.compare_and_swap_action(
                parent_action.stable_action_id,
                expected_version=record.version,
                expected_phase=record.phase,
                next_record=replace(
                    record,
                    state_json=self._encode_state(state),
                    version=record.version + 1,
                ),
            )
            state = self._decode_state(record)
            queue = state["singleton_queue"]

    def _execute_child(
        self,
        child_action: BatchDeliveryAction,
        child_request: BatchDeliveryRequest,
    ) -> BatchDeliveryObservation:
        if child_request.request_digest != child_action.request_digest:
            raise DeliveryIdentityMismatch(
                "Singleton child action does not bind its actual request"
            )
        record = self.journal.read_action(child_action.stable_action_id)
        if record is None:
            raise BatchIntegratorError(
                "BATCH_ACTION_MISSING",
                "Singleton action disappeared before execution",
            )
        self._validate_action_record(record, child_action)
        while record.phase not in {"complete", "decision", "blocked"}:
            next_record = self._advance_one_delivery_step(
                record, child_action, child_request
            )
            record = self.journal.compare_and_swap_action(
                child_action.stable_action_id,
                expected_version=record.version,
                expected_phase=record.phase,
                next_record=next_record,
            )
            if record.phase == "wait":
                break
        return self._observation_from_record(record)

    def _advance_singleton_queue(
        self,
        record: object,
        parent_action: BatchDeliveryAction,
        request: BatchDeliveryRequest,
        state: dict[str, object],
    ) -> object:
        queue = state.get("singleton_queue")
        if not isinstance(queue, list):
            raise DeliveryIdentityMismatch("Singleton fallback queue is malformed")
        if state.get("singleton_materialization_complete") is not True:
            raise DeliveryIdentityMismatch(
                "Singleton fallback queue was used before materialization completed"
            )
        index = state.get("next_singleton_index", 0)
        if type(index) is not int or index < 0:
            raise DeliveryIdentityMismatch("Singleton fallback queue index is malformed")
        raw_members = state.get("members")
        if not isinstance(raw_members, list):
            raise DeliveryIdentityMismatch("parent Singleton member partition is missing")
        if not queue or len(queue) != len(raw_members):
            raise DeliveryIdentityMismatch(
                "Singleton fallback queue does not exactly cover the parent members"
            )
        resume_indices = state.get("resume_indices", [])
        if not isinstance(resume_indices, list) or any(
            type(item) is not int or item < 0 for item in resume_indices
        ):
            raise DeliveryIdentityMismatch("Singleton resume partition is malformed")
        for resume_index in resume_indices:
            if resume_index >= len(raw_members):
                raise DeliveryIdentityMismatch("Singleton resume member index is invalid")
            if resume_index != index and isinstance(raw_members[resume_index], dict):
                raw_members[resume_index]["status"] = "preserved"
        if index >= len(queue):
            if not all(
                isinstance(item, dict) and item.get("status") == "integrated"
                for item in raw_members
            ):
                return replace(
                    record,
                    phase="decision",
                    reason="WorkerResumeRequired",
                    state_json=self._encode_state(state),
                    version=record.version + 1,
                )
            return replace(
                record,
                phase="complete",
                reason="SingletonFallbackComplete",
                state_json=self._encode_state(state),
                version=record.version + 1,
            )
        entry = queue[index]
        if not isinstance(entry, dict):
            raise DeliveryIdentityMismatch("Singleton fallback queue entry is malformed")
        try:
            child_action = BatchDeliveryAction(**entry["action"])
            member = self._receipt_from_canonical(entry["member"])
        except (KeyError, TypeError, ValueError) as error:
            raise DeliveryIdentityMismatch(
                "Singleton fallback queue entry is not canonical"
            ) from error
        if child_action.member_ticket_keys != (member.ticket_key,):
            raise DeliveryIdentityMismatch("Singleton child member identity changed")
        child_target = request.target
        target_state = entry.get("target")
        if isinstance(target_state, dict):
            try:
                child_target = BatchTarget(**target_state)
            except (TypeError, ValueError) as error:
                raise DeliveryIdentityMismatch(
                    "Singleton child target identity changed"
                ) from error
        child_request = replace(
            request,
            stable_action_id=child_action.stable_action_id,
            target=child_target,
            accepted_candidates=(member,),
        )
        self._requests[child_action.stable_action_id] = child_request
        child_observation = self._execute_child(child_action, child_request)
        if child_observation.phase == "complete":
            if (
                child_observation.stable_action_id != child_action.stable_action_id
                or child_observation.batch_id != child_action.batch_id
                or child_observation.batch_sha != child_action.batch_sha
                or len(child_observation.delivery_proofs) != 1
                or child_observation.delivery_proofs[0].member_ticket_keys
                != (member.ticket_key,)
            ):
                raise DeliveryIdentityMismatch(
                    "completed Singleton observation changed child delivery identity"
                )
            if index >= len(raw_members):
                raise DeliveryIdentityMismatch("parent Singleton member partition changed")
            parent_member = raw_members[index]
            if not isinstance(parent_member, dict):
                raise DeliveryIdentityMismatch("parent Singleton member is malformed")
            parent_member["status"] = "integrated"
            parent_member.pop("resume_reason", None)
            state.setdefault("delivery_proofs", []).append(
                child_observation.delivery_proofs[0].canonical()
            )
            state["next_singleton_index"] = index + 1
            all_integrated = all(
                isinstance(item, dict) and item.get("status") == "integrated"
                for item in raw_members
            )
            phase = (
                "complete"
                if index + 1 == len(queue) and all_integrated
                else "decision"
                if index + 1 == len(queue)
                else "wait"
            )
            reason = (
                "SingletonFallbackComplete"
                if phase == "complete"
                else "SingletonCompleted"
            )
            return replace(
                record,
                phase=phase,
                reason=reason,
                state_json=self._encode_state(state),
                version=record.version + 1,
            )
        if child_observation.phase == "blocked":
            return replace(
                record,
                phase="blocked",
                reason="SingletonBlocked",
                state_json=self._encode_state(state),
                version=record.version + 1,
            )
        if (
            child_observation.reason == "WorkerResumeRequired"
            or child_observation.phase == "decision"
        ):
            if index >= len(raw_members):
                raise DeliveryIdentityMismatch("parent Singleton member partition changed")
            parent_member = raw_members[index]
            if not isinstance(parent_member, dict):
                raise DeliveryIdentityMismatch("parent Singleton member is malformed")
            parent_member["status"] = "resume_required"
            parent_member["resume_reason"] = child_observation.reason
            if index not in resume_indices:
                resume_indices.append(index)
            state["resume_indices"] = resume_indices
            # An unresolved child is handed back to the owning Work Run, but
            # unaffected children can still be delivered.  Advance the queue
            # without fabricating a proof for the failed member.
            state["next_singleton_index"] = index + 1
            phase = "decision" if index + 1 == len(queue) else "wait"
        return replace(
            record,
            phase=(
                phase
                if child_observation.reason == "WorkerResumeRequired"
                else "wait"
            ),
            reason=(
                "WorkerResumeRequired"
                if child_observation.reason == "WorkerResumeRequired"
                else "SingletonWaiting"
            ),
            state_json=self._encode_state(state),
            version=record.version + 1,
        )

    def _advance_one_delivery_step(
        self,
        record: object,
        action: BatchDeliveryAction,
        request: BatchDeliveryRequest,
    ) -> object:
        from ._batch_integrator_drivers import HostedResultObservation
        from ._batch_integrator_store import HostedResultReceipt

        state = self._decode_state(record)
        if record.fallback_generation == 1 and (
            isinstance(state.get("singleton_queue"), list)
            or state.get("singleton_materialization_complete") is False
        ):
            if state.get("singleton_materialization_complete") is not True:
                return self._materialize_singleton_queue(
                    record, action, request
                )
            return self._advance_singleton_queue(
                record, action, request, state
            )
        if record.phase == "wait" and state.get("resume_required") is True:
            return self._next_record(
                record,
                phase="decision",
                reason="WorkerResumeRequired",
                state=state,
            )
        if record.phase == "wait" and isinstance(state.get("retry_intent"), dict):
            return self._perform_retry_intent(record, action, request, state)
        if record.phase == "wait" and isinstance(state.get("hosted_pending"), dict):
            pending = state["hosted_pending"]
            expected_pending = {
                "stable_action_id": action.stable_action_id,
                "batch_sha": action.batch_sha,
                "suite_id": request.hosted_suites[0].suite_id,
                "provider_check_id": "check:1",
            }
            if pending != expected_pending:
                raise DeliveryIdentityMismatch(
                    "pending hosted check identity changed before reread"
                )
            state.pop("hosted_pending", None)
            return self._next_record(
                record,
                phase="published",
                reason="HostedResultCheckReady",
                state=state,
            )
        if record.phase == "wait" and isinstance(state.get("next_check"), dict):
            next_check = state["next_check"]
            expected_check = {
                "stable_action_id": action.stable_action_id,
                "batch_sha": action.batch_sha,
                "suite_id": request.hosted_suites[0].suite_id,
                "provider_check_id": "check:1",
            }
            if not isinstance(next_check, dict) or any(
                next_check.get(key) != value for key, value in expected_check.items()
            ):
                raise DeliveryIdentityMismatch(
                    "next hosted check identity changed before retry"
                )
            state.pop("next_check", None)
            state.pop("hosted_receipt", None)
            return self._next_record(
                record,
                phase="published",
                reason="InfrastructureRetryReady",
                state=state,
            )
        if record.phase == "prepared":
            local_receipt = self.local.run(action.batch_sha, request.local_suite)
            if (
                local_receipt.batch_sha != action.batch_sha
                or local_receipt.suite_id != request.local_suite.suite_id
                or local_receipt.definition_digest
                != request.local_suite.definition_digest
            ):
                raise DeliveryIdentityMismatch(
                    "local check receipt did not preserve Batch or suite identity"
                )
            if local_receipt.outcome != "passed":
                state["local_receipt"] = asdict(local_receipt)
                if local_receipt.outcome == "code_failure":
                    state["resume_phase"] = "prepared"
                    return self._classify_failure(
                        record,
                        action,
                        request,
                        local_receipt.outcome,
                        state,
                    )
                raise BatchIntegratorError(
                    "BATCH_LOCAL_CHECK_FAILED",
                    "exact local suite did not pass",
                )
            state["local_receipt"] = asdict(local_receipt)
            return self._next_record(
                record,
                phase="local_checked",
                reason="local check passed",
                state=state,
            )

        if record.phase == "local_checked":
            publication = self.hosted.read_publication(
                request.repository, action.batch_sha
            )
            if publication is None:
                publication = self.hosted.publish_once(
                    request.repository,
                    action.batch_sha,
                    request.plan_revision_digest,
                )
            if (
                publication.repository != request.repository
                or publication.batch_sha != action.batch_sha
            ):
                raise DeliveryIdentityMismatch(
                    "publication readback named a different Batch SHA"
                )
            state["publication"] = asdict(publication)
            return self._next_record(
                record,
                phase="published",
                reason="publication read back",
                state=state,
            )

        if record.phase == "published":
            pull_request = self.hosted.read_pull_request(
                request.repository, action.batch_sha
            )
            if (
                pull_request.repository != request.repository
                or pull_request.head_sha != action.batch_sha
                or pull_request.base_branch != request.target.target_branch
                or pull_request.merge_method != "merge"
            ):
                raise DeliveryIdentityMismatch(
                    "PR readback rewrote the reviewed Batch identity"
                )
            state["pull_request"] = asdict(pull_request)

            return self._advance_hosted_result(record, action, request, state)

        if record.phase == "hosted":
            publication = self._publication_from_state(state)
            pull_request = self._pull_request_from_state(state)
            local_receipt = self._local_receipt_from_state(state)
            hosted_receipt = HostedResultReceipt(**state["hosted_receipt"])
            lease = self.journal.acquire_integration_lease(
                request.repository,
                action.stable_action_id,
                request.writer_generation,
                request.activation_id,
            )
            try:
                target_readback = self.hosted.integrate_serially(
                    request.repository,
                    action.batch_sha,
                    request.target,
                    pull_request,
                )
                if (
                    target_readback.repository != request.repository
                    or target_readback.target_branch
                    != request.target.target_branch
                    or target_readback.batch_sha != action.batch_sha
                    or target_readback.pull_request_number != pull_request.number
                    or target_readback.pull_request_head_sha != action.batch_sha
                    or not target_readback.batch_is_ancestor
                    or target_readback.merge_method != "merge"
                    or target_readback.merge_commit_sha
                    != target_readback.target_head_sha
                ):
                    raise DeliveryIdentityMismatch(
                        "target readback did not prove exact Batch ancestry"
                    )
                delivery_proof = BatchDeliveryProof.create(
                    delivery_stable_action_id=action.stable_action_id,
                    delivery_request_digest=action.request_digest,
                    batch_id=action.batch_id,
                    batch_sha=action.batch_sha,
                    member_ticket_keys=action.member_ticket_keys,
                    local_check_receipt_digest=local_receipt.receipt_digest,
                    publication_receipt_digest=publication.receipt_digest,
                    pull_request_number=target_readback.pull_request_number,
                    pull_request_head_sha=target_readback.pull_request_head_sha,
                    hosted_result_receipt_digest=hosted_receipt.receipt_digest,
                    integration_lease_digest=lease.lease_digest,
                    target_branch=target_readback.target_branch,
                    target_head_sha=target_readback.target_head_sha,
                    target_readback_digest=target_readback.readback_digest,
                    target_contains_batch_sha=target_readback.batch_is_ancestor,
                    pull_request_merge_target_sha=target_readback.merge_commit_sha,
                    merge_method=target_readback.merge_method,
                )
            finally:
                self.journal.release_integration_lease(request.repository, lease)

            candidates_by_ticket = {
                candidate.ticket_key: candidate
                for candidate in request.accepted_candidates
            }
            try:
                selected = tuple(
                    candidates_by_ticket[ticket_key]
                    for ticket_key in action.member_ticket_keys
                )
            except KeyError as error:
                raise DeliveryIdentityMismatch(
                    "Batch action member identity is not in the request"
                ) from error
            for item in state["members"]:
                item["status"] = "integrated"
                item.pop("resume_reason", None)
            state["target_readback"] = asdict(target_readback)
            state["integration_lease"] = asdict(lease)
            state["delivery_proofs"] = [delivery_proof.canonical()]
            del selected
            return self._next_record(
                record,
                phase="complete",
                reason="integrated",
                state=state,
            )

        raise BatchIntegratorError(
            "BATCH_PHASE_INVALID",
            f"cannot advance action from phase {record.phase}",
        )

    @staticmethod
    def _local_receipt_from_state(state: dict[str, object]):
        from ._batch_integrator_drivers import LocalCheckReceipt

        try:
            return LocalCheckReceipt(**state["local_receipt"])
        except (KeyError, TypeError, ValueError) as error:
            raise DeliveryIdentityMismatch(
                "journal local check receipt is malformed"
            ) from error

    @staticmethod
    def _publication_from_state(state: dict[str, object]):
        from ._batch_integrator_drivers import BatchPublicationReceipt

        try:
            return BatchPublicationReceipt(**state["publication"])
        except (KeyError, TypeError, ValueError) as error:
            raise DeliveryIdentityMismatch(
                "journal publication receipt is malformed"
            ) from error

    @staticmethod
    def _pull_request_from_state(state: dict[str, object]):
        from ._batch_integrator_drivers import PullRequestReadback

        try:
            return PullRequestReadback(**state["pull_request"])
        except (KeyError, TypeError, ValueError) as error:
            raise DeliveryIdentityMismatch(
                "journal pull request readback is malformed"
            ) from error

    def _block_identity_failure(
        self, action: BatchDeliveryAction, error: BatchIntegratorError
    ) -> None:
        current = self.journal.read_action(action.stable_action_id)
        if current is None or current.phase in {"complete", "decision", "blocked"}:
            return
        state = self._decode_state(current)
        members = state.get("members", [])
        if isinstance(members, list):
            for member in members:
                if isinstance(member, dict):
                    member["status"] = "blocked"
                    member["resume_reason"] = error.code
        state["identity_error"] = error.code
        try:
            self.journal.compare_and_swap_action(
                action.stable_action_id,
                expected_version=current.version,
                expected_phase=current.phase,
                next_record=replace(
                    current,
                    phase="blocked",
                    reason=error.code,
                    state_json=self._encode_state(state),
                    version=current.version + 1,
                ),
            )
        except BatchIntegratorError:
            return

    @staticmethod
    def _validate_request(request: BatchDeliveryRequest) -> None:
        if request.target.repository != request.repository:
            raise BatchIntegratorError(
                "BATCH_TARGET_REPOSITORY_MISMATCH",
                "request and target repository differ",
            )
