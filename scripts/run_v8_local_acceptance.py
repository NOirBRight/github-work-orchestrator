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
_REPOSITORY = "local/v8-acceptance"
_TICKET_KEY = "issue:1"
_TARGET_BRANCH = "main"


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


def _snapshot() -> dict[str, Any]:
    return {
        "repository": _REPOSITORY,
        "target_branch": _TARGET_BRANCH,
        "campaign_source": _campaign_source(),
        "policy": _policy(),
        "tickets": [_ticket()],
    }


class _LocalSnapshotSource:
    def snapshot(self, repository: str, ready_refs: tuple[str, ...]) -> dict[str, Any]:
        if repository != _REPOSITORY or ready_refs != (_TICKET_KEY,):
            raise AssertionError("local acceptance source identity changed")
        return _snapshot()


@dataclass
class _LocalPlanningGateway:
    artifacts: ArtifactStore
    calls: list[str]

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
        payload = {
            "admitted_work": [_TICKET_KEY],
            "dependency_additions": [],
            "exclusive_resources": {_TICKET_KEY: []},
            "capability_requirements": {
                _TICKET_KEY: ["git", "local_check"]
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


class _LocalDeliveryStub:
    def __init__(self, store_path: Path):
        self.store_path = Path(store_path)
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
        candidate = _candidate_for(action)
        accepted = _accepted_for(action, candidate)
        request_digest = action.batch_delivery_request_digest
        if request_digest is None:
            raise AssertionError("Batch delivery request digest was not bound")
        batch_id = digest_value(
            {"kind": "local-batch.v1", "request_digest": request_digest}
        )
        delivery = BatchDeliveryProof.create(
            delivery_stable_action_id=action.stable_action_id,
            delivery_request_digest=request_digest,
            batch_id=batch_id,
            batch_sha=candidate.candidate_commit_oid,
            member_ticket_keys=(action.ticket_key,),
            local_check_receipt_digest=digest_value(
                {"kind": "local-check.v1", "request_digest": request_digest}
            ),
            publication_receipt_digest=digest_value(
                {"kind": "local-publication.v1", "batch_id": batch_id}
            ),
            pull_request_number=1,
            pull_request_head_sha=candidate.candidate_commit_oid,
            hosted_result_receipt_digest=digest_value(
                {"kind": "local-hosted-readback.v1", "batch_id": batch_id}
            ),
            integration_lease_digest=digest_value(
                {"kind": "local-integration-lease.v1", "batch_id": batch_id}
            ),
            target_branch=_TARGET_BRANCH,
            target_head_sha="6" * 40,
            target_readback_digest=digest_value(
                {"kind": "local-target-readback.v1", "batch_id": batch_id}
            ),
            target_contains_batch_sha=True,
            pull_request_merge_target_sha="6" * 40,
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
            evidence_digests=accepted.evidence_digests,
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
    planning_gateway = _LocalPlanningGateway(artifacts, [])
    control = PlanControl(
        source=_LocalSnapshotSource(),
        artifacts=artifacts,
        gateway=planning_gateway,
        repository=InMemoryPlanRepository(writer_generation="writer:local"),
    )
    _install_start_host(
        _LocalStartHost(control, _campaign_key(run_id, scenario))
    )
    delivery = _LocalDeliveryStub(sqlite_root / "delivery.sqlite3")
    effects = _LocalEffects(
        sqlite_root / "effects.sqlite3",
        delivery,
        scenario,
    )
    configuration = ExecutionKernelConfiguration(
        host_worker_slots=1,
        repository_worker_slots={_REPOSITORY: 1},
        host_stale_after_seconds=1800,
        repository_stale_after_seconds={_REPOSITORY: 1800},
    )
    install_execution_kernel(
        store_path=sqlite_root / "execution.sqlite3",
        plan_control=control,
        effects=effects,
        configuration=configuration,
    )
    handle = gwo_v8.start(_REPOSITORY, (_TICKET_KEY,))
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
        ),
        handle,
    )


def _install_restart(harness: _Harness) -> None:
    effects = _LocalEffects(
        harness.root / "sqlite" / "effects.sqlite3",
        _LocalDeliveryStub(harness.root / "sqlite" / "delivery.sqlite3"),
        harness.scenario,
    )
    harness.effects = effects
    harness.delivery = effects.delivery
    install_execution_kernel(
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


def _diagnostics_record(diagnostics: object) -> dict[str, Any]:
    return {
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
            }
            for run in diagnostics.work_runs
        ],
        "outstanding_effect_ids": list(diagnostics.outstanding_effect_ids),
    }


def _record_digest(record: dict[str, Any]) -> dict[str, Any]:
    return {**record, "record_digest": digest_value(record)}


def _run_local_acceptance_in_root(
    *,
    root: Path,
    run_id: str = "phase1-single",
    scenario: str = "single",
) -> dict[str, Any]:
    """Run one local acceptance scenario and return its JSON record."""

    if type(run_id) is not str or not run_id:
        raise ValueError("run_id must be non-empty text")
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
        choices=("single", "wait", "blocked", "failure"),
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
