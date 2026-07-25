"""Atomic native Store reconstruction and shared-planner shadow evaluation."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
from pathlib import Path
import re
import sqlite3
from typing import Any

from ._canonical import canonical_bytes, digest_bytes, digest_value
from .activation import (
    ActivationReceipt,
    DurablePlanControl,
    DurablePlanRecord,
    LocalPlanPublication,
)
from .evidence import EvidenceVerifier, ResultClaim, TypedEvidence
from .kernel import Kernel
from .retirement import (
    RetirementAuthorization,
    RetirementError,
    RetirementReadback,
    ValidatedReviewRetirements,
    completed_retirement,
    validate_review_retirement_records,
)
from .runtime import (
    ReviewAxisBinding,
    ReviewAxisObservation,
    RuntimeBinding,
    RuntimeObservation,
    RuntimePrompt,
)


_SHA40 = re.compile(r"^[0-9a-f]{40}$")


@dataclass(frozen=True)
class ReviewChildReadback:
    recovery_ordinal: int
    binding: ReviewAxisBinding
    observed_prompt_digest: str


@dataclass(frozen=True)
class ExecutionBudgetReadback:
    attempt_ordinal: int
    repair_rounds_used: int
    materialization_create_executions: int
    materialization_prompt_executions: int
    hosted_retry_count: int
    runtime_observation_failures: int
    runtime_circuits: dict[str, dict[str, Any]]


@dataclass(frozen=True)
class AuthoritativeNodeReadback:
    """Durable facts required to restore one native Kernel node state."""

    node_key: str
    goal_key: str
    work_item_key: str
    status: str
    directive: str
    admission_id: str | None
    admission_state: str | None
    attempt_id: str | None
    attempt_state: str | None
    attempt_record_state: str | None
    attempt_terminal_reason: str | None
    budgets: ExecutionBudgetReadback
    base_sha: str | None
    prompt: RuntimePrompt | None
    runtime_binding: RuntimeBinding | None
    candidate_sha: str | None
    wait_condition: str | None
    wait_source_ref: str | None
    publication_state: str | None
    publication_ref: str | None
    hosted_check_state: str | None
    hosted_check_evidence: tuple[TypedEvidence, ...]
    worker_parked_for_ci: bool
    resume_sent: bool
    publication_eligible: bool | None
    evidence: tuple[TypedEvidence, ...]
    review_children: tuple[ReviewChildReadback, ...]
    review_observations: tuple[ReviewAxisObservation, ...]
    held_resource_claims: tuple[str, ...]
    integrated_sha: str | None
    candidate_source_ref: str | None = None
    integration_source_ref: str | None = None
    integration_batch_id: str | None = None
    integration_batch_sha: str | None = None
    integration_evidence: TypedEvidence | None = None
    retirement: dict[str, Any] | None = None
    retirement_state: str | None = None
    last_retirement_error: dict[str, str] | None = None
    review_retirements: dict[str, dict[str, Any]] = field(
        default_factory=dict
    )
    review_children_retired: bool = False

    def result_claim(self) -> ResultClaim | None:
        if self.attempt_id is None or self.candidate_sha is None:
            return None
        return ResultClaim(
            attempt_id=self.attempt_id,
            node_key=self.node_key,
            candidate_sha=self.candidate_sha,
        )

    def state(
        self,
        *,
        repository: str,
        plan_digest: str,
        activation_id: str,
        contract_digest: str,
        result_digest: str | None,
        review_retirement_validation: ValidatedReviewRetirements,
    ) -> dict[str, Any]:
        claim = self.result_claim()
        review_evidence = next(
            (item for item in self.evidence if item.kind == "review"),
            None,
        )
        bindings = {
            f"{item.binding.axis}:{item.recovery_ordinal}": asdict(item.binding)
            for item in self.review_children
        }
        observations = {
            f"{item.axis}:{item.recovery_ordinal}": asdict(item)
            for item in self.review_observations
        }
        candidate_observation = None
        if claim is not None and self.runtime_binding is not None:
            candidate_observation = {
                "lifecycle": "completed",
                "binding": asdict(self.runtime_binding),
                "result_claim": asdict(claim),
                "evidence": [asdict(item) for item in self.evidence],
            }
        return {
            "status": self.status,
            "directive": self.directive,
            "repository": repository,
            "plan_digest": plan_digest,
            "activation_id": activation_id,
            "goal_key": self.goal_key,
            "goal_state": (
                "completed" if self.status == "complete" else "active"
            ),
            "work_item_key": self.work_item_key,
            "work_item_state": (
                "integrated"
                if self.integrated_sha is not None
                else "active"
            ),
            "node_key": self.node_key,
            "contract_digest": contract_digest,
            "admission_id": self.admission_id,
            "admission_state": self.admission_state,
            "attempt_id": self.attempt_id,
            "attempt_state": self.attempt_state,
            "attempt_ordinal": self.budgets.attempt_ordinal,
            "repair_rounds_used": self.budgets.repair_rounds_used,
            "attempt_terminal_reason": self.attempt_terminal_reason,
            "candidate_sha": self.candidate_sha,
            "candidate_source_ref": self.candidate_source_ref,
            "candidate_observation": candidate_observation,
            "result_digest": result_digest,
            "publication_eligible": self.publication_eligible,
            "publication_state": self.publication_state,
            "publication_ref": self.publication_ref,
            "hosted_check_state": self.hosted_check_state,
            "hosted_check_evidence": [
                asdict(item) for item in self.hosted_check_evidence
            ],
            "hosted_retry_count": self.budgets.hosted_retry_count,
            "worker_parked_for_ci": self.worker_parked_for_ci,
            "materialization_executions": (
                self.budgets.materialization_create_executions
                + self.budgets.materialization_prompt_executions
            ),
            "materialization_actions": {
                "create": self.budgets.materialization_create_executions,
                "prompt": self.budgets.materialization_prompt_executions,
            },
            "runtime_circuit": None,
            "runtime_circuit_state": None,
            "runtime_circuits": self.budgets.runtime_circuits,
            "runtime_observation_failures": (
                self.budgets.runtime_observation_failures
            ),
            "wait_condition": self.wait_condition,
            "wait_source_ref": self.wait_source_ref,
            "wait_event_identity": (
                None
                if self.wait_condition is None
                else f"{self.wait_condition}:{self.node_key}"
            ),
            "next_check_at": None,
            "base_sha": self.base_sha,
            "prompt_snapshot": (
                None if self.prompt is None else asdict(self.prompt)
            ),
            "resume_sent": self.resume_sent,
            "review_candidate_sha": self.candidate_sha,
            "review_bindings": bindings,
            "review_observations": observations,
            "review_evidence": (
                None if review_evidence is None else asdict(review_evidence)
            ),
            "review_children_retired": (
                review_retirement_validation.children_retired
            ),
            "review_retirements": review_retirement_validation.records,
            "integrated_sha": self.integrated_sha,
            "integration_source_ref": self.integration_source_ref,
            "integration_batch_id": self.integration_batch_id,
            "integration_batch_sha": self.integration_batch_sha,
            "integration_batch_hosted_check_evidence": [
                asdict(item) for item in self.hosted_check_evidence
            ],
            "integration_evidence": (
                None
                if self.integration_evidence is None
                else asdict(self.integration_evidence)
            ),
            "integration_evidence_digest": (
                None
                if self.integration_evidence is None
                else self.integration_evidence.content_digest
            ),
            "retirement": self.retirement,
            "retirement_state": self.retirement_state,
            "last_retirement_error": self.last_retirement_error,
        }


@dataclass(frozen=True)
class AuthoritativeRepositoryReadback:
    repository: str
    receipt: ActivationReceipt
    plan_record: DurablePlanRecord
    nodes: tuple[AuthoritativeNodeReadback, ...] = ()
    legacy_identities: tuple[str, ...] = ()

    @classmethod
    def from_durable(
        cls,
        durable: DurablePlanControl,
        repository: str,
        *,
        nodes: tuple[AuthoritativeNodeReadback, ...] = (),
        legacy_identities: tuple[str, ...] = (),
    ) -> AuthoritativeRepositoryReadback:
        receipt = durable.read_current_activation(repository)
        if receipt is None:
            raise ValueError("repository has no durable current Activation Receipt")
        record = durable.read_plan(repository, receipt.plan_digest)
        if record is None:
            raise ValueError("durable Activation Receipt has no Plan record")
        return cls(
            repository=repository,
            receipt=receipt,
            plan_record=record,
            nodes=nodes,
            legacy_identities=legacy_identities,
        )


@dataclass(frozen=True)
class ReconstructionResult:
    status: str
    repository: str
    store_path: str
    active_plan_digest: str | None
    writer_generation: str | None
    node_count: int
    admission_count: int
    attempt_count: int
    runtime_count: int
    review_child_count: int
    axis_observation_count: int
    check_evidence_count: int
    review_evidence_count: int
    publication_eligible_count: int
    integration_fact_count: int
    reviewer_lifecycle_count: int
    legacy_identity_count: int
    blockers: tuple[str, ...] = ()


@dataclass(frozen=True)
class ProposedShadowAction:
    kind: str
    node_key: str | None
    source_ref: str


@dataclass(frozen=True)
class ShadowDecision:
    repository: str
    plan_digest: str
    proposed_actions: tuple[ProposedShadowAction, ...]
    audit_record: bytes
    audit_digest: str


def _connect(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    return connection


class StoreReconstructor:
    """Validate all facts, then activate and populate one native Store transaction."""

    def __init__(self, verifier: EvidenceVerifier | None = None):
        self.verifier = verifier or EvidenceVerifier()

    @staticmethod
    def _prepare_schema(path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with _connect(path) as connection:
            Kernel.ensure_store_schema(connection)
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS v8_reconstruction_audit (
                    repository TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    source_digest TEXT NOT NULL,
                    blockers_json TEXT NOT NULL
                )
                """
            )

    def _validated(
        self,
        readback: AuthoritativeRepositoryReadback,
    ) -> tuple[
        tuple[str, ...],
        dict[str, dict[str, Any]],
        dict[str, str | None],
        dict[str, ValidatedReviewRetirements],
    ]:
        blockers: set[str] = set()
        if (
            readback.repository != readback.receipt.repository
            or readback.repository != readback.plan_record.repository
            or readback.receipt.plan_digest != readback.plan_record.plan_digest
            or readback.receipt.plan_record_ref != readback.plan_record.record_ref
            or digest_bytes(readback.plan_record.canonical_bytes)
            != readback.plan_record.plan_digest
        ):
            blockers.add("REPOSITORY_IDENTITY_MISMATCH")
        if readback.legacy_identities:
            blockers.add("LEGACY_IDENTITY_PRESENT")
        try:
            plan = json.loads(readback.plan_record.canonical_bytes)
        except (json.JSONDecodeError, UnicodeDecodeError):
            plan = {}
            blockers.add("PLAN_RECORD_INVALID")
        work_nodes = {
            node["node_key"]: node
            for node in plan.get("nodes") or ()
            if isinstance(node, dict)
            and node.get("kind") == "work"
            and isinstance(node.get("node_key"), str)
        }
        node_keys: set[str] = set()
        admissions: set[str] = set()
        attempts: set[str] = set()
        runtimes: set[str] = set()
        results: dict[str, str | None] = {}
        review_retirement_validations: dict[
            str, ValidatedReviewRetirements
        ] = {}
        allowed_statuses = {
            "running",
            "waiting",
            "blocked",
            "rejected",
            "verified",
            "complete",
            "failed",
            "superseded",
        }
        for node in readback.nodes:
            plan_node = work_nodes.get(node.node_key)
            if node.node_key in node_keys or plan_node is None:
                blockers.add("NODE_IDENTITY_CONTRADICTION")
            node_keys.add(node.node_key)
            if plan_node is None:
                continue
            if (
                plan_node.get("goal_key") != node.goal_key
                or plan_node.get("work_item_key") != node.work_item_key
            ):
                blockers.add("NODE_RELATION_CONTRADICTION")
            recovery_policy = plan_node.get("recovery_policy") or {}
            budget_values = (
                node.budgets.attempt_ordinal,
                node.budgets.repair_rounds_used,
                node.budgets.materialization_create_executions,
                node.budgets.materialization_prompt_executions,
                node.budgets.hosted_retry_count,
                node.budgets.runtime_observation_failures,
            )
            if (
                any(
                    not isinstance(value, int)
                    or isinstance(value, bool)
                    or value < 0
                    for value in budget_values
                )
                or node.budgets.attempt_ordinal < 1
                or node.budgets.attempt_ordinal
                > int(recovery_policy.get("semantic_attempts", 0))
                or node.budgets.repair_rounds_used
                > int(recovery_policy.get("repair_rounds", 0))
                or node.budgets.materialization_create_executions > 3
                or node.budgets.materialization_prompt_executions > 3
                or node.budgets.hosted_retry_count > 2
                or node.budgets.runtime_observation_failures > 3
                or not isinstance(node.budgets.runtime_circuits, dict)
            ):
                blockers.add("EXECUTION_BUDGET_CONTRADICTION")
            if (
                node.status not in allowed_statuses
                or (node.admission_id is None) != (node.admission_state is None)
                or (node.attempt_id is None) != (node.attempt_state is None)
                or (node.attempt_id is None)
                != (node.attempt_record_state is None)
                or (
                    node.admission_state == "consumed"
                    and node.attempt_id is None
                )
                or (
                    node.status == "waiting"
                    and not node.wait_condition
                )
                or (
                    node.status not in {"waiting", "blocked", "rejected"}
                    and node.wait_condition is not None
                )
            ):
                blockers.add("LIFECYCLE_RELATION_CONTRADICTION")
            terminal_reasons = {
                "rejected",
                "no_result",
                "runtime_lost",
                "superseded",
            }
            if (
                not isinstance(node.worker_parked_for_ci, bool)
                or not isinstance(node.resume_sent, bool)
                or (
                    node.attempt_record_state is not None
                    and node.attempt_record_state
                    not in {
                        "running",
                        "verified",
                        "terminal",
                    }
                )
                or (
                    node.attempt_terminal_reason is not None
                    and node.attempt_terminal_reason not in terminal_reasons
                )
                or (
                    node.attempt_state
                    in {"terminal", "superseded", "runtime_unavailable"}
                    and node.attempt_terminal_reason is None
                )
                or (
                    node.attempt_state
                    not in {"terminal", "superseded", "runtime_unavailable"}
                    and node.attempt_terminal_reason is not None
                )
                or (
                    node.attempt_state == "superseded"
                    and (
                        node.attempt_terminal_reason != "superseded"
                        or node.attempt_record_state != "terminal"
                    )
                )
                or (
                    node.attempt_state == "runtime_unavailable"
                    and (
                        node.attempt_terminal_reason != "runtime_lost"
                        or node.attempt_record_state != "running"
                    )
                )
                or (
                    node.attempt_state == "terminal"
                    and node.attempt_record_state != "terminal"
                )
                or (
                    node.attempt_state == "verified"
                    and node.attempt_record_state != "verified"
                )
                or (
                    node.resume_sent
                    and (
                        node.runtime_binding is None
                        or node.attempt_id is None
                    )
                )
            ):
                blockers.add("ATTEMPT_PROGRESS_CONTRADICTION")
            if (
                node.publication_state
                not in {None, "blocked", "published"}
                or node.hosted_check_state
                not in {
                    None,
                    "pending",
                    "passed",
                    "code_failure",
                    "infrastructure_failure",
                    "unavailable",
                }
                or (
                    node.publication_state == "published"
                    and not node.publication_ref
                )
                or (
                    node.publication_state != "published"
                    and node.publication_ref is not None
                )
                or (
                    node.hosted_check_state is not None
                    and node.publication_state != "published"
                )
                or (
                    node.worker_parked_for_ci
                    and node.publication_state != "published"
                )
            ):
                blockers.add("DELIVERY_PROGRESS_CONTRADICTION")
            if node.admission_id is not None:
                if node.admission_id in admissions:
                    blockers.add("DUPLICATE_ADMISSION_IDENTITY")
                admissions.add(node.admission_id)
            if node.attempt_id is not None:
                if node.admission_id is None:
                    blockers.add("ATTEMPT_ADMISSION_MISSING")
                if node.attempt_id in attempts:
                    blockers.add("DUPLICATE_ATTEMPT_IDENTITY")
                attempts.add(node.attempt_id)
            if node.held_resource_claims and node.admission_id is None:
                blockers.add("RESOURCE_CLAIM_OWNER_MISSING")
            plan_claims = set(plan_node.get("resource_claims") or ())
            if not set(node.held_resource_claims).issubset(plan_claims):
                blockers.add("RESOURCE_CLAIM_CONTRADICTION")
            if node.candidate_sha is not None and (
                node.attempt_id is None
                or node.runtime_binding is None
                or node.prompt is None
                or _SHA40.fullmatch(node.candidate_sha) is None
                or _SHA40.fullmatch(str(node.base_sha or "")) is None
                or not node.candidate_source_ref
            ):
                blockers.add("CANDIDATE_IDENTITY_INVALID")
            hosted_definitions = tuple(
                check
                for check in (
                    (plan_node.get("output_contract") or {}).get("checks")
                    or ()
                )
                if isinstance(check, dict)
                and check.get("hosted_only") is True
            )
            batch_identity_present = (
                node.integration_batch_id is not None
                or node.integration_batch_sha is not None
            )
            if batch_identity_present and (
                not node.integration_batch_id
                or _SHA40.fullmatch(str(node.integration_batch_sha or ""))
                is None
            ):
                blockers.add("INTEGRATION_BATCH_IDENTITY_INVALID")
            integration_subject = (
                node.integration_batch_sha
                if node.integration_batch_sha is not None
                else node.candidate_sha
            )
            if node.hosted_check_evidence:
                hosted_findings = self.verifier.verify_hosted_checks(
                    str(integration_subject or ""),
                    hosted_definitions,
                    node.hosted_check_evidence,
                )
                if hosted_findings:
                    blockers.add("HOSTED_EVIDENCE_INVALID")
            if (
                node.hosted_check_state == "passed"
                and (
                    node.candidate_sha is None
                    or not hosted_definitions
                    or not node.hosted_check_evidence
                )
            ):
                blockers.add("HOSTED_EVIDENCE_MISSING")
            binding = node.runtime_binding
            if binding is not None:
                expected = (
                    binding.repository == readback.repository
                    and binding.plan_digest == readback.receipt.plan_digest
                    and binding.node_key == node.node_key
                    and binding.admission_id == node.admission_id
                    and binding.attempt_id == node.attempt_id
                    and binding.base_sha == node.base_sha
                    and node.prompt is not None
                    and binding.prompt_accepted
                    and binding.prompt_digest == node.prompt.digest
                )
                if not expected or binding.runtime_id in runtimes:
                    blockers.add("RUNTIME_IDENTITY_CONTRADICTION")
                runtimes.add(binding.runtime_id)
            claim = node.result_claim()
            result_digest = None
            if claim is not None and binding is not None:
                observation = RuntimeObservation(
                    binding=binding,
                    lifecycle="completed",
                    result_claim=claim,
                    evidence=node.evidence,
                )
                decision = self.verifier.verify(
                    claim,
                    plan_node.get("output_contract") or {},
                    observation,
                )
                eligibility = self.verifier.publication_eligibility(
                    claim,
                    plan_node.get("output_contract") or {},
                    observation,
                )
                if (
                    decision.findings
                    or (node.publication_eligible is True and not eligibility.eligible)
                    or (
                        node.publication_eligible is not None
                        and node.publication_eligible != eligibility.eligible
                    )
                ):
                    blockers.add("EVIDENCE_OR_ELIGIBILITY_INVALID")
                if decision.result is not None:
                    result_digest = decision.result.result_digest
            elif node.evidence or node.publication_eligible is True:
                blockers.add("EVIDENCE_OWNER_MISSING")
            results[node.node_key] = result_digest
            binding_keys = {
                (
                    item.binding.axis,
                    item.recovery_ordinal,
                    item.binding.action_key,
                )
                for item in node.review_children
            }
            observation_keys = {
                (item.axis, item.recovery_ordinal, item.action_key)
                for item in node.review_observations
            }
            if (
                len(binding_keys) != len(node.review_children)
                or not observation_keys.issubset(binding_keys)
                or any(
                    item.recovery_ordinal < 0
                    or not item.binding.action_key
                    or not item.binding.provider
                    or not item.binding.agent_id
                    or not item.binding.session_id
                    or not item.binding.prompt_digest
                    or item.observed_prompt_digest
                    != item.binding.prompt_digest
                    or item.binding.candidate_sha != node.candidate_sha
                    or item.recovery_ordinal not in {0, 1}
                    for item in node.review_children
                )
                or any(
                    item.candidate_sha != node.candidate_sha
                    or item.attempt_id != node.attempt_id
                    for item in node.review_observations
                )
            ):
                blockers.add("REVIEW_READBACK_CONTRADICTION")
            children_by_key = {
                (
                    item.binding.axis,
                    item.recovery_ordinal,
                    item.binding.action_key,
                ): item.binding
                for item in node.review_children
            }
            if any(
                (
                    children_by_key[
                        (item.axis, item.recovery_ordinal, item.action_key)
                    ].runtime_id
                    != item.runtime_id
                    or children_by_key[
                        (item.axis, item.recovery_ordinal, item.action_key)
                    ].agent_id
                    != item.agent_id
                    or children_by_key[
                        (item.axis, item.recovery_ordinal, item.action_key)
                    ].session_id
                    != item.session_id
                    or children_by_key[
                        (item.axis, item.recovery_ordinal, item.action_key)
                    ].profile_digest
                    != item.profile_digest
                    or children_by_key[
                        (item.axis, item.recovery_ordinal, item.action_key)
                    ].provider
                    != item.provider
                    or children_by_key[
                        (item.axis, item.recovery_ordinal, item.action_key)
                    ].model
                    != item.model
                    or children_by_key[
                        (item.axis, item.recovery_ordinal, item.action_key)
                    ].thinking
                    != item.thinking
                    or children_by_key[
                        (item.axis, item.recovery_ordinal, item.action_key)
                    ].mode
                    != item.mode
                    or children_by_key[
                        (item.axis, item.recovery_ordinal, item.action_key)
                    ].fixed_input_digest
                    != item.fixed_input_digest
                )
                for item in node.review_observations
                if (item.axis, item.recovery_ordinal, item.action_key)
                in children_by_key
            ):
                blockers.add("REVIEW_PROVIDER_IDENTITY_CONTRADICTION")
            requirement = (
                plan_node.get("output_contract") or {}
            ).get("review_requirement") or {"mode": "none", "axes": []}
            review_evidence = next(
                (item for item in node.evidence if item.kind == "review"),
                None,
            )
            if requirement.get("mode") == "none":
                if (
                    node.review_children
                    or node.review_observations
                    or review_evidence is not None
                ):
                    blockers.add("REVIEW_READBACK_CONTRADICTION")
            elif claim is not None and binding is not None:
                review_completed = (
                    node.publication_eligible is True
                    or node.status in {"verified", "complete"}
                    or review_evidence is not None
                )
                if review_completed and review_evidence is None:
                    blockers.add("REVIEW_EVIDENCE_MISSING")
                elif review_evidence is not None:
                    effective_observations: dict[
                        str, ReviewAxisObservation
                    ] = {}
                    for observation in node.review_observations:
                        current_observation = effective_observations.get(
                            observation.axis
                        )
                        if (
                            current_observation is None
                            or observation.recovery_ordinal
                            > current_observation.recovery_ordinal
                        ):
                            effective_observations[
                                observation.axis
                            ] = observation
                    gate = self.verifier.assemble_review_evidence(
                        claim,
                        requirement,
                        tuple(effective_observations.values()),
                        acceptance_digest=str(
                            review_evidence.payload.get("acceptance_digest")
                            or ""
                        ),
                        check_manifest_digest=str(
                            review_evidence.payload.get(
                                "check_manifest_digest"
                            )
                            or ""
                        ),
                        observer_id=binding.runtime_id,
                    )
                    if (
                        gate.status != "accepted"
                        or gate.evidence is None
                        or gate.evidence.payload != review_evidence.payload
                    ):
                        blockers.add("REVIEW_EVIDENCE_INVALID")
            if binding is not None:
                try:
                    review_retirement_validation = (
                        validate_review_retirement_records(
                            records=node.review_retirements,
                            worker_binding=binding,
                            review_bindings={
                                (
                                    f"{item.binding.axis}:"
                                    f"{item.recovery_ordinal}"
                                ): item.binding
                                for item in node.review_children
                            },
                            review_evidence=review_evidence,
                        )
                    )
                except RetirementError:
                    blockers.add(
                        "REVIEW_RETIREMENT_READBACK_CONTRADICTION"
                    )
                else:
                    review_retirement_validations[node.node_key] = (
                        review_retirement_validation
                    )
                    if (
                        node.review_children_retired
                        and not review_retirement_validation.children_retired
                    ):
                        blockers.add(
                            "REVIEW_RETIREMENT_READBACK_CONTRADICTION"
                        )
            elif node.review_retirements or node.review_children_retired:
                blockers.add("REVIEW_RETIREMENT_READBACK_CONTRADICTION")
            retirement = node.retirement
            if retirement is not None:
                retirement_record_valid = (
                    isinstance(retirement, dict)
                    and retirement.get("state")
                    in {"pending", "error", "complete"}
                    and node.retirement_state == retirement.get("state")
                )
                authorization = None
                authorization_value = (
                    retirement.get("authorization")
                    if isinstance(retirement, dict)
                    else None
                )
                if isinstance(authorization_value, dict):
                    try:
                        authorization = RetirementAuthorization(
                            **authorization_value
                        )
                        authorization.assert_valid_digest()
                    except (RetirementError, TypeError):
                        retirement_record_valid = False
                    else:
                        retirement_record_valid = retirement_record_valid and (
                            authorization.repository == readback.repository
                            and authorization.plan_digest
                            == readback.receipt.plan_digest
                            and authorization.node_key == node.node_key
                            and authorization.admission_id == node.admission_id
                            and authorization.attempt_id == node.attempt_id
                            and authorization.candidate_sha
                            == node.candidate_sha
                            and authorization.integrated_sha
                            == node.integrated_sha
                        )
                elif (
                    not isinstance(retirement, dict)
                    or retirement.get("state") != "error"
                ):
                    retirement_record_valid = False
                if (
                    retirement_record_valid
                    and retirement.get("state") == "complete"
                    and authorization is not None
                ):
                    evidence_value = retirement.get("evidence")
                    if not isinstance(evidence_value, dict):
                        retirement_record_valid = False
                    else:
                        try:
                            completed_retirement(
                                authorization,
                                RetirementReadback(**evidence_value),
                            )
                        except (RetirementError, TypeError):
                            retirement_record_valid = False
                if not retirement_record_valid:
                    blockers.add("RETIREMENT_READBACK_CONTRADICTION")
            if node.attempt_state == "retirement_pending" and (
                retirement is None
                or node.status != "waiting"
                or node.integrated_sha is None
                or node.retirement_state not in {"pending", "error"}
            ):
                blockers.add("RETIREMENT_READBACK_CONTRADICTION")
            if node.status == "complete" and (
                node.directive != "goal_complete"
                or node.candidate_sha is None
                or result_digest is None
                or node.integrated_sha != integration_subject
                or node.attempt_state != "verified"
            ):
                blockers.add("COMPLETION_FACTS_MISSING")
            if node.status != "complete" and (
                node.directive == "goal_complete"
                or (
                    node.integrated_sha is not None
                    and node.attempt_state != "retirement_pending"
                )
            ):
                blockers.add("LIFECYCLE_RELATION_CONTRADICTION")
            if node.integrated_sha is not None and (
                (
                    node.status != "complete"
                    and node.attempt_state != "retirement_pending"
                )
                or node.integrated_sha != integration_subject
                or not node.integration_source_ref
            ):
                blockers.add("INTEGRATION_IDENTITY_INVALID")
            if node.integration_evidence is not None:
                evidence = node.integration_evidence
                expected_payload = {
                    "integration_batch_id": node.integration_batch_id,
                    "batch_sha": node.integration_batch_sha,
                    "candidate_sha": node.candidate_sha,
                }
                if (
                    node.integrated_sha is None
                    or evidence.kind != "integration"
                    or evidence.subject != node.integrated_sha
                    or evidence.source_ref != node.integration_source_ref
                    or any(
                        evidence.payload.get(key) != value
                        for key, value in expected_payload.items()
                        if value is not None
                    )
                ):
                    blockers.add("INTEGRATION_EVIDENCE_INVALID")
            elif node.integration_batch_id is not None and node.status == "complete":
                blockers.add("INTEGRATION_EVIDENCE_MISSING")
        batches: dict[str, tuple[str, str | None, set[str], set[str]]] = {}
        for node in readback.nodes:
            if node.integration_batch_id is None:
                continue
            batch_id = node.integration_batch_id
            batch_sha = str(node.integration_batch_sha or "")
            current = batches.get(batch_id)
            if current is None:
                batches[batch_id] = (
                    batch_sha,
                    node.integration_source_ref,
                    {node.node_key},
                    {str(node.candidate_sha or "")},
                )
                continue
            expected_sha, expected_source, members, candidates = current
            if (
                expected_sha != batch_sha
                or expected_source != node.integration_source_ref
            ):
                blockers.add("INTEGRATION_BATCH_RELATION_INVALID")
            members.add(node.node_key)
            candidates.add(str(node.candidate_sha or ""))
        for batch_id, (batch_sha, _source, members, candidates) in batches.items():
            batch_nodes = [
                node
                for node in readback.nodes
                if node.integration_batch_id == batch_id
            ]
            if (
                len(candidates) != len(batch_nodes)
                or len({node.base_sha for node in batch_nodes}) != 1
                or len({node.publication_state for node in batch_nodes}) != 1
                or len({node.publication_ref for node in batch_nodes}) != 1
                or len({node.hosted_check_state for node in batch_nodes}) != 1
                or len({node.integrated_sha for node in batch_nodes}) != 1
            ):
                blockers.add("INTEGRATION_BATCH_RELATION_INVALID")
            for node in batch_nodes:
                evidence = node.integration_evidence
                if evidence is None:
                    continue
                payload_members = set(evidence.payload.get("member_node_keys") or ())
                if (
                    evidence.payload.get("batch_sha") != batch_sha
                    or payload_members != members
                    or evidence.payload.get("candidate_sha") not in candidates
                ):
                    blockers.add("INTEGRATION_BATCH_RELATION_INVALID")
        return (
            tuple(sorted(blockers)),
            work_nodes,
            results,
            review_retirement_validations,
        )

    def reconstruct(
        self,
        readback: AuthoritativeRepositoryReadback,
        store_path: Path,
    ) -> ReconstructionResult:
        path = Path(store_path)
        self._prepare_schema(path)
        (
            blockers,
            work_nodes,
            results,
            review_retirement_validations,
        ) = self._validated(readback)
        source_digest = digest_value(
            {
                "receipt": readback.receipt.as_dict(),
                "plan_record_ref": readback.plan_record.record_ref,
                "nodes": [asdict(node) for node in readback.nodes],
                "legacy_identities": list(readback.legacy_identities),
            }
        )
        if blockers:
            self._write_audit(path, readback.repository, source_digest, blockers)
            return self._result(readback, path, blockers)

        def populate(connection: sqlite3.Connection) -> None:
            for node in readback.nodes:
                plan_node = work_nodes[node.node_key]
                state = node.state(
                    repository=readback.repository,
                    plan_digest=readback.receipt.plan_digest,
                    activation_id=readback.receipt.activation_id,
                    contract_digest=str(plan_node["contract_digest"]),
                    result_digest=results[node.node_key],
                    review_retirement_validation=(
                        review_retirement_validations.get(
                            node.node_key,
                            ValidatedReviewRetirements(
                                records={},
                                children_retired=False,
                            ),
                        )
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO v8_node_execution_state (
                        repository, plan_digest, node_key, state_json
                    ) VALUES (?, ?, ?, ?)
                    """,
                    (
                        readback.repository,
                        readback.receipt.plan_digest,
                        node.node_key,
                        canonical_bytes(state).decode("utf-8"),
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO v8_node_states (
                        repository, plan_digest, node_key, state
                    ) VALUES (?, ?, ?, ?)
                    """,
                    (
                        readback.repository,
                        readback.receipt.plan_digest,
                        node.node_key,
                        node.status,
                    ),
                )
                if node.admission_id is not None:
                    connection.execute(
                        """
                        INSERT INTO v8_admissions (
                            admission_id, repository, plan_digest,
                            node_key, goal_key, state
                        ) VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (
                            node.admission_id,
                            readback.repository,
                            readback.receipt.plan_digest,
                            node.node_key,
                            node.goal_key,
                            node.admission_state or "unknown",
                        ),
                    )
                if node.attempt_id is not None:
                    connection.execute(
                        """
                        INSERT INTO v8_attempts (
                            attempt_id, repository, plan_digest,
                            node_key, admission_id, state
                        ) VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (
                            node.attempt_id,
                            readback.repository,
                            readback.receipt.plan_digest,
                            node.node_key,
                            node.admission_id,
                            node.attempt_record_state or "unknown",
                        ),
                    )
                for claim in node.held_resource_claims:
                    connection.execute(
                        """
                        INSERT INTO v8_resource_claims (
                            repository, resource_key, admission_id, attempt_id
                        ) VALUES (?, ?, ?, ?)
                        """,
                        (
                            readback.repository,
                            claim,
                            (
                                node.admission_id
                                if node.attempt_id is None
                                else None
                            ),
                            node.attempt_id,
                        ),
                    )
                result_digest = results[node.node_key]
                if (
                    node.candidate_sha is not None
                    and node.base_sha is not None
                    and result_digest is not None
                ):
                    evidence_json = canonical_bytes(
                        {
                            "runtime_evidence": [
                                asdict(item) for item in node.evidence
                            ],
                            "hosted_check_evidence": [
                                asdict(item)
                                for item in node.hosted_check_evidence
                            ],
                        }
                    ).decode("utf-8")
                    connection.execute(
                        """
                        INSERT INTO v8_verified_results (
                            repository, plan_digest, node_key,
                            contract_digest, candidate_sha, result_digest,
                            base_sha, evidence_manifest_digest, evidence_json
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            readback.repository,
                            readback.receipt.plan_digest,
                            node.node_key,
                            plan_node["contract_digest"],
                            node.candidate_sha,
                            result_digest,
                            node.base_sha,
                            digest_value(
                                sorted(
                                    item.content_digest
                                    for item in node.evidence
                                )
                            ),
                            evidence_json,
                        ),
                    )
            batch_groups: dict[str, list[AuthoritativeNodeReadback]] = {}
            for node in readback.nodes:
                if node.integration_batch_id is not None:
                    batch_groups.setdefault(
                        node.integration_batch_id,
                        [],
                    ).append(node)
            for batch_id, members in sorted(batch_groups.items()):
                first = members[0]
                hosted_by_digest: dict[str, dict[str, Any]] = {}
                for member in members:
                    output_contract = (
                        work_nodes[member.node_key].get("output_contract") or {}
                    )
                    for definition in output_contract.get("checks") or ():
                        if (
                            isinstance(definition, dict)
                            and definition.get("hosted_only") is True
                        ):
                            hosted_by_digest[
                                str(definition["definition_digest"])
                            ] = definition
                batch_state = {
                    "repository": readback.repository,
                    "plan_digest": readback.receipt.plan_digest,
                    "batch_id": batch_id,
                    "base_sha": first.base_sha,
                    "batch_sha": first.integration_batch_sha,
                    "member_node_keys": sorted(
                        member.node_key for member in members
                    ),
                    "candidate_shas": sorted(
                        str(member.candidate_sha) for member in members
                    ),
                    "candidate_evidence_manifest_digests": sorted(
                        digest_value(
                            sorted(
                                item.content_digest
                                for item in member.evidence
                            )
                        )
                        for member in members
                    ),
                    "hosted_definitions": [
                        hosted_by_digest[key] for key in sorted(hosted_by_digest)
                    ],
                    "state": (
                        "integrated"
                        if all(member.status == "complete" for member in members)
                        else "waiting"
                    ),
                    "publication_state": first.publication_state,
                    "publication_ref": first.publication_ref,
                    "hosted_check_state": first.hosted_check_state,
                    "hosted_retry_count": max(
                        member.budgets.hosted_retry_count for member in members
                    ),
                    "integrated_sha": first.integrated_sha,
                    "integration_source_ref": first.integration_source_ref,
                    "hosted_check_evidence": [
                        asdict(item) for item in first.hosted_check_evidence
                    ],
                }
                connection.execute(
                    """
                    INSERT INTO v8_integration_batches (
                        repository, plan_digest, batch_id, state_json
                    ) VALUES (?, ?, ?, ?)
                    """,
                    (
                        readback.repository,
                        readback.receipt.plan_digest,
                        batch_id,
                        canonical_bytes(batch_state).decode("utf-8"),
                    ),
                )
            connection.execute(
                """
                INSERT INTO v8_reconstruction_audit (
                    repository, status, source_digest, blockers_json
                ) VALUES (?, 'reconstructed', ?, '[]')
                """,
                (readback.repository, source_digest),
            )

        LocalPlanPublication(
            path,
            durable=_ReadbackPlanControl(readback),
        ).reconstruct_active_from_readback(
            readback.plan_record,
            readback.receipt,
            populate=populate,
        )
        return self._result(readback, path, ())

    def reconstruct_from_durable(
        self,
        durable: DurablePlanControl,
        repository: str,
        store_path: Path,
        *,
        nodes: tuple[AuthoritativeNodeReadback, ...] = (),
        legacy_identities: tuple[str, ...] = (),
    ) -> ReconstructionResult:
        path = Path(store_path)
        receipt = durable.read_current_activation(repository)
        if receipt is None:
            return self._missing_result(
                repository, path, "DURABLE_ACTIVATION_MISSING"
            )
        record = durable.read_plan(repository, receipt.plan_digest)
        if record is None:
            return self._missing_result(
                repository, path, "DURABLE_PLAN_RECORD_MISSING"
            )
        return self.reconstruct(
            AuthoritativeRepositoryReadback(
                repository=repository,
                receipt=receipt,
                plan_record=record,
                nodes=nodes,
                legacy_identities=legacy_identities,
            ),
            path,
        )

    def _missing_result(
        self,
        repository: str,
        path: Path,
        blocker: str,
    ) -> ReconstructionResult:
        self._prepare_schema(path)
        self._write_audit(
            path,
            repository,
            digest_value({"repository": repository, "blocker": blocker}),
            (blocker,),
        )
        return ReconstructionResult(
            status="blocked",
            repository=repository,
            store_path=str(path.resolve()),
            active_plan_digest=None,
            writer_generation=None,
            node_count=0,
            admission_count=0,
            attempt_count=0,
            runtime_count=0,
            review_child_count=0,
            axis_observation_count=0,
            check_evidence_count=0,
            review_evidence_count=0,
            publication_eligible_count=0,
            integration_fact_count=0,
            reviewer_lifecycle_count=0,
            legacy_identity_count=0,
            blockers=(blocker,),
        )

    @staticmethod
    def _write_audit(
        path: Path,
        repository: str,
        source_digest: str,
        blockers: tuple[str, ...],
    ) -> None:
        with _connect(path) as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO v8_reconstruction_audit (
                    repository, status, source_digest, blockers_json
                ) VALUES (?, 'blocked', ?, ?)
                """,
                (
                    repository,
                    source_digest,
                    json.dumps(list(blockers), separators=(",", ":")),
                ),
            )

    @staticmethod
    def _result(
        readback: AuthoritativeRepositoryReadback,
        path: Path,
        blockers: tuple[str, ...],
    ) -> ReconstructionResult:
        restored = not blockers
        return ReconstructionResult(
            status="reconstructed" if restored else "blocked",
            repository=readback.repository,
            store_path=str(path.resolve()),
            active_plan_digest=(
                readback.receipt.plan_digest if restored else None
            ),
            writer_generation=(
                readback.receipt.writer_generation if restored else None
            ),
            node_count=len(readback.nodes) if restored else 0,
            admission_count=(
                sum(node.admission_id is not None for node in readback.nodes)
                if restored
                else 0
            ),
            attempt_count=(
                sum(node.attempt_id is not None for node in readback.nodes)
                if restored
                else 0
            ),
            runtime_count=(
                sum(node.runtime_binding is not None for node in readback.nodes)
                if restored
                else 0
            ),
            review_child_count=(
                sum(len(node.review_children) for node in readback.nodes)
                if restored
                else 0
            ),
            axis_observation_count=(
                sum(len(node.review_observations) for node in readback.nodes)
                if restored
                else 0
            ),
            check_evidence_count=(
                sum(
                    item.kind == "check"
                    for node in readback.nodes
                    for item in node.evidence
                )
                if restored
                else 0
            ),
            review_evidence_count=(
                sum(
                    item.kind == "review"
                    for node in readback.nodes
                    for item in node.evidence
                )
                if restored
                else 0
            ),
            publication_eligible_count=(
                sum(node.publication_eligible is True for node in readback.nodes)
                if restored
                else 0
            ),
            integration_fact_count=(
                sum(node.integrated_sha is not None for node in readback.nodes)
                if restored
                else 0
            ),
            reviewer_lifecycle_count=0,
            legacy_identity_count=0,
            blockers=blockers,
        )


class _ReadbackPlanControl:
    def __init__(self, readback: AuthoritativeRepositoryReadback):
        self.readback = readback

    def plan_record_ref(self, repository: str, plan_digest: str) -> str:
        del repository, plan_digest
        return self.readback.plan_record.record_ref

    def publish_plan(self, record: DurablePlanRecord) -> None:
        del record
        raise AssertionError("reconstruction cannot publish a Plan")

    def read_plan(
        self,
        repository: str,
        plan_digest: str,
    ) -> DurablePlanRecord | None:
        record = self.readback.plan_record
        if record.repository == repository and record.plan_digest == plan_digest:
            return record
        return None

    def publish_activation(
        self,
        receipt: ActivationReceipt,
        *,
        expected_previous_digest: str | None,
    ) -> None:
        del receipt, expected_previous_digest
        raise AssertionError("reconstruction cannot publish an Activation")

    def read_activation(
        self,
        repository: str,
        activation_id: str,
    ) -> ActivationReceipt | None:
        receipt = self.readback.receipt
        if (
            receipt.repository == repository
            and receipt.activation_id == activation_id
        ):
            return receipt
        return None

    def read_current_activation(
        self,
        repository: str,
    ) -> ActivationReceipt | None:
        if self.readback.repository == repository:
            return self.readback.receipt
        return None


class ShadowEvaluator:
    """Ask the live Kernel planner for actions without invoking effect adapters."""

    def __init__(self, kernel: Kernel):
        self.kernel = kernel

    def evaluate_store(self, repository: str) -> ShadowDecision:
        plan = self.kernel.plan_reconciliation(repository)
        actions = tuple(
            ProposedShadowAction(
                kind=action.kind,
                node_key=action.node_key,
                source_ref=action.source_ref,
            )
            for action in plan.actions
        )
        identity = {
            "repository": repository,
            "plan_digest": plan.plan_digest,
            "actions": [asdict(action) for action in actions],
        }
        audit_record = canonical_bytes(identity)
        return ShadowDecision(
            repository=repository,
            plan_digest=plan.plan_digest,
            proposed_actions=actions,
            audit_record=audit_record,
            audit_digest=digest_bytes(audit_record),
        )
