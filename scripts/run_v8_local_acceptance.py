"""Run the deterministic V8 single-Campaign public-API acceptance.

The harness composes local Planning, Runtime, Candidate, Review, and
delivery doubles, then drives the Campaign only with ``gwo_v8.start``,
``gwo_v8.advance``, and ``gwo_v8.inspect``.  All mutable acceptance state is
created below the caller supplied temporary root.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import dataclass, replace
import gc
from pathlib import Path
import sqlite3
import sys
import tempfile
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills" / "orchestrator" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import gwo_v8  # noqa: E402
from gwo_v8._canonical import (  # noqa: E402
    canonical_bytes,
    digest_value,
    load_canonical_json,
)
from gwo_v8.batch_integrator import BatchDeliveryProof  # noqa: E402
from gwo_v8.candidate_gate import (  # noqa: E402
    AcceptedCandidateReceipt,
    CandidateDiffRecordV1,
    CandidateReceipt,
    InteractionClassification,
    InteractionKey,
)
from gwo_v8.campaign_watchdog import (  # noqa: E402
    CampaignWatchdog,
    WatchdogWake,
    WatchdogWakePage,
)
from gwo_v8.execution_kernel import (  # noqa: E402
    CampaignHandle,
    ExecutionKernelConfiguration,
    ResultIntegrityProof,
    WorkRunAction,
    WorkRunObservation,
    install_execution_kernel,
)
from gwo_v8.plan_control import (  # noqa: E402
    InMemoryPlanRepository,
    PlanControl,
    _install_start_host,
)
from gwo_v8.runtime_gateway import (  # noqa: E402
    ArtifactStore,
    PlanningPreflightReceipt,
    PlanningReceipt,
)


PUBLIC_API_SINGLE_NODE_GO = "PUBLIC_API_SINGLE_NODE_GO"
LOCAL_ROOT_CANARY_GO = "LOCAL_ROOT_CANARY_GO"
_REPOSITORY = "local/v8-acceptance"
_TICKET_KEY = "issue:1"
_TARGET_BRANCH = "main"
_ROOT_STANDARD_TICKETS = ("issue:101", "issue:102", "issue:103")
_ROOT_STRICT_TICKET = "issue:104"
_ROOT_TICKET_KEYS = (*_ROOT_STANDARD_TICKETS, _ROOT_STRICT_TICKET)
_ROOT_STANDARD_BATCH_SHA = "e" * 40
_ROOT_STRICT_BATCH_SHA = "f" * 40
_ROOT_STANDARD_TARGET_SHA = "1" * 40
_ROOT_STRICT_TARGET_SHA = "a" * 40


def _campaign_key(run_id: str, scenario: str) -> str:
    return "campaign:" + digest_value(
        {
            "kind": "gwo.v8.local-acceptance-campaign.v1",
            "run_id": run_id,
            "scenario": scenario,
        }
    )[:24]


@contextmanager
def _connection(path: Path):
    connection = sqlite3.connect(path)
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


class LocalAcceptanceFailure(RuntimeError):
    """Deterministic failure used by the failure branch of the acceptance."""


def _policy() -> dict[str, Any]:
    core = {
        "schema_version": 1,
        "ref": "policy:local-acceptance",
        "replan": {
            "successor_revision_limit": 1,
            "repeated_invalidation_limit": 1,
        },
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
    return {**core, "digest": digest_value(core)}


def _campaign_source() -> dict[str, str]:
    core = {
        "repository": _REPOSITORY,
        "input_ref": "refs/heads/main",
        "resolved_commit_oid": "a" * 40,
        "tree_oid": "b" * 40,
    }
    return {**core, "digest": digest_value(core)}


def _ticket() -> dict[str, Any]:
    repository = {
        "full_name": _REPOSITORY,
        "url": f"https://api.github.com/repos/{_REPOSITORY}",
    }
    labels = [
        {
            "id": 1,
            "node_id": "LABEL_READY",
            "url": f"https://api.github.com/repos/{_REPOSITORY}/labels/ready-for-agent",
            "name": "ready-for-agent",
            "color": "0052cc",
            "default": False,
            "description": "ready",
        }
    ]
    contract = {
        "id": 1,
        "node_id": "ISSUE_1",
        "number": 1,
        "title": "V8 local acceptance Ticket",
        "body": "Complete the deterministic local public API acceptance.",
        "state": "open",
        "state_reason": None,
        "type": None,
        "repository": repository,
        "labels": labels,
        "comments": [],
        "updated_at": "2026-08-11T00:00:00Z",
    }
    source_projection = {
        "number": 1,
        "contract": contract,
        "labels": ["ready-for-agent"],
        "source_ref": _TICKET_KEY,
        "native_blockers": [],
    }
    return {
        "key": _TICKET_KEY,
        "labels": ["ready-for-agent"],
        "source": {
            "ref": _TICKET_KEY,
            "digest": digest_value(source_projection),
        },
        "contract": contract,
        "native_blockers": [],
    }


def _root_ticket(ticket_key: str, number: int) -> dict[str, Any]:
    value = _ticket()
    contract = value["contract"]
    contract["id"] = number
    contract["node_id"] = f"ISSUE_{number}"
    contract["number"] = number
    contract["title"] = f"V8 local root canary Ticket {number}"
    contract["body"] = "Complete one deterministic local root-canary Work Run."
    contract["labels"][0]["id"] = number
    contract["labels"][0]["node_id"] = f"LABEL_READY_{number}"
    value["key"] = ticket_key
    value["source"]["ref"] = ticket_key
    value["source"]["digest"] = digest_value(
        {
            "number": number,
            "contract": contract,
            "labels": ["ready-for-agent"],
            "source_ref": ticket_key,
            "native_blockers": [],
        }
    )
    return value


def _snapshot() -> dict[str, Any]:
    return {
        "repository": _REPOSITORY,
        "target_branch": _TARGET_BRANCH,
        "campaign_source": _campaign_source(),
        "policy": _policy(),
        "tickets": [_ticket()],
    }


def _root_snapshot() -> dict[str, Any]:
    return {
        "repository": _REPOSITORY,
        "target_branch": _TARGET_BRANCH,
        "campaign_source": _campaign_source(),
        "policy": _policy(),
        "tickets": [
            _root_ticket(ticket_key, 100 + ordinal)
            for ordinal, ticket_key in enumerate(_ROOT_TICKET_KEYS, start=1)
        ],
    }


class _LocalSnapshotSource:
    def __init__(self, ticket_keys: tuple[str, ...] = (_TICKET_KEY,)) -> None:
        self.ticket_keys = tuple(ticket_keys)

    def snapshot(self, repository: str, ready_refs: tuple[str, ...]) -> dict[str, Any]:
        if repository != _REPOSITORY or ready_refs != self.ticket_keys:
            raise AssertionError("local acceptance source identity changed")
        return _snapshot() if self.ticket_keys == (_TICKET_KEY,) else _root_snapshot()


@dataclass
class _LocalPlanningGateway:
    artifacts: ArtifactStore
    calls: list[str]
    ticket_keys: tuple[str, ...] = (_TICKET_KEY,)
    exclusive_resources: dict[str, tuple[str, ...]] | None = None

    def planning_preflight(self, subject) -> PlanningPreflightReceipt:
        self.calls.append("planning_preflight")
        return PlanningPreflightReceipt(
            subject_digest=subject.digest,
            stable_action_id=subject.stable_action_id,
            receipt_digest=digest_value(
                {
                    "kind": "local-planning-preflight.v1",
                    "subject_digest": subject.digest,
                }
            ),
        )

    def progress(
        self,
        subject,
        preflight: PlanningPreflightReceipt,
    ) -> PlanningReceipt:
        self.calls.append("planning_progress")
        resources = self.exclusive_resources or {
            ticket_key: () for ticket_key in self.ticket_keys
        }
        payload = {
            "admitted_work": list(self.ticket_keys),
            "dependency_additions": [],
            "exclusive_resources": {
                ticket_key: list(resources.get(ticket_key, ()))
                for ticket_key in self.ticket_keys
            },
            "capability_requirements": {
                ticket_key: ["git", "local_check"]
                for ticket_key in self.ticket_keys
            },
            "decision_requirements": [],
        }
        output = self.artifacts.put_canonical(
            {
                "schema_version": "gwo.runtime.output.v1",
                "subject_digest": subject.digest,
                "stable_action_id": subject.stable_action_id,
                "authority_digest": subject.authority_digest,
                "payload": payload,
            }
        )
        return PlanningReceipt(
            subject_digest=subject.digest,
            stable_action_id=subject.stable_action_id,
            status="completed",
            receipt_digest=digest_value(
                {
                    "kind": "local-planning-receipt.v1",
                    "subject_digest": subject.digest,
                    "output_digest": output.digest,
                }
            ),
            output_artifact_digest=output.digest,
            planning_output_artifact_digest=output.digest,
        )


def _candidate_for(action: WorkRunAction) -> CandidateReceipt:
    diff = _candidate_diff_for()
    return CandidateReceipt(
        parent_digest=digest_value(
            {"kind": "local-candidate-parent.v1", "work": action.work_run_key}
        ),
        repository=action.repository,
        campaign_key=action.campaign_key,
        campaign_handle=action.campaign_key,
        plan_revision_digest=action.plan_revision_digest,
        work_run_key=action.work_run_key,
        ticket_key=action.ticket_key,
        reported_reference="refs/heads/local-candidate",
        base_commit_oid=diff.base_commit_oid,
        base_tree_oid=diff.base_tree_oid,
        candidate_commit_oid=diff.candidate_commit_oid,
        candidate_tree_oid=diff.candidate_tree_oid,
        diff_schema_version=diff.schema_version,
        diff_record_digest=diff.digest,
        authority_subtree_digest=digest_value(
            {"kind": "local-worker-authority.v1", "ticket": action.ticket_key}
        ),
        runtime_subject_digest=action.work_subject_digest,
    )


def _candidate_diff_for() -> CandidateDiffRecordV1:
    return CandidateDiffRecordV1(
        schema_version="CandidateDiffRecordV1",
        repository_object_format="sha1",
        base_commit_oid="2" * 40,
        base_tree_oid="3" * 40,
        candidate_commit_oid="4" * 40,
        candidate_tree_oid="5" * 40,
        entries=(),
    )


def _accepted_for(
    action: WorkRunAction,
    candidate: CandidateReceipt,
) -> AcceptedCandidateReceipt:
    evidence_digest = digest_value(
        {"kind": "local-review-evidence.v1", "candidate": candidate.digest}
    )
    return AcceptedCandidateReceipt(
        repository=action.repository,
        campaign_key=action.campaign_key,
        plan_revision_digest=action.plan_revision_digest,
        target_branch=_TARGET_BRANCH,
        ticket_key=action.ticket_key,
        work_run_key=action.work_run_key,
        integration_node_key=f"integration:{action.ticket_key}",
        accepted_sequence=1,
        base_sha=candidate.base_commit_oid,
        base_tree_oid=candidate.base_tree_oid,
        candidate_sha=candidate.candidate_commit_oid,
        candidate_tree_oid=candidate.candidate_tree_oid,
        candidate_receipt_digest=candidate.digest,
        diff_record_digest=candidate.diff_record_digest,
        authority_subtree_digest=candidate.authority_subtree_digest,
        policy_witness_digest=digest_value(
            {"kind": "local-policy-witness.v1", "repository": action.repository}
        ),
        review_subject_digest=digest_value(
            {"kind": "local-review-subject.v1", "candidate": candidate.digest}
        ),
        assurance="standard",
        assurance_requirement_digest=digest_value(
            {"kind": "local-assurance.v1", "mode": "standard"}
        ),
        check_environment_digest=digest_value(
            {"kind": "local-check-environment.v1", "python": "deterministic"}
        ),
        delivery_identity_digest=digest_value(
            {"kind": "local-delivery-identity.v1", "candidate": candidate.digest}
        ),
        interaction_keys=(
            InteractionKey(
                "candidate-path",
                "src/local_acceptance.py",
                InteractionClassification.ORDINARY,
            ),
        ),
        protected_surfaces=(),
        gitlink_change=False,
        evidence_digests=(evidence_digest,),
        review_finding_ledger_digest=digest_value(
            {"kind": "local-review-ledger.v1", "evidence": evidence_digest}
        ),
    )


def _root_assurance(ticket_key: str) -> str:
    return "strict" if ticket_key == _ROOT_STRICT_TICKET else "standard"


def _root_candidate_diff_for(
    ticket_key: str,
    *,
    variant: str = "accepted",
) -> CandidateDiffRecordV1:
    values = {
        "issue:101": ("4" * 40, "5" * 40),
        "issue:102": ("6" * 40, "7" * 40),
        "issue:103": ("8" * 40, "9" * 40),
        "issue:104": ("a" * 40, "b" * 40),
    }
    if variant == "rejected":
        values["issue:103"] = ("c" * 40, "d" * 40)
    candidate_commit_oid, candidate_tree_oid = values[ticket_key]
    return CandidateDiffRecordV1(
        schema_version="CandidateDiffRecordV1",
        repository_object_format="sha1",
        base_commit_oid="2" * 40,
        base_tree_oid="3" * 40,
        candidate_commit_oid=candidate_commit_oid,
        candidate_tree_oid=candidate_tree_oid,
        entries=(),
    )


def _root_candidate_for(
    action: WorkRunAction,
    *,
    variant: str = "accepted",
) -> CandidateReceipt:
    diff = _root_candidate_diff_for(action.ticket_key, variant=variant)
    return CandidateReceipt(
        parent_digest=digest_value(
            {"kind": "local-root-candidate-parent.v1", "work": action.work_run_key}
        ),
        repository=action.repository,
        campaign_key=action.campaign_key,
        campaign_handle=action.campaign_key,
        plan_revision_digest=action.plan_revision_digest,
        work_run_key=action.work_run_key,
        ticket_key=action.ticket_key,
        reported_reference=f"refs/heads/local-root/{action.ticket_key}",
        base_commit_oid=diff.base_commit_oid,
        base_tree_oid=diff.base_tree_oid,
        candidate_commit_oid=diff.candidate_commit_oid,
        candidate_tree_oid=diff.candidate_tree_oid,
        diff_schema_version=diff.schema_version,
        diff_record_digest=diff.digest,
        authority_subtree_digest=digest_value(
            {"kind": "local-root-worker-authority.v1", "ticket": action.ticket_key}
        ),
        runtime_subject_digest=action.work_subject_digest,
    )


def _root_accepted_for(
    action: WorkRunAction,
    candidate: CandidateReceipt,
) -> AcceptedCandidateReceipt:
    evidence_digest = digest_value(
        {"kind": "local-root-review-evidence.v1", "candidate": candidate.digest}
    )
    standard = action.ticket_key in _ROOT_STANDARD_TICKETS
    interaction = InteractionKey(
        "candidate-path",
        f"src/root/{action.ticket_key}.py",
        InteractionClassification.ORDINARY if standard else InteractionClassification.PROTECTED,
    )
    assurance = _root_assurance(action.ticket_key)
    return AcceptedCandidateReceipt(
        repository=action.repository,
        campaign_key=action.campaign_key,
        plan_revision_digest=action.plan_revision_digest,
        target_branch=_TARGET_BRANCH,
        ticket_key=action.ticket_key,
        work_run_key=action.work_run_key,
        integration_node_key=f"integration:{action.ticket_key}",
        accepted_sequence=1,
        base_sha=candidate.base_commit_oid,
        base_tree_oid=candidate.base_tree_oid,
        candidate_sha=candidate.candidate_commit_oid,
        candidate_tree_oid=candidate.candidate_tree_oid,
        candidate_receipt_digest=candidate.digest,
        diff_record_digest=candidate.diff_record_digest,
        authority_subtree_digest=candidate.authority_subtree_digest,
        policy_witness_digest=digest_value(
            {"kind": "local-root-policy-witness.v1", "repository": action.repository}
        ),
        review_subject_digest=digest_value(
            {"kind": "local-root-review-subject.v1", "candidate": candidate.digest}
        ),
        assurance=assurance,
        assurance_requirement_digest=digest_value(
            {"kind": "local-root-assurance.v1", "mode": assurance}
        ),
        check_environment_digest=digest_value(
            {"kind": "local-root-check-environment.v1", "mode": assurance}
        ),
        delivery_identity_digest=digest_value(
            {
                "kind": "local-root-delivery-identity.v1",
                "mode": "standard" if standard else "strict",
            }
        ),
        interaction_keys=(interaction,),
        protected_surfaces=() if standard else ("src/root/strict-surface.py",),
        gitlink_change=False,
        evidence_digests=(evidence_digest,),
        review_finding_ledger_digest=digest_value(
            {"kind": "local-root-review-ledger.v1", "evidence": evidence_digest}
        ),
    )


class _LocalDeliveryStub:
    def __init__(self, store_path: Path, scenario: str = "single"):
        self.store_path = Path(store_path)
        self.scenario = scenario
        self.store_path.parent.mkdir(parents=True, exist_ok=True)
        with _connection(self.store_path) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS deliveries(
                    stable_action_id TEXT PRIMARY KEY,
                    observation_json BLOB NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS counters(
                    singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
                    execute_calls INTEGER NOT NULL
                )
                """
            )
            connection.execute(
                """
                INSERT INTO counters(singleton, execute_calls)
                VALUES (1, 0)
                ON CONFLICT(singleton) DO NOTHING
                """
            )

    @property
    def execute_calls(self) -> int:
        with _connection(self.store_path) as connection:
            row = connection.execute(
                "SELECT execute_calls FROM counters WHERE singleton = 1"
            ).fetchone()
        assert row is not None
        return int(row[0])

    def _observation(self, action: WorkRunAction) -> WorkRunObservation:
        if self.scenario == "root":
            candidate = _root_candidate_for(action)
            accepted = _root_accepted_for(action, candidate)
        else:
            candidate = _candidate_for(action)
            accepted = _accepted_for(action, candidate)
        request_digest = action.batch_delivery_request_digest
        if request_digest is None:
            raise AssertionError("Batch delivery request digest was not bound")
        if self.scenario == "root":
            standard = action.ticket_key in _ROOT_STANDARD_TICKETS
            batch_group = "standard" if standard else "strict"
            batch_id = digest_value(
                {"kind": "local-root-integration-batch.v1", "group": batch_group}
            )
            batch_sha = (
                _ROOT_STANDARD_BATCH_SHA if standard else _ROOT_STRICT_BATCH_SHA
            )
            member_ticket_keys = (
                _ROOT_STANDARD_TICKETS if standard else (_ROOT_STRICT_TICKET,)
            )
            target_head_sha = (
                _ROOT_STANDARD_TARGET_SHA if standard else _ROOT_STRICT_TARGET_SHA
            )
            evidence_digests = accepted.evidence_digests
        else:
            batch_id = digest_value(
                {"kind": "local-batch.v1", "request_digest": request_digest}
            )
            batch_sha = candidate.candidate_commit_oid
            member_ticket_keys = (action.ticket_key,)
            target_head_sha = "6" * 40
            evidence_digests = accepted.evidence_digests
        delivery = BatchDeliveryProof.create(
            delivery_stable_action_id=action.stable_action_id,
            delivery_request_digest=request_digest,
            batch_id=batch_id,
            batch_sha=batch_sha,
            member_ticket_keys=member_ticket_keys,
            local_check_receipt_digest=digest_value(
                {"kind": "local-check.v1", "request_digest": request_digest}
            ),
            publication_receipt_digest=digest_value(
                {"kind": "local-publication.v1", "batch_id": batch_id}
            ),
            pull_request_number=1,
            pull_request_head_sha=batch_sha,
            hosted_result_receipt_digest=digest_value(
                {"kind": "local-hosted-readback.v1", "batch_id": batch_id}
            ),
            integration_lease_digest=digest_value(
                {"kind": "local-integration-lease.v1", "batch_id": batch_id}
            ),
            target_branch=_TARGET_BRANCH,
            target_head_sha=target_head_sha,
            target_readback_digest=digest_value(
                {"kind": "local-target-readback.v1", "batch_id": batch_id}
            ),
            target_contains_batch_sha=True,
            pull_request_merge_target_sha=target_head_sha,
            merge_method="merge",
        )
        delivery_receipt_digest = digest_value(
            {
                "kind": "local-batch-observation.v1",
                "action": action.stable_action_id,
                "proof": delivery.proof_digest,
            }
        )
        proof = ResultIntegrityProof(
            accepted_candidate_receipt_digest=accepted.digest,
            candidate_commit_oid=candidate.candidate_commit_oid,
            candidate_tree_oid=candidate.candidate_tree_oid,
            candidate_diff_record_digest=candidate.diff_record_digest,
            batch_delivery_receipt_digest=delivery_receipt_digest,
            batch_delivery_stable_action_id=action.stable_action_id,
            batch_delivery_request_digest=request_digest,
            batch_delivery_batch_id=delivery.batch_id,
            batch_delivery_batch_sha=delivery.batch_sha,
            batch_delivery_proof_digest=delivery.proof_digest,
            delivery_stable_action_id=delivery.delivery_stable_action_id,
            delivery_request_digest=delivery.delivery_request_digest,
            batch_id=delivery.batch_id,
            batch_sha=delivery.batch_sha,
            delivery_member_ticket_keys=delivery.member_ticket_keys,
            local_check_receipt_digest=delivery.local_check_receipt_digest,
            publication_receipt_digest=delivery.publication_receipt_digest,
            pull_request_number=delivery.pull_request_number,
            pull_request_head_sha=delivery.pull_request_head_sha,
            hosted_result_receipt_digest=delivery.hosted_result_receipt_digest,
            integration_lease_digest=delivery.integration_lease_digest,
            target_branch=delivery.target_branch,
            target_head_sha=delivery.target_head_sha,
            target_readback_digest=delivery.target_readback_digest,
            target_contains_batch_sha=delivery.target_contains_batch_sha,
            pull_request_merge_target_sha=delivery.pull_request_merge_target_sha,
            merge_method=delivery.merge_method,
            result_digest="",
            evidence_digests=evidence_digests,
        )
        proof = replace(proof, result_digest=proof.expected_result_digest())
        return WorkRunObservation(
            phase="completed",
            stable_action_id=action.stable_action_id,
            runtime_binding_id=action.runtime_binding_id,
            receipt_digest=delivery_receipt_digest,
            candidate_receipt=candidate,
            accepted_candidate_receipt_digest=accepted.digest,
            candidate_diff_record_digest=candidate.diff_record_digest,
            delivery_receipt_digest=delivery_receipt_digest,
            result_digest=proof.result_digest,
            evidence_digests=proof.evidence_digests,
            result_integrity=proof,
        )

    def readback(self, action: WorkRunAction) -> WorkRunObservation | None:
        with _connection(self.store_path) as connection:
            row = connection.execute(
                "SELECT observation_json FROM deliveries WHERE stable_action_id = ?",
                (action.stable_action_id,),
            ).fetchone()
        if row is None:
            return None
        return WorkRunObservation.from_canonical(load_canonical_json(row[0]))

    def readbacks(self) -> tuple[WorkRunObservation, ...]:
        with _connection(self.store_path) as connection:
            rows = connection.execute(
                "SELECT observation_json FROM deliveries ORDER BY stable_action_id"
            ).fetchall()
        return tuple(
            WorkRunObservation.from_canonical(load_canonical_json(row[0]))
            for row in rows
        )

    def execute(self, action: WorkRunAction) -> WorkRunObservation:
        existing = self.readback(action)
        if existing is not None:
            return existing
        observation = self._observation(action)
        rendered = canonical_bytes(observation.canonical())
        with _connection(self.store_path) as connection:
            connection.execute(
                "UPDATE counters SET execute_calls = execute_calls + 1 WHERE singleton = 1"
            )
            connection.execute(
                "INSERT INTO deliveries(stable_action_id, observation_json) VALUES (?, ?)",
                (action.stable_action_id, rendered),
            )
        return observation


