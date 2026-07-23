"""Deterministic Phase 1 Kernel over a private SQLite execution Store."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import subprocess
from typing import Any

from .activation import LocalPlanPublication
from .evidence import EvidenceVerifier, TypedEvidence
from .runtime import InMemoryRuntimeAdapter, RuntimeAdmission


class KernelError(RuntimeError):
    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class ReconcileOutcome:
    status: str
    directive: str
    repository: str
    plan_digest: str
    goal_key: str
    goal_state: str
    work_item_key: str
    work_item_state: str
    node_key: str
    attempt_id: str | None
    candidate_sha: str | None
    result_digest: str | None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _git(repository: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repository), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if result.returncode != 0:
        raise KernelError(
            "GIT_OPERATION_FAILED",
            result.stderr.strip() or result.stdout.strip() or "git failed",
        )
    return result.stdout.strip()


class Kernel:
    """Own one public reconciliation pass; Store handlers stay private."""

    def __init__(
        self,
        *,
        store_path: Path,
        publication: LocalPlanPublication,
        runtime: InMemoryRuntimeAdapter,
        verifier: EvidenceVerifier,
        repository_path: Path,
        integration_branch: str,
        writer_generation: str,
    ):
        self.store_path = Path(store_path)
        self.publication = publication
        self.runtime = runtime
        self.verifier = verifier
        self.repository_path = Path(repository_path).resolve()
        self.integration_branch = integration_branch
        self.writer_generation = writer_generation
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS v8_execution_state (
                    repository TEXT NOT NULL,
                    plan_digest TEXT NOT NULL,
                    state_json TEXT NOT NULL,
                    PRIMARY KEY (repository, plan_digest)
                );
                CREATE TABLE IF NOT EXISTS v8_integration_leases (
                    repository TEXT PRIMARY KEY,
                    holder TEXT NOT NULL
                );
                """
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.store_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _read_state(self, repository: str, plan_digest: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT state_json
                FROM v8_execution_state
                WHERE repository = ? AND plan_digest = ?
                """,
                (repository, plan_digest),
            ).fetchone()
        return None if row is None else json.loads(row["state_json"])

    def _write_state(
        self, repository: str, plan_digest: str, state: dict[str, Any]
    ) -> None:
        rendered = json.dumps(
            state,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO v8_execution_state (
                    repository,
                    plan_digest,
                    state_json
                ) VALUES (?, ?, ?)
                ON CONFLICT(repository, plan_digest) DO UPDATE SET
                    state_json = excluded.state_json
                """,
                (repository, plan_digest, rendered),
            )

    def _acquire_integration_lease(self, repository: str, holder: str) -> None:
        try:
            with self._connect() as connection:
                connection.execute(
                    """
                    INSERT INTO v8_integration_leases (repository, holder)
                    VALUES (?, ?)
                    """,
                    (repository, holder),
                )
        except sqlite3.IntegrityError as error:
            raise KernelError(
                "INTEGRATION_LEASE_UNAVAILABLE",
                "another integration owns the repository lease",
            ) from error

    def _release_integration_lease(self, repository: str, holder: str) -> None:
        with self._connect() as connection:
            deleted = connection.execute(
                """
                DELETE FROM v8_integration_leases
                WHERE repository = ? AND holder = ?
                """,
                (repository, holder),
            ).rowcount
        if deleted != 1:
            raise KernelError(
                "INTEGRATION_LEASE_LOST", "integration lease identity changed"
            )

    @staticmethod
    def _outcome(state: dict[str, Any]) -> ReconcileOutcome:
        return ReconcileOutcome(
            **{
                field: state.get(field)
                for field in ReconcileOutcome.__dataclass_fields__
            }
        )

    def reconcile_once(self, repository: str) -> ReconcileOutcome:
        active = self.publication.read_active(repository)
        if active is None:
            raise KernelError("PLAN_NOT_ACTIVE", "repository has no active Plan Revision")
        if active.writer_generation != self.writer_generation:
            raise KernelError(
                "WRITER_GENERATION_MISMATCH",
                "Kernel does not own the active writer generation",
            )

        existing = self._read_state(repository, active.plan_digest)
        if existing is not None and existing.get("status") == "complete":
            return self._outcome(existing)

        try:
            plan = json.loads(active.canonical_bytes)
        except json.JSONDecodeError as error:
            raise KernelError(
                "ACTIVE_PLAN_INVALID", "active Compiler bytes are not valid PlanSpec"
            ) from error
        if plan.get("schema_version") != 2 or plan.get("repository") != repository:
            raise KernelError(
                "ACTIVE_PLAN_INVALID", "active PlanSpec identity is invalid"
            )
        work_nodes = [
            node for node in plan.get("nodes") or [] if node.get("kind") == "work"
        ]
        integration_nodes = [
            node
            for node in plan.get("nodes") or []
            if node.get("kind") == "integration"
        ]
        if len(work_nodes) != 1 or len(integration_nodes) != 1:
            raise KernelError(
                "ACTIVE_PLAN_UNSUPPORTED",
                "Phase 1 requires one work and one integration Plan Node",
            )
        work_node = work_nodes[0]
        integration_node = integration_nodes[0]
        goal = (plan.get("goals") or [None])[0]
        work_item = (plan.get("work_items") or [None])[0]
        if not isinstance(goal, dict) or not isinstance(work_item, dict):
            raise KernelError("ACTIVE_PLAN_INVALID", "Goal or Work Item is missing")

        admission_id = f"admission:{active.plan_digest[:20]}"
        attempt_id = f"attempt:{active.plan_digest[:20]}:1"
        state = {
            "status": "running",
            "directive": "run_again",
            "repository": repository,
            "plan_digest": active.plan_digest,
            "goal_key": goal["goal_key"],
            "goal_state": "active",
            "work_item_key": work_item["work_item_key"],
            "work_item_state": "active",
            "node_key": work_node["node_key"],
            "attempt_id": attempt_id,
            "candidate_sha": None,
            "result_digest": None,
        }
        self._write_state(repository, active.plan_digest, state)

        base_sha = _git(self.repository_path, "rev-parse", self.integration_branch)
        admission = RuntimeAdmission(
            repository=repository,
            plan_digest=active.plan_digest,
            node_key=work_node["node_key"],
            admission_id=admission_id,
            attempt_id=attempt_id,
            repository_path=self.repository_path,
            base_sha=base_sha,
        )
        binding = self.runtime.materialize(admission)
        execution = self.runtime.execute(binding, work_node)
        decision = self.verifier.verify(
            execution.result_claim,
            work_node["output_contract"],
            execution.evidence,
        )
        state["candidate_sha"] = execution.result_claim.candidate_sha
        if decision.status != "accepted" or decision.result is None:
            state.update(
                {
                    "status": decision.status,
                    "directive": (
                        "wait_for_evidence"
                        if decision.status == "waiting"
                        else "invoke_coordinator"
                    ),
                }
            )
            self._write_state(repository, active.plan_digest, state)
            return self._outcome(state)

        state["result_digest"] = decision.result.result_digest
        state["status"] = "verified"
        self._write_state(repository, active.plan_digest, state)

        lease_holder = integration_node["node_key"]
        self._acquire_integration_lease(repository, lease_holder)
        try:
            current_branch = _git(
                self.repository_path, "rev-parse", "--abbrev-ref", "HEAD"
            )
            if current_branch != self.integration_branch:
                raise KernelError(
                    "INTEGRATION_BRANCH_MISMATCH",
                    "repository is not on the configured Integration branch",
                )
            current_head = _git(self.repository_path, "rev-parse", "HEAD")
            if current_head != execution.result_claim.candidate_sha:
                _git(
                    self.repository_path,
                    "merge",
                    "--ff-only",
                    execution.result_claim.candidate_sha,
                )
            integrated_sha = _git(self.repository_path, "rev-parse", "HEAD")
            if integrated_sha != execution.result_claim.candidate_sha:
                raise KernelError(
                    "INTEGRATION_READBACK_FAILED",
                    "Integration branch did not reach the Candidate",
                )
            integration_evidence = TypedEvidence.observe(
                kind="integration",
                subject=integrated_sha,
                observer_type="kernel",
                observer_id=self.writer_generation,
                observed_at=_now(),
                source_ref=f"git://{repository}/{self.integration_branch}",
                payload={
                    "integration_node": integration_node["node_key"],
                    "branch": self.integration_branch,
                    "head": integrated_sha,
                },
            )
            state.update(
                {
                    "status": "complete",
                    "directive": "goal_complete",
                    "goal_state": "completed",
                    "work_item_state": "integrated",
                    "integration_evidence_digest": (
                        integration_evidence.content_digest
                    ),
                }
            )
            self._write_state(repository, active.plan_digest, state)
        finally:
            self._release_integration_lease(repository, lease_holder)

        self.runtime.retire(binding)
        return self._outcome(state)
