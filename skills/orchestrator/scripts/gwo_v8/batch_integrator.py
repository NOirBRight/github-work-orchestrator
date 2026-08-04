"""V3 BatchIntegrator identity boundary and delivery action seam."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
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
        self.formation_calls += 1
        if not request.accepted_candidates:
            raise BatchIntegratorError("BATCH_EMPTY", "accepted candidate set is empty")
        batch_id = digest_value(
            {"kind": "batch-id.v1", "request_digest": request.request_digest}
        )
        return BatchDeliveryAction(
            stable_action_id=request.stable_action_id,
            request_digest=request.request_digest,
            batch_id=batch_id,
            batch_sha=request.accepted_candidates[0].candidate_sha,
            member_ticket_keys=tuple(
                item.ticket_key for item in request.accepted_candidates
            ),
        )

    def readback(
        self, action: BatchDeliveryAction
    ) -> "BatchDeliveryObservation | None":
        return None

    def execute(self, action: BatchDeliveryAction) -> "BatchDeliveryObservation":
        existing = self.readback(action)
        if existing is not None:
            return existing
        raise BatchIntegratorError(
            "BATCH_EXECUTION_NOT_READY",
            "delivery execution is installed by the later action-loop task",
        )

    @staticmethod
    def _validate_request(request: BatchDeliveryRequest) -> None:
        if request.target.repository != request.repository:
            raise BatchIntegratorError(
                "BATCH_TARGET_REPOSITORY_MISMATCH",
                "request and target repository differ",
            )