class _LocalEffects:
    def __init__(self, store_path: Path, delivery: _LocalDeliveryStub, scenario: str):
        self.store_path = Path(store_path)
        self.store_path.parent.mkdir(parents=True, exist_ok=True)
        self.delivery = delivery
        self.scenario = scenario
        self.gate_events: list[dict[str, Any]] = []
        self._semantic_attempts: dict[str, int] = {}
        with _connection(self.store_path) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS observations(
                    stable_action_id TEXT PRIMARY KEY,
                    observation_json BLOB NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS counters(
                    singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
                    execute_calls INTEGER NOT NULL
                )
                """
            )
            connection.execute(
                """
                INSERT INTO counters(singleton, execute_calls)
                VALUES (1, 0)
                ON CONFLICT(singleton) DO NOTHING
                """
            )

    def bind_batch_delivery_request_digest(self, action: WorkRunAction) -> str:
        if self.scenario == "root":
            return digest_value(
                {
                    "kind": "local-root-batch-request.v1",
                    "campaign": action.campaign_key,
                    "group": (
                        "standard"
                        if action.ticket_key in _ROOT_STANDARD_TICKETS
                        else "strict"
                    ),
                }
            )
        return digest_value(
            {
                "kind": "local-batch-request.v1",
                "campaign": action.campaign_key,
                "ticket": action.ticket_key,
                "candidate": action.accepted_candidate_receipt_digest,
            }
        )

    @property
    def execute_calls(self) -> int:
        with _connection(self.store_path) as connection:
            row = connection.execute(
                "SELECT execute_calls FROM counters WHERE singleton = 1"
            ).fetchone()
        assert row is not None
        return int(row[0])

    def _read_semantic(self, action: WorkRunAction) -> WorkRunObservation | None:
        with _connection(self.store_path) as connection:
            row = connection.execute(
                "SELECT observation_json FROM observations WHERE stable_action_id = ?",
                (action.stable_action_id,),
            ).fetchone()
        if row is None:
            return None
        return WorkRunObservation.from_canonical(load_canonical_json(row[0]))

    def readback(self, action: WorkRunAction) -> WorkRunObservation | None:
        if action.kind == "batch_delivery":
            return self.delivery.readback(action)
        if (
            self.scenario == "root"
            and action.kind in {"semantic_execution", "semantic_resume"}
            and action.wake_ref is not None
            and action.wake_ref.startswith("watchdog:due:")
        ):
            existing = self._read_semantic(action)
            if existing is not None:
                return existing
            return self._root_accepted_observation(action)
        return self._read_semantic(action)

    def canonical_readbacks(self) -> dict[str, list[dict[str, Any]]]:
        with _connection(self.store_path) as connection:
            rows = connection.execute(
                "SELECT observation_json FROM observations ORDER BY stable_action_id"
            ).fetchall()
        semantic = [
            WorkRunObservation.from_canonical(load_canonical_json(row[0]))
            for row in rows
        ]
        return {
            "semantic": [observation.canonical() for observation in semantic],
            "delivery": [
                observation.canonical() for observation in self.delivery.readbacks()
            ],
        }

    def _save_semantic(self, action: WorkRunAction, observation: WorkRunObservation):
        rendered = canonical_bytes(observation.canonical())
        with _connection(self.store_path) as connection:
            connection.execute(
                "INSERT INTO observations(stable_action_id, observation_json) VALUES (?, ?)",
                (action.stable_action_id, rendered),
            )

    def _root_accepted_observation(self, action: WorkRunAction) -> WorkRunObservation:
        candidate = _root_candidate_for(action)
        accepted = _root_accepted_for(action, candidate)
        event: dict[str, Any] = {
            "ticket_key": action.ticket_key,
            "assurance": _root_assurance(action.ticket_key),
            "review": "accepted",
            "candidate_receipt_digest": candidate.digest,
            "accepted_candidate_receipt_digest": accepted.digest,
        }
        if action.ticket_key == "issue:102":
            event["review"] = "repair_required"
            event["repair"] = "repair_verify"
        elif action.ticket_key == "issue:103":
            rejected = _root_candidate_for(action, variant="rejected")
            event["review"] = "rejected"
            event["rejected_candidate_receipt_digest"] = rejected.digest
            event["repair"] = "replacement_candidate"
        elif action.ticket_key == _ROOT_STRICT_TICKET:
            event["specialist_review"] = "accepted"
        self.gate_events.append(event)
        observation = WorkRunObservation(
            phase="accepted_awaiting_delivery",
            stable_action_id=action.stable_action_id,
            receipt_digest=candidate.digest,
            candidate_identity=f"candidate:{candidate.candidate_commit_oid}",
            candidate_receipt=candidate,
            runtime_binding_id=action.runtime_binding_id,
            accepted_candidate_receipt_digest=accepted.digest,
            candidate_diff_record_digest=candidate.diff_record_digest,
        )
        self._save_semantic(action, observation)
        return observation

    def execute(self, action: WorkRunAction) -> WorkRunObservation:
        existing = self.readback(action)
        if existing is not None:
            return existing
        if self.scenario == "failure":
            raise LocalAcceptanceFailure("deterministic local Runtime failure")
        if action.kind == "batch_delivery":
            return self.delivery.execute(action)
        with _connection(self.store_path) as connection:
            connection.execute(
                "UPDATE counters SET execute_calls = execute_calls + 1 WHERE singleton = 1"
            )
        if self.scenario == "root":
            attempt = self._semantic_attempts.get(action.stable_action_id, 0) + 1
            self._semantic_attempts[action.stable_action_id] = attempt
            if attempt == 1:
                return WorkRunObservation(
                    phase="running",
                    stable_action_id=action.stable_action_id,
                    receipt_digest=digest_value(
                        {
                            "kind": "local-root-running.v1",
                            "action": action.stable_action_id,
                        }
                    ),
                    reason="LocalRootCandidateCallbackLost",
                    next_check_at="2026-08-11T00:00:00+00:00",
                    runtime_binding_id=f"binding:root:{action.ticket_key}",
                )
            return self._root_accepted_observation(action)
        if self.scenario == "wait":
            observation = WorkRunObservation(
                phase="wait",
                stable_action_id=action.stable_action_id,
                receipt_digest=digest_value(
                    {"kind": "local-wait.v1", "action": action.stable_action_id}
                ),
                reason="LocalRuntimeWait",
                next_check_at="2026-08-11T00:00:00+00:00",
                binding_established=False,
            )
        elif self.scenario == "blocked":
            observation = WorkRunObservation(
                phase="blocked",
                stable_action_id=action.stable_action_id,
                receipt_digest=digest_value(
                    {"kind": "local-blocked.v1", "action": action.stable_action_id}
                ),
                reason="LocalRuntimeBlocked",
                binding_established=False,
            )
        elif action.kind == "semantic_execution":
            candidate = _candidate_for(action)
            accepted = _accepted_for(action, candidate)
            observation = WorkRunObservation(
                phase="accepted_awaiting_delivery",
                stable_action_id=action.stable_action_id,
                receipt_digest=candidate.digest,
                candidate_identity=f"candidate:{candidate.candidate_commit_oid}",
                candidate_receipt=candidate,
                runtime_binding_id="binding:local",
                accepted_candidate_receipt_digest=accepted.digest,
                candidate_diff_record_digest=candidate.diff_record_digest,
            )
        else:  # pragma: no cover - the Kernel owns the closed action union.
            raise LocalAcceptanceFailure(f"unsupported local action: {action.kind}")
        self._save_semantic(action, observation)
        return observation


@dataclass
class _Harness:
    root: Path
    scenario: str
    run_id: str
    handle: CampaignHandle
    effects: _LocalEffects
    delivery: _LocalDeliveryStub
    control: PlanControl
    configuration: ExecutionKernelConfiguration
    kernel: object | None = None


@dataclass(frozen=True)
class _LocalStartHost:
    control: PlanControl
    campaign_key: str

    def start(
        self,
        repository: str,
        ready_refs: tuple[str, ...],
        options: object = None,
    ) -> CampaignHandle:
        return self.control.start(
            repository,
            ready_refs,
            options,
            campaign_key=self.campaign_key,
        )


@dataclass
class _RootWatchdogEventSource:
    handle: CampaignHandle
    emit_callback: bool = False

    def read(self, after_cursor: str | None) -> WatchdogWakePage:
        if self.emit_callback and after_cursor is None:
            return WatchdogWakePage(
                events=(
                    WatchdogWake(
                        "1",
                        self.handle,
                        "runtime",
                        "local-root-callback",
                    ),
                ),
                next_cursor="1",
            )
        return WatchdogWakePage(events=(), next_cursor=after_cursor)


@dataclass(frozen=True)
class _PublicAdvance:
    def advance(
        self,
        handle: CampaignHandle,
        wake_ref: str | None = None,
    ):
        return gwo_v8.advance(handle, wake_ref)


def _canonical_readback(effects: _LocalEffects) -> dict[str, Any]:
    observations = effects.canonical_readbacks()
    all_observations = [
        WorkRunObservation.from_canonical(value)
        for group in observations.values()
        for value in group
    ]
    candidate_observation = next(
        (
            observation
            for observation in all_observations
            if observation.candidate_receipt is not None
        ),
        None,
    )
    candidate = (
        candidate_observation.candidate_receipt
        if candidate_observation is not None
        else None
    )
    if candidate is None or candidate_observation is None:
        return {
            "observations": observations,
            "candidate_receipt": None,
            "candidate_diff": None,
            "accepted_candidate_receipt": None,
            "delivery_proof": None,
            "result_integrity": None,
        }

    action = WorkRunAction(
        stable_action_id=candidate_observation.stable_action_id,
        repository=candidate.repository,
        campaign_key=candidate.campaign_key,
        plan_revision_digest=candidate.plan_revision_digest,
        ticket_key=candidate.ticket_key,
        kind="semantic_execution",
        semantic_action_id=candidate_observation.stable_action_id,
        work_run_key=candidate.work_run_key,
        work_subject_digest=candidate.runtime_subject_digest,
        runtime_binding_id=candidate_observation.runtime_binding_id,
    )
    diff = _candidate_diff_for()
    accepted = _accepted_for(action, candidate)
    result_observation = next(
        (
            observation
            for observation in all_observations
            if observation.result_integrity is not None
        ),
        None,
    )
    proof = (
        result_observation.result_integrity
        if result_observation is not None
        else None
    )
    delivery = None
    if proof is not None:
        delivery = BatchDeliveryProof.create(
            delivery_stable_action_id=proof.delivery_stable_action_id,
            delivery_request_digest=proof.delivery_request_digest,
            batch_id=proof.batch_id,
            batch_sha=proof.batch_sha,
            member_ticket_keys=proof.delivery_member_ticket_keys,
            local_check_receipt_digest=proof.local_check_receipt_digest,
            publication_receipt_digest=proof.publication_receipt_digest,
            pull_request_number=proof.pull_request_number,
            pull_request_head_sha=proof.pull_request_head_sha,
            hosted_result_receipt_digest=proof.hosted_result_receipt_digest,
            integration_lease_digest=proof.integration_lease_digest,
            target_branch=proof.target_branch,
            target_head_sha=proof.target_head_sha,
            target_readback_digest=proof.target_readback_digest,
            target_contains_batch_sha=proof.target_contains_batch_sha,
            pull_request_merge_target_sha=proof.pull_request_merge_target_sha,
            merge_method=proof.merge_method,
        )
    return {
        "observations": observations,
        "candidate_receipt": candidate.canonical(),
        "candidate_diff": diff.canonical(),
        "accepted_candidate_receipt": accepted.canonical(),
        "delivery_proof": None if delivery is None else delivery.canonical(),
        "result_integrity": None if proof is None else proof.canonical(),
    }


def _root_action_for_observation(
    observation: WorkRunObservation,
    candidate: CandidateReceipt,
) -> WorkRunAction:
    return WorkRunAction(
        stable_action_id=observation.stable_action_id,
        repository=candidate.repository,
        campaign_key=candidate.campaign_key,
        plan_revision_digest=candidate.plan_revision_digest,
        ticket_key=candidate.ticket_key,
        kind="semantic_execution",
        semantic_action_id=observation.stable_action_id,
        work_run_key=candidate.work_run_key,
        work_subject_digest=candidate.runtime_subject_digest,
        runtime_binding_id=observation.runtime_binding_id,
    )


def _delivery_from_result_proof(proof: ResultIntegrityProof) -> BatchDeliveryProof:
    return BatchDeliveryProof.create(
        delivery_stable_action_id=proof.delivery_stable_action_id,
        delivery_request_digest=proof.delivery_request_digest,
        batch_id=proof.batch_id,
        batch_sha=proof.batch_sha,
        member_ticket_keys=proof.delivery_member_ticket_keys,
        local_check_receipt_digest=proof.local_check_receipt_digest,
        publication_receipt_digest=proof.publication_receipt_digest,
        pull_request_number=proof.pull_request_number,
        pull_request_head_sha=proof.pull_request_head_sha,
        hosted_result_receipt_digest=proof.hosted_result_receipt_digest,
        integration_lease_digest=proof.integration_lease_digest,
        target_branch=proof.target_branch,
        target_head_sha=proof.target_head_sha,
        target_readback_digest=proof.target_readback_digest,
        target_contains_batch_sha=proof.target_contains_batch_sha,
        pull_request_merge_target_sha=proof.pull_request_merge_target_sha,
        merge_method=proof.merge_method,
    )


def _canonical_root_readback(effects: _LocalEffects) -> dict[str, Any]:
    observations = effects.canonical_readbacks()
    all_observations = [
        WorkRunObservation.from_canonical(value)
        for group in observations.values()
        for value in group
    ]
    candidates_by_ticket: dict[str, CandidateReceipt] = {}
    accepted_by_ticket: dict[str, AcceptedCandidateReceipt] = {}
    for observation in all_observations:
        candidate = observation.candidate_receipt
        if candidate is None or candidate.ticket_key in candidates_by_ticket:
            continue
        candidates_by_ticket[candidate.ticket_key] = candidate
        accepted_by_ticket[candidate.ticket_key] = _root_accepted_for(
            _root_action_for_observation(observation, candidate), candidate
        )

    result_observations = sorted(
        (
            observation
            for observation in all_observations
            if observation.result_integrity is not None
        ),
        key=lambda observation: observation.result_integrity.accepted_candidate_receipt_digest,
    )
    proofs = [
        _delivery_from_result_proof(observation.result_integrity)
        for observation in result_observations
    ]
    batches: dict[str, dict[str, Any]] = {}
    for proof in proofs:
        batches.setdefault(
            proof.batch_id,
            {
                "batch_id": proof.batch_id,
                "batch_sha": proof.batch_sha,
                "member_ticket_keys": list(proof.member_ticket_keys),
                "target_branch": proof.target_branch,
                "target_head_sha": proof.target_head_sha,
                "target_contains_batch_sha": proof.target_contains_batch_sha,
            },
        )
    ordered_batches = sorted(
        batches.values(),
        key=lambda batch: (batch["member_ticket_keys"], batch["batch_id"]),
    )
    return {
        "observations": observations,
        "candidate_receipts": [
            candidates_by_ticket[ticket_key].canonical()
            for ticket_key in _ROOT_TICKET_KEYS
        ],
        "candidate_diffs": [
            _root_candidate_diff_for(ticket_key).canonical()
            for ticket_key in _ROOT_TICKET_KEYS
        ],
        "accepted_candidate_receipts": [
            accepted_by_ticket[ticket_key].canonical()
            for ticket_key in _ROOT_TICKET_KEYS
        ],
        "delivery_proofs": [proof.canonical() for proof in proofs],
        "result_integrities": [
            observation.result_integrity.canonical()
            for observation in result_observations
        ],
        "git_readback": {
            "target_branch": _TARGET_BRANCH,
            "batches": ordered_batches,
        },
    }


def _diagnostics_record(
    diagnostics: object,
    *,
    root: bool = False,
) -> dict[str, Any]:
    value = {
        "status": diagnostics.status.value,
        "reason": diagnostics.reason,
        "plan_revision_digest": diagnostics.plan_revision_digest,
        "worker_slots": dict(diagnostics.worker_slots),
        "work_runs": [
            {
                "ticket_key": run.ticket_key,
                "work_run_key": run.work_run_key,
                "phase": run.phase,
                "claim_state": run.claim_state,
                "slot_held": run.slot_held,
                "candidate_identity": run.candidate_identity,
                "accepted_candidate_receipt_digest": run.accepted_candidate_receipt_digest,
                "candidate_diff_record_digest": run.candidate_diff_record_digest,
                "delivery_receipt_digest": run.delivery_receipt_digest,
                "result_digest": run.result_digest,
                "evidence_digests": list(run.evidence_digests),
                "reason": run.reason,
                "next_check_at": run.next_check_at,
                **(
                    {"exclusive_resources": list(run.exclusive_resources)}
                    if root
                    else {}
                ),
            }
            for run in diagnostics.work_runs
        ],
        "outstanding_effect_ids": list(diagnostics.outstanding_effect_ids),
    }
    return value


def _install_harness(root: Path, run_id: str, scenario: str) -> tuple[_Harness, CampaignHandle]:
    root = Path(root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    repository_root = root / "repository"
    repository_root.mkdir(parents=True, exist_ok=True)
    (repository_root / "README.md").write_text(
        "deterministic V8 local acceptance\n", encoding="utf-8"
    )
    sqlite_root = root / "sqlite"
    sqlite_root.mkdir(parents=True, exist_ok=True)
    artifacts = ArtifactStore(root / "artifacts")
    ticket_keys = _ROOT_TICKET_KEYS if scenario == "root" else (_TICKET_KEY,)
    planning_gateway = _LocalPlanningGateway(
        artifacts,
        [],
        ticket_keys=ticket_keys,
        exclusive_resources=(
            {ticket_key: () for ticket_key in _ROOT_STANDARD_TICKETS}
            | {_ROOT_STRICT_TICKET: ("repository.target.v1",)}
            if scenario == "root"
            else None
        ),
    )
    control = PlanControl(
        source=_LocalSnapshotSource(ticket_keys),
        artifacts=artifacts,
        gateway=planning_gateway,
        repository=InMemoryPlanRepository(writer_generation="writer:local"),
    )
    _install_start_host(
        _LocalStartHost(control, _campaign_key(run_id, scenario))
    )
    delivery = _LocalDeliveryStub(sqlite_root / "delivery.sqlite3", scenario)
    effects = _LocalEffects(
        sqlite_root / "effects.sqlite3",
        delivery,
        scenario,
    )
    configuration = ExecutionKernelConfiguration(
        host_worker_slots=4 if scenario == "root" else 1,
        repository_worker_slots={
            _REPOSITORY: 4 if scenario == "root" else 1
        },
        host_stale_after_seconds=1800,
        repository_stale_after_seconds={_REPOSITORY: 1800},
    )
    kernel = install_execution_kernel(
        store_path=sqlite_root / "execution.sqlite3",
        plan_control=control,
        effects=effects,
        configuration=configuration,
    )
    handle = gwo_v8.start(_REPOSITORY, ticket_keys)
    return (
        _Harness(
            root=root,
            scenario=scenario,
            run_id=run_id,
            handle=handle,
            effects=effects,
            delivery=delivery,
            control=control,
            configuration=configuration,
            kernel=kernel,
        ),
        handle,
    )


def _install_restart(harness: _Harness) -> None:
    effects = _LocalEffects(
        harness.root / "sqlite" / "effects.sqlite3",
        _LocalDeliveryStub(
            harness.root / "sqlite" / "delivery.sqlite3", harness.scenario
        ),
        harness.scenario,
    )
    harness.effects = effects
    harness.delivery = effects.delivery
    harness.kernel = install_execution_kernel(
        store_path=harness.root / "sqlite" / "execution.sqlite3",
        plan_control=harness.control,
        effects=effects,
        configuration=harness.configuration,
    )


def _outcome_record(outcome: object) -> dict[str, str]:
    return {
        "status": outcome.status.value,
        "reason": outcome.reason,
    }


def _record_digest(record: dict[str, Any]) -> dict[str, Any]:
    return {**record, "record_digest": digest_value(record)}


def _root_batch_facts(
    readback: dict[str, Any],
    work_runs: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    proof_by_ticket: dict[str, dict[str, Any]] = {}
    for proof in readback["result_integrities"]:
        accepted_digest = proof["accepted_candidate_receipt_digest"]
        for run in work_runs:
            if run["accepted_candidate_receipt_digest"] == accepted_digest:
                proof_by_ticket[run["ticket_key"]] = proof
    batches: list[dict[str, Any]] = []
    for member_ticket_keys, singleton in (
        (_ROOT_STANDARD_TICKETS, False),
        ((_ROOT_STRICT_TICKET,), True),
    ):
        member_proofs = [proof_by_ticket[ticket_key] for ticket_key in member_ticket_keys]
        first = member_proofs[0]
        members = [
            next(run for run in work_runs if run["ticket_key"] == ticket_key)
            for ticket_key in member_ticket_keys
        ]
        batches.append(
            {
                "batch_id": first["batch_id"],
                "batch_sha": first["batch_sha"],
                "member_ticket_keys": list(member_ticket_keys),
                "singleton": singleton,
                "candidate_receipt_digests": [
                    member["candidate_receipt_digest"] for member in members
                ],
                "delivery_receipt_digests": [
                    member["delivery_receipt_digest"] for member in members
                ],
                "result_digests": [member["result_digest"] for member in members],
                "evidence_digests": sorted(
                    {
                        digest
                        for member in members
                        for digest in member["evidence_digests"]
                    }
                ),
                "target_branch": first["target_branch"],
                "target_head_sha": first["target_head_sha"],
                "target_contains_batch_sha": first["target_contains_batch_sha"],
            }
        )
    return batches


def _run_root_acceptance_in_root(
    *,
    root: Path,
    run_id: str,
) -> dict[str, Any]:
    harness, handle = _install_harness(Path(root), run_id, "root")
    initial = gwo_v8.inspect(handle)
    transcript: list[dict[str, Any]] = [
        {"operation": "start", "campaign_key": handle.campaign_key},
        {
            "operation": "inspect",
            "readback": _diagnostics_record(initial, root=True),
        },
    ]

    def advance_with_readback(wake_ref: str) -> dict[str, str]:
        outcome = gwo_v8.advance(handle, wake_ref)
        result = _outcome_record(outcome)
        transcript.append(
            {
                "operation": "advance",
                "wake_ref": wake_ref,
                "outcome": result,
            }
        )
        return result

    def inspect_with_readback() -> object:
        diagnostics = gwo_v8.inspect(handle)
        transcript.append(
            {
                "operation": "inspect",
                "readback": _diagnostics_record(diagnostics, root=True),
            }
        )
        return diagnostics

    initial_outcome = advance_with_readback("root:initial")
    initial_after_advance = inspect_with_readback()

    watchdog = CampaignWatchdog(
        store_path=Path(root) / "sqlite" / "watchdog.sqlite3",
        event_sources={
            "runtime": _RootWatchdogEventSource(handle),
        },
        campaign_source=harness.kernel,
        advancer=_PublicAdvance(),
    )
    lost_wake_outcomes = watchdog.run_once("2026-08-11T00:00:01+00:00")
    lost_wake = _outcome_record(lost_wake_outcomes[-1])
    transcript.append(
        {
            "operation": "watchdog",
            "kind": "lost_wake",
            "outcomes": [_outcome_record(item) for item in lost_wake_outcomes],
        }
    )
    after_watchdog = inspect_with_readback()

    duplicate_callback_ref = WatchdogWake(
        "1",
        handle,
        "runtime",
        "local-root-candidate-callback",
    ).wake_ref
    duplicate_callback = advance_with_readback(duplicate_callback_ref)
    inspect_with_readback()

    gate_events = list(harness.effects.gate_events)
    before_restart_readback = _canonical_root_readback(harness.effects)
    replay: dict[str, Any] = {
        "initial_advance": initial_outcome,
        "initial_work_runs": len(initial_after_advance.work_runs),
        "initial_worker_slots": dict(initial_after_advance.worker_slots),
        "watchdog_progressed": bool(lost_wake_outcomes),
        "lost_wake": lost_wake,
        "duplicate_callback_ref": duplicate_callback_ref,
        "duplicate_callback": duplicate_callback,
        "watchdog_status_after_progress": after_watchdog.status.value,
        "semantic_execute_calls_before_restart": harness.effects.execute_calls,
        "delivery_execute_calls_before_restart": harness.delivery.execute_calls,
        "readback_digest_before_restart": digest_value(before_restart_readback),
    }

    _install_restart(harness)
    restarted = inspect_with_readback()
    replay["restart_inspect"] = _diagnostics_record(restarted, root=True)
    replay["restart_advance"] = advance_with_readback("root:restart")
    restarted_after_advance = inspect_with_readback()
    replay["restart_advance_inspect"] = _diagnostics_record(
        restarted_after_advance,
        root=True,
    )
    replay["repeated_restart_advance"] = advance_with_readback("root:restart")
    final = inspect_with_readback()
    replay["final_inspect"] = _diagnostics_record(final, root=True)
    after_replay_readback = _canonical_root_readback(harness.effects)
    replay["semantic_execute_calls_after_replay"] = harness.effects.execute_calls
    replay["delivery_execute_calls_after_replay"] = harness.delivery.execute_calls
    replay["readback_digest_after_replay"] = digest_value(after_replay_readback)
    replay["readback_unchanged"] = before_restart_readback == after_replay_readback
    replay["idempotent_delivery"] = (
        replay["delivery_execute_calls_before_restart"]
        == replay["delivery_execute_calls_after_replay"]
    )
    replay["idempotent_effects"] = (
        replay["semantic_execute_calls_before_restart"]
        == replay["semantic_execute_calls_after_replay"]
        and replay["idempotent_delivery"]
    )

    readback = after_replay_readback
    proof_by_accepted = {
        proof["accepted_candidate_receipt_digest"]: proof
        for proof in readback["result_integrities"]
    }
    work_runs: list[dict[str, Any]] = []
    for run in final.work_runs:
        proof = proof_by_accepted[run.accepted_candidate_receipt_digest]
        work_runs.append(
            {
                "ticket_key": run.ticket_key,
                "work_run_key": run.work_run_key,
                "phase": run.phase,
                "claim_state": run.claim_state,
                "slot_held": run.slot_held,
                "exclusive_resources": list(run.exclusive_resources),
                "candidate_identity": run.candidate_identity,
                "candidate_receipt_digest": next(
                    receipt["receipt_digest"]
                    for receipt in readback["candidate_receipts"]
                    if receipt["ticket_key"] == run.ticket_key
                ),
                "accepted_candidate_receipt_digest": run.accepted_candidate_receipt_digest,
                "candidate_diff_record_digest": run.candidate_diff_record_digest,
                "delivery_receipt_digest": run.delivery_receipt_digest,
                "batch_id": proof["batch_id"],
                "batch_sha": proof["batch_sha"],
                "result_digest": run.result_digest,
                "evidence_digests": list(run.evidence_digests),
                "git_readback": {
                    "target_branch": proof["target_branch"],
                    "target_head_sha": proof["target_head_sha"],
                    "batch_id": proof["batch_id"],
                    "batch_sha": proof["batch_sha"],
                    "target_contains_batch_sha": proof["target_contains_batch_sha"],
                },
            }
        )

    facts = {
        "tickets": [
            {
                "ticket_key": ticket_key,
                "assurance": _root_assurance(ticket_key),
            }
            for ticket_key in _ROOT_TICKET_KEYS
        ],
        "campaign": {"campaign_key": handle.campaign_key},
        "plan_revision": {"digest": final.plan_revision_digest},
        "concurrency": {
            "worker_slot_limit": initial_after_advance.worker_slots["limit"],
            "max_held": max(
                snapshot["readback"]["worker_slots"]["held"]
                for snapshot in transcript
                if snapshot["operation"] == "inspect"
            ),
            "work_run_count": len(work_runs),
        },
        "exclusive_resources": {
            run["ticket_key"]: run["exclusive_resources"] for run in work_runs
        },
        "work_runs": work_runs,
        "candidate_gate": {
            "reviewed": list(_ROOT_TICKET_KEYS),
            "repair_required": [
                event["ticket_key"]
                for event in gate_events
                if event["review"] == "repair_required"
            ],
            "rejected": [
                event["ticket_key"]
                for event in gate_events
                if event["review"] == "rejected"
            ],
            "strict_specialist_review": [
                event["ticket_key"]
                for event in gate_events
                if event.get("specialist_review") == "accepted"
            ],
            "accepted": list(_ROOT_TICKET_KEYS),
            "events": gate_events,
        },
        "batches": _root_batch_facts(readback, work_runs),
        "git_readback": readback["git_readback"],
        "readback": readback,
    }
    record = {
        "schema_version": "gwo.v8.local-root-canary.v1",
        "gate": LOCAL_ROOT_CANARY_GO,
        "scenario": "root",
        "run_id": run_id,
        "status": final.status.value,
        "public_status": final.status.value,
        "reason": final.reason,
        "public_reason": final.reason,
        "campaign": {
            "repository": handle.repository,
            "campaign_key": handle.campaign_key,
        },
        "facts": facts,
        "transcript": transcript,
        "replay": replay,
        "failure": None,
    }
    return _record_digest(record)


def _run_local_acceptance_in_root(
    *,
    root: Path,
    run_id: str = "phase1-single",
    scenario: str = "single",
) -> dict[str, Any]:
    """Run one local acceptance scenario and return its JSON record."""

    if type(run_id) is not str or not run_id:
        raise ValueError("run_id must be non-empty text")
    if scenario == "root":
        return _run_root_acceptance_in_root(root=Path(root), run_id=run_id)
    if scenario not in {"single", "wait", "blocked", "failure"}:
        raise ValueError("scenario must be single, wait, blocked, or failure")

    harness, handle = _install_harness(Path(root), run_id, scenario)
    initial = gwo_v8.inspect(handle)
    transcript: list[dict[str, Any]] = [
        {"operation": "start", "campaign_key": handle.campaign_key},
        {"operation": "inspect", "readback": _diagnostics_record(initial)},
    ]
    def advance_with_readback(wake_ref: str) -> tuple[dict[str, str], dict[str, str] | None]:
        try:
            outcome = gwo_v8.advance(handle, wake_ref)
        except LocalAcceptanceFailure as error:
            failure_record = {"type": type(error).__name__, "message": str(error)}
            return {"status": "Failure", **failure_record}, failure_record
        return _outcome_record(outcome), None

    def inspect_with_readback() -> object:
        diagnostics = gwo_v8.inspect(handle)
        transcript.append(
            {"operation": "inspect", "readback": _diagnostics_record(diagnostics)}
        )
        return diagnostics

    first_outcome, failure = advance_with_readback("local:initial")
    transcript.append(
        {
            "operation": "advance",
            "wake_ref": "local:initial",
            "outcome": first_outcome,
        }
    )
    inspect_with_readback()

    same_wake_ref = "local:initial" if failure is None else "local:replay-before-restart"
    same_wake, _same_wake_failure = advance_with_readback(same_wake_ref)
    transcript.append(
        {
            "operation": "advance",
            "wake_ref": same_wake_ref,
            "outcome": same_wake,
        }
    )
    same_wake_inspect = inspect_with_readback()
    before_restart_readback = _canonical_readback(harness.effects)
    replay: dict[str, Any] = {
        "same_wake": same_wake,
        "same_wake_ref": same_wake_ref,
        "same_wake_inspect": _diagnostics_record(same_wake_inspect),
        "semantic_execute_calls_before_restart": harness.effects.execute_calls,
        "delivery_execute_calls_before_restart": harness.delivery.execute_calls,
        "readback_digest_before_restart": digest_value(before_restart_readback),
    }

    _install_restart(harness)
    restarted = inspect_with_readback()
    replay["restart_inspect"] = _diagnostics_record(restarted)

    restart_outcome, _restart_failure = advance_with_readback("local:restart")
    replay["restart_advance"] = restart_outcome
    restarted_after_advance = inspect_with_readback()
    replay["restart_advance_inspect"] = _diagnostics_record(restarted_after_advance)

    repeated_restart_ref = (
        "local:restart" if failure is None else "local:replay-after-restart"
    )
    repeated_restart, _repeated_restart_failure = advance_with_readback(
        repeated_restart_ref
    )
    replay["repeated_restart_advance_ref"] = repeated_restart_ref
    replay["repeated_restart_advance"] = repeated_restart
    final = inspect_with_readback()
    replay["final_inspect"] = _diagnostics_record(final)
    after_replay_readback = _canonical_readback(harness.effects)
    replay["semantic_execute_calls_after_replay"] = harness.effects.execute_calls
    replay["delivery_execute_calls_after_replay"] = harness.delivery.execute_calls
    replay["readback_digest_after_replay"] = digest_value(after_replay_readback)
    replay["readback_unchanged"] = before_restart_readback == after_replay_readback
    replay["idempotent_delivery"] = (
        replay["delivery_execute_calls_before_restart"]
        == replay["delivery_execute_calls_after_replay"]
    )
    replay["idempotent_effects"] = (
        replay["semantic_execute_calls_before_restart"]
        == replay["semantic_execute_calls_after_replay"]
        and replay["idempotent_delivery"]
    )

    run = final.work_runs[0] if final.work_runs else None
    status = "Failure" if failure is not None else final.status.value
    record = {
        "schema_version": "gwo.v8.local-acceptance.v1",
        "gate": PUBLIC_API_SINGLE_NODE_GO,
        "scenario": scenario,
        "run_id": run_id,
        "status": status,
        "public_status": final.status.value,
        "reason": failure["message"] if failure is not None else final.reason,
        "public_reason": final.reason,
        "campaign": {
            "repository": handle.repository,
            "campaign_key": handle.campaign_key,
        },
        "facts": {
            "ticket": {"key": _TICKET_KEY, "ready_ref": _TICKET_KEY},
            "campaign": {"campaign_key": handle.campaign_key},
            "plan_revision": {"digest": final.plan_revision_digest},
            "worker": (
                {
                    "work_run_key": run.work_run_key,
                    "phase": run.phase,
                }
                if run is not None
                else None
            ),
            "candidate_gate": (
                {
                    "candidate_identity": run.candidate_identity,
                    "accepted_candidate_receipt_digest": run.accepted_candidate_receipt_digest,
                    "candidate_diff_record_digest": run.candidate_diff_record_digest,
                }
                if run is not None
                else None
            ),
            "review": (
                {
                    "status": "accepted",
                    "evidence_digests": list(run.evidence_digests),
                }
                if run is not None and run.evidence_digests
                else None
            ),
            "batch": (
                {"delivery_receipt_digest": run.delivery_receipt_digest}
                if run is not None and run.delivery_receipt_digest
                else None
            ),
            "result": (
                {"result_digest": run.result_digest}
                if run is not None and run.result_digest
                else None
            ),
            "evidence": (
                {"digests": list(run.evidence_digests)}
                if run is not None and run.evidence_digests
                else None
            ),
            "readback": after_replay_readback,
        },
        "transcript": transcript,
        "replay": replay,
        "failure": failure,
    }
    return _record_digest(record)


def run_local_acceptance(
    *,
    root: Path,
    run_id: str = "phase1-single",
    scenario: str = "single",
) -> dict[str, Any]:
    """Run one isolated local acceptance scenario and return its JSON record."""

    caller_root = Path(root).resolve()
    caller_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix="gwo-v8-local-run-",
        dir=str(caller_root),
    ) as isolated_root:
        try:
            return _run_local_acceptance_in_root(
                root=Path(isolated_root),
                run_id=run_id,
                scenario=scenario,
            )
        finally:
            gc.collect()


def canonical_json(record: dict[str, Any]) -> str:
    """Render one acceptance record as canonical JSON plus no extra spacing."""

    return canonical_bytes(record).decode("utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, help="temporary acceptance root")
    parser.add_argument("--run-id", default="phase1-single")
    parser.add_argument(
        "--scenario",
        choices=("single", "wait", "blocked", "failure", "root"),
        default="single",
    )
    args = parser.parse_args(argv)
    if args.root is None:
        import tempfile

        with tempfile.TemporaryDirectory(prefix="gwo-v8-local-") as temporary:
            record = run_local_acceptance(
                root=Path(temporary),
                run_id=args.run_id,
                scenario=args.scenario,
            )
            gc.collect()
    else:
        record = run_local_acceptance(
            root=args.root,
            run_id=args.run_id,
            scenario=args.scenario,
        )
    sys.stdout.write(canonical_json(record) + "\n")
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised by the CLI gate.
    raise SystemExit(main())
