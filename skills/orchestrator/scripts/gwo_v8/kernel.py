"""Deterministic V8 Kernel reconciliation over a private SQLite Store."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
import subprocess
from typing import Any

from .activation import LocalPlanPublication
from .evidence import EvidenceVerifier, TypedEvidence
from .runtime import (
    RuntimeAdapter,
    RuntimeAdapterError,
    RuntimeAdmission,
    RuntimeProfile,
    RuntimePrompt,
    SkillCatalog,
)


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
    admission_id: str
    admission_state: str
    attempt_id: str | None
    attempt_state: str | None
    candidate_sha: str | None
    result_digest: str | None
    materialization_executions: int
    wait_condition: str | None
    runtime_circuit: str | None = None
    wait_source_ref: str | None = None
    wait_event_identity: str | None = None
    next_check_at: str | None = None


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
    """Own one public convergence pass; no external call holds a Store transaction."""

    def __init__(
        self,
        *,
        store_path: Path,
        publication: LocalPlanPublication,
        runtime: RuntimeAdapter,
        verifier: EvidenceVerifier,
        repository_path: Path,
        integration_branch: str,
        writer_generation: str,
        runtime_profile: RuntimeProfile | None = None,
        parent_agent_id: str | None = None,
        skill_catalog: SkillCatalog | None = None,
    ):
        self.store_path = Path(store_path)
        self.publication = publication
        self.runtime = runtime
        self.verifier = verifier
        self.repository_path = Path(repository_path).resolve()
        self.integration_branch = integration_branch
        self.writer_generation = writer_generation
        self.runtime_profile = runtime_profile
        self.parent_agent_id = parent_agent_id
        self.skill_catalog = skill_catalog
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
                CREATE TABLE IF NOT EXISTS v8_admissions (
                    admission_id TEXT PRIMARY KEY,
                    repository TEXT NOT NULL,
                    plan_digest TEXT NOT NULL,
                    node_key TEXT NOT NULL,
                    goal_key TEXT NOT NULL,
                    state TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS v8_attempts (
                    attempt_id TEXT PRIMARY KEY,
                    repository TEXT NOT NULL,
                    plan_digest TEXT NOT NULL,
                    node_key TEXT NOT NULL,
                    admission_id TEXT NOT NULL,
                    state TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS v8_resource_claims (
                    repository TEXT NOT NULL,
                    resource_key TEXT NOT NULL,
                    admission_id TEXT,
                    attempt_id TEXT,
                    PRIMARY KEY (repository, resource_key)
                );
                CREATE TABLE IF NOT EXISTS v8_goal_holds (
                    repository TEXT NOT NULL,
                    goal_key TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    PRIMARY KEY (repository, goal_key)
                );
                CREATE TABLE IF NOT EXISTS v8_node_states (
                    repository TEXT NOT NULL,
                    plan_digest TEXT NOT NULL,
                    node_key TEXT NOT NULL,
                    state TEXT NOT NULL,
                    PRIMARY KEY (repository, plan_digest, node_key)
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
        rendered = self._render_state(state)
        with self._connect() as connection:
            self._upsert_state(
                connection,
                repository=repository,
                plan_digest=plan_digest,
                rendered=rendered,
            )

    @staticmethod
    def _render_state(state: dict[str, Any]) -> str:
        return json.dumps(
            state,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )

    @staticmethod
    def _upsert_state(
        connection: sqlite3.Connection,
        *,
        repository: str,
        plan_digest: str,
        rendered: str,
    ) -> None:
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

    def _commit_admission(
        self,
        state: dict[str, Any],
        work_node: dict[str, Any],
        *,
        activation_id: str,
        dependency_keys: tuple[str, ...] = (),
    ) -> None:
        claims = work_node.get("resource_claims") or []
        if not isinstance(claims, list) or any(
            not isinstance(claim, str) or not claim for claim in claims
        ):
            raise KernelError(
                "RESOURCE_CLAIMS_INVALID",
                "Plan Node Resource Claims are invalid",
            )
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            active = connection.execute(
                """
                SELECT plan_digest, writer_generation, activation_id
                FROM v8_active_plans
                WHERE repository = ?
                """,
                (state["repository"],),
            ).fetchone()
            pending = connection.execute(
                """
                SELECT 1
                FROM v8_pending_activations
                WHERE repository = ?
                """,
                (state["repository"],),
            ).fetchone()
            hold = connection.execute(
                """
                SELECT reason
                FROM v8_goal_holds
                WHERE repository = ? AND goal_key = ?
                """,
                (state["repository"], state["goal_key"]),
            ).fetchone()
            if (
                active is None
                or active["plan_digest"] != state["plan_digest"]
                or active["writer_generation"] != self.writer_generation
                or active["activation_id"] != activation_id
                or pending is not None
            ):
                raise KernelError(
                    "ADMISSION_PLAN_FENCED",
                    "active Plan or writer changed before Admission commit",
                )
            if hold is not None:
                raise KernelError(
                    "GOAL_ON_REPLAN_HOLD",
                    str(hold["reason"]),
                )
            for dependency_key in dependency_keys:
                predecessor = connection.execute(
                    """
                    SELECT state FROM v8_node_states
                    WHERE repository = ? AND plan_digest = ? AND node_key = ?
                    """,
                    (
                        state["repository"],
                        state["plan_digest"],
                        dependency_key,
                    ),
                ).fetchone()
                if predecessor is None or predecessor["state"] not in {
                    "verified",
                    "integrated",
                    "complete",
                }:
                    raise KernelError(
                        "ADMISSION_DEPENDENCY_UNSATISFIED",
                        f"Plan Node dependency is not satisfied: {dependency_key}",
                    )
            existing = connection.execute(
                """
                SELECT admission_id, state
                FROM v8_admissions
                WHERE repository = ? AND node_key = ?
                  AND state NOT IN ('consumed', 'abandoned')
                """,
                (state["repository"], state["node_key"]),
            ).fetchone()
            attempt = connection.execute(
                """
                SELECT attempt_id
                FROM v8_attempts
                WHERE repository = ? AND node_key = ?
                  AND state NOT IN ('verified', 'terminal')
                """,
                (state["repository"], state["node_key"]),
            ).fetchone()
            if existing is not None or attempt is not None:
                raise KernelError(
                    "ADMISSION_ALREADY_EXISTS",
                    "Plan Node already has non-terminal execution",
                )
            for claim in sorted(set(claims)):
                occupied = connection.execute(
                    """
                    SELECT admission_id, attempt_id
                    FROM v8_resource_claims
                    WHERE repository = ? AND resource_key = ?
                    """,
                    (state["repository"], claim),
                ).fetchone()
                if occupied is not None:
                    raise KernelError(
                        "RESOURCE_CLAIM_UNAVAILABLE",
                        f"Resource Claim is already reserved: {claim}",
                    )
            connection.execute(
                """
                INSERT INTO v8_admissions (
                    admission_id,
                    repository,
                    plan_digest,
                    node_key,
                    goal_key,
                    state
                ) VALUES (?, ?, ?, ?, ?, 'materializing')
                """,
                (
                    state["admission_id"],
                    state["repository"],
                    state["plan_digest"],
                    state["node_key"],
                    state["goal_key"],
                ),
            )
            connection.execute(
                """
                INSERT INTO v8_node_states (
                    repository, plan_digest, node_key, state
                ) VALUES (?, ?, ?, 'materializing')
                ON CONFLICT(repository, plan_digest, node_key) DO UPDATE SET
                    state = excluded.state
                """,
                (
                    state["repository"],
                    state["plan_digest"],
                    state["node_key"],
                ),
            )
            for claim in sorted(set(claims)):
                connection.execute(
                    """
                    INSERT INTO v8_resource_claims (
                        repository,
                        resource_key,
                        admission_id,
                        attempt_id
                    ) VALUES (?, ?, ?, NULL)
                    """,
                    (state["repository"], claim, state["admission_id"]),
                )
            self._upsert_state(
                connection,
                repository=state["repository"],
                plan_digest=state["plan_digest"],
                rendered=self._render_state(state),
            )

    def _commit_attempt(
        self,
        state: dict[str, Any],
        *,
        attempt_id: str,
    ) -> None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            active = connection.execute(
                """
                SELECT plan_digest, writer_generation, activation_id
                FROM v8_active_plans
                WHERE repository = ?
                """,
                (state["repository"],),
            ).fetchone()
            pending = connection.execute(
                """
                SELECT 1 FROM v8_pending_activations WHERE repository = ?
                """,
                (state["repository"],),
            ).fetchone()
            admission = connection.execute(
                """
                SELECT state FROM v8_admissions
                WHERE admission_id = ? AND repository = ? AND plan_digest = ?
                """,
                (
                    state["admission_id"],
                    state["repository"],
                    state["plan_digest"],
                ),
            ).fetchone()
            if (
                active is None
                or active["plan_digest"] != state["plan_digest"]
                or active["writer_generation"] != self.writer_generation
                or active["activation_id"] != state["activation_id"]
                or pending is not None
            ):
                raise KernelError(
                    "ATTEMPT_PLAN_FENCED",
                    "active Plan or writer changed before Attempt commit",
                )
            if admission is None or admission["state"] != "materializing":
                raise KernelError(
                    "ADMISSION_NOT_CONSUMABLE",
                    "Admission is not available for one atomic Attempt transition",
                )
            connection.execute(
                """
                INSERT INTO v8_attempts (
                    attempt_id, repository, plan_digest, node_key,
                    admission_id, state
                ) VALUES (?, ?, ?, ?, ?, 'running')
                """,
                (
                    attempt_id,
                    state["repository"],
                    state["plan_digest"],
                    state["node_key"],
                    state["admission_id"],
                ),
            )
            connection.execute(
                """
                UPDATE v8_admissions SET state = 'consumed'
                WHERE admission_id = ? AND state = 'materializing'
                """,
                (state["admission_id"],),
            )
            connection.execute(
                """
                UPDATE v8_node_states SET state = 'running'
                WHERE repository = ? AND plan_digest = ? AND node_key = ?
                """,
                (
                    state["repository"],
                    state["plan_digest"],
                    state["node_key"],
                ),
            )
            connection.execute(
                """
                UPDATE v8_resource_claims
                SET admission_id = NULL, attempt_id = ?
                WHERE repository = ? AND admission_id = ?
                """,
                (attempt_id, state["repository"], state["admission_id"]),
            )
            self._upsert_state(
                connection,
                repository=state["repository"],
                plan_digest=state["plan_digest"],
                rendered=self._render_state(state),
            )

    def _release_attempt_claims(self, state: dict[str, Any]) -> None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                UPDATE v8_attempts SET state = 'verified'
                WHERE attempt_id = ? AND state = 'running'
                """,
                (state["attempt_id"],),
            )
            connection.execute(
                """
                DELETE FROM v8_resource_claims
                WHERE repository = ? AND attempt_id = ?
                """,
                (state["repository"], state["attempt_id"]),
            )
            connection.execute(
                """
                UPDATE v8_node_states SET state = 'verified'
                WHERE repository = ? AND plan_digest = ? AND node_key = ?
                """,
                (
                    state["repository"],
                    state["plan_digest"],
                    state["node_key"],
                ),
            )
            self._upsert_state(
                connection,
                repository=state["repository"],
                plan_digest=state["plan_digest"],
                rendered=self._render_state(state),
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

    @staticmethod
    def _prompt_from_state(state: dict[str, Any]) -> RuntimePrompt:
        snapshot = state.get("prompt_snapshot")
        if not isinstance(snapshot, dict):
            raise KernelError(
                "PROMPT_SNAPSHOT_MISSING",
                "Admission has no frozen Prompt snapshot",
            )
        return RuntimePrompt(
            text=str(snapshot["text"]),
            digest=str(snapshot["digest"]),
            authority_digest=snapshot.get("authority_digest"),
            skill_name=snapshot.get("skill_name"),
            skill_digest=snapshot.get("skill_digest"),
            warnings=tuple(snapshot.get("warnings") or ()),
        )

    def _materialization_failure(
        self,
        state: dict[str, Any],
        error: RuntimeAdapterError,
        *,
        operation: str,
    ) -> ReconcileOutcome:
        actions = state.setdefault("materialization_actions", {})
        executions = int(actions.get(operation, 0))
        circuit_key = f"{self.runtime.adapter_name}:{operation}:{error.failure_class}"
        circuits = state.setdefault("runtime_circuits", {})
        previous = circuits.get(circuit_key)
        consecutive = (
            int(previous.get("consecutive_failures", 0)) + 1
            if isinstance(previous, dict)
            else 1
        )
        next_check_at = (
            datetime.now(timezone.utc) + timedelta(seconds=30)
        ).isoformat()
        if error.failure_class == "ambiguous":
            state.update(
                {
                    "status": "waiting",
                    "directive": "wait_for_runtime_readback",
                    "admission_state": "materialization_ambiguous",
                    "wait_condition": "runtime_identity_readback",
                    "wait_source_ref": (
                        f"{self.runtime.adapter_name}://"
                        f"admission/{state['admission_id']}"
                    ),
                    "wait_event_identity": f"{operation}:identity_readback",
                    "next_check_at": next_check_at,
                }
            )
        elif (
            error.failure_class == "transient"
            and consecutive < 3
            and (operation not in {"create", "prompt"} or executions < 3)
        ):
            opened = consecutive >= 2
            circuit_state = {
                "key": circuit_key,
                "state": "open" if opened else "closed",
                "consecutive_failures": consecutive,
                "probe_executed": bool(
                    isinstance(previous, dict)
                    and previous.get("probe_executed")
                ),
            }
            circuits[circuit_key] = circuit_state
            state.update(
                {
                    "status": "waiting",
                    "directive": (
                        "wait_for_runtime_circuit"
                        if opened
                        else "retry_materialization"
                    ),
                    "admission_state": "materialization_retry",
                    "wait_condition": (
                        "runtime_circuit_probe"
                        if opened
                        else "runtime_retry_due"
                    ),
                    "runtime_circuit": circuit_key,
                    "runtime_circuit_state": circuit_state,
                    "wait_source_ref": (
                        f"{self.runtime.adapter_name}://"
                        f"admission/{state['admission_id']}"
                    ),
                    "wait_event_identity": f"{operation}:retry",
                    "next_check_at": next_check_at,
                }
            )
        else:
            circuit_state = {
                "key": circuit_key,
                "state": "open",
                "consecutive_failures": consecutive,
                "probe_executed": True,
            }
            circuits[circuit_key] = circuit_state
            state.update(
                {
                    "status": "blocked",
                    "directive": "request_decision",
                    "admission_state": "materialization_blocked",
                    "wait_condition": None,
                    "runtime_circuit": circuit_key,
                    "runtime_circuit_state": circuit_state,
                    "wait_source_ref": None,
                    "wait_event_identity": None,
                    "next_check_at": None,
                }
            )
        state["last_runtime_error"] = {
            "code": error.code,
            "failure_class": error.failure_class,
        }
        self._write_state(state["repository"], state["plan_digest"], state)
        return self._outcome(state)

    def _prepare_runtime_operation(
        self,
        state: dict[str, Any],
        operation: str,
    ) -> ReconcileOutcome | None:
        prefix = f"{self.runtime.adapter_name}:{operation}:"
        circuits = state.setdefault("runtime_circuits", {})
        for key, circuit in tuple(circuits.items()):
            if (
                not key.startswith(prefix)
                or not isinstance(circuit, dict)
                or circuit.get("state") != "open"
            ):
                continue
            if circuit.get("probe_executed"):
                return self._materialization_failure(
                    state,
                    RuntimeAdapterError(
                        "RUNTIME_CIRCUIT_PROBE_EXHAUSTED",
                        f"the single {operation} circuit probe was already used",
                    ),
                    operation=operation,
                )
            updated = {
                **circuit,
                "state": "half_open",
                "probe_executed": True,
            }
            circuits[key] = updated
            state["runtime_circuit"] = key
            state["runtime_circuit_state"] = updated
            self._write_state(
                state["repository"],
                state["plan_digest"],
                state,
            )
        return None

    def _clear_runtime_operation(
        self,
        state: dict[str, Any],
        operation: str,
    ) -> None:
        prefix = f"{self.runtime.adapter_name}:{operation}:"
        circuits = state.setdefault("runtime_circuits", {})
        for key in tuple(circuits):
            if key.startswith(prefix):
                del circuits[key]
        if str(state.get("runtime_circuit") or "").startswith(prefix):
            state["runtime_circuit"] = None
            state["runtime_circuit_state"] = None

    def _initial_state(
        self,
        *,
        repository: str,
        plan_digest: str,
        goal: dict[str, Any],
        work_item: dict[str, Any],
        work_node: dict[str, Any],
    ) -> dict[str, Any]:
        prompt = RuntimePrompt.from_node(
            work_node,
            skill_catalog=self.skill_catalog,
        )
        return {
            "status": "running",
            "directive": "run_again",
            "repository": repository,
            "plan_digest": plan_digest,
            "goal_key": goal["goal_key"],
            "goal_state": "active",
            "work_item_key": work_item["work_item_key"],
            "work_item_state": "active",
            "node_key": work_node["node_key"],
            "admission_id": f"admission:{plan_digest[:20]}",
            "admission_state": "materializing",
            "attempt_id": None,
            "attempt_state": None,
            "candidate_sha": None,
            "result_digest": None,
            "materialization_executions": 0,
            "materialization_actions": {"create": 0, "prompt": 0},
            "wait_condition": None,
            "runtime_circuit": None,
            "runtime_circuit_state": None,
            "runtime_circuits": {},
            "wait_source_ref": None,
            "wait_event_identity": None,
            "next_check_at": None,
            "base_sha": _git(
                self.repository_path,
                "rev-parse",
                self.integration_branch,
            ),
            "prompt_snapshot": {
                "text": prompt.text,
                "digest": prompt.digest,
                "authority_digest": prompt.authority_digest,
                "skill_name": prompt.skill_name,
                "skill_digest": prompt.skill_digest,
                "warnings": list(prompt.warnings),
            },
            "resume_sent": False,
        }

    @staticmethod
    def _validate_plan(repository: str, canonical_bytes_value: bytes) -> dict[str, Any]:
        try:
            plan = json.loads(canonical_bytes_value)
        except json.JSONDecodeError as error:
            raise KernelError(
                "ACTIVE_PLAN_INVALID", "active Compiler bytes are not valid PlanSpec"
            ) from error
        if plan.get("schema_version") != 2 or plan.get("repository") != repository:
            raise KernelError(
                "ACTIVE_PLAN_INVALID", "active PlanSpec identity is invalid"
            )
        return plan

    @staticmethod
    def _nodes(
        plan: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
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
                "the walking skeleton requires one work and one integration Plan Node",
            )
        goal = (plan.get("goals") or [None])[0]
        work_item = (plan.get("work_items") or [None])[0]
        if not isinstance(goal, dict) or not isinstance(work_item, dict):
            raise KernelError("ACTIVE_PLAN_INVALID", "Goal or Work Item is missing")
        return work_nodes[0], integration_nodes[0], goal, work_item

    def _adopt_or_materialize(
        self,
        state: dict[str, Any],
        work_node: dict[str, Any],
    ):
        prompt = self._prompt_from_state(state)
        admission = RuntimeAdmission(
            repository=state["repository"],
            plan_digest=state["plan_digest"],
            node_key=work_node["node_key"],
            admission_id=state["admission_id"],
            repository_path=self.repository_path,
            base_sha=state["base_sha"],
            runtime_profile=self.runtime_profile,
            parent_agent_id=self.parent_agent_id,
        )
        circuit_outcome = self._prepare_runtime_operation(
            state,
            "read_binding",
        )
        if circuit_outcome is not None:
            return None, circuit_outcome
        try:
            binding = self.runtime.read_binding(admission, prompt)
        except RuntimeAdapterError as error:
            return None, self._materialization_failure(
                state,
                error,
                operation="read_binding",
            )
        self._clear_runtime_operation(state, "read_binding")

        if binding is None:
            if state["admission_state"] == "materialization_ambiguous":
                state.update(
                    {
                        "status": "waiting",
                        "directive": "wait_for_runtime_readback",
                        "wait_condition": "runtime_identity_readback",
                        "wait_source_ref": (
                            f"{self.runtime.adapter_name}://"
                            f"admission/{state['admission_id']}"
                        ),
                        "wait_event_identity": "create:identity_readback",
                        "next_check_at": (
                            datetime.now(timezone.utc) + timedelta(seconds=30)
                        ).isoformat(),
                    }
                )
                self._write_state(
                    state["repository"],
                    state["plan_digest"],
                    state,
                )
                return None, self._outcome(state)
            actions = state.setdefault("materialization_actions", {})
            if int(actions.get("create", 0)) >= 3:
                blocked = RuntimeAdapterError(
                    "MATERIALIZATION_RETRIES_EXHAUSTED",
                    "three unchanged Materialization executions were exhausted",
                )
                return None, self._materialization_failure(
                    state,
                    blocked,
                    operation="create",
                )
            circuit_outcome = self._prepare_runtime_operation(
                state,
                "create",
            )
            if circuit_outcome is not None:
                return None, circuit_outcome
            actions["create"] = int(actions.get("create", 0)) + 1
            state["materialization_executions"] = sum(
                int(value) for value in actions.values()
            )
            state.update(
                {
                    "status": "running",
                    "directive": "run_again",
                    "admission_state": "materializing",
                    "wait_condition": None,
                    "wait_source_ref": None,
                    "wait_event_identity": None,
                    "next_check_at": None,
                }
            )
            self._write_state(state["repository"], state["plan_digest"], state)
            try:
                self.runtime.materialize(admission, prompt)
                self._clear_runtime_operation(state, "create")
                binding = self.runtime.read_binding(admission, prompt)
            except RuntimeAdapterError as error:
                return None, self._materialization_failure(
                    state,
                    error,
                    operation="create",
                )
            if binding is None:
                ambiguous = RuntimeAdapterError(
                    "MATERIALIZATION_READBACK_MISSING",
                    "Runtime creation acknowledgement has no identity readback",
                    failure_class="ambiguous",
                )
                return None, self._materialization_failure(
                    state,
                    ambiguous,
                    operation="create",
                )

        if (
            binding.repository != admission.repository
            or binding.plan_digest != admission.plan_digest
            or binding.node_key != admission.node_key
            or binding.admission_id != admission.admission_id
        ):
            ambiguous = RuntimeAdapterError(
                "MATERIALIZATION_READBACK_FAILED",
                "Runtime Binding did not round-trip Admission identity",
                failure_class="ambiguous",
            )
            return None, self._materialization_failure(
                state,
                ambiguous,
                operation="read_binding",
            )

        if not binding.prompt_accepted:
            actions = state.setdefault("materialization_actions", {})
            if int(actions.get("prompt", 0)) >= 3:
                blocked = RuntimeAdapterError(
                    "PROMPT_DELIVERY_RETRIES_EXHAUSTED",
                    "three unchanged Prompt executions were exhausted",
                )
                return None, self._materialization_failure(
                    state,
                    blocked,
                    operation="prompt",
                )
            circuit_outcome = self._prepare_runtime_operation(
                state,
                "prompt",
            )
            if circuit_outcome is not None:
                return None, circuit_outcome
            actions["prompt"] = int(actions.get("prompt", 0)) + 1
            state["materialization_executions"] = sum(
                int(value) for value in actions.values()
            )
            self._write_state(state["repository"], state["plan_digest"], state)
            try:
                self.runtime.accept_prompt(binding, prompt)
                self._clear_runtime_operation(state, "prompt")
                binding = self.runtime.read_binding(admission, prompt)
            except RuntimeAdapterError as error:
                return None, self._materialization_failure(
                    state,
                    error,
                    operation="prompt",
                )
        if (
            binding is None
            or not binding.prompt_accepted
            or binding.prompt_digest != prompt.digest
        ):
            ambiguous = RuntimeAdapterError(
                "PROMPT_READBACK_FAILED",
                "Runtime did not confirm the exact frozen Prompt",
                failure_class="ambiguous",
            )
            return None, self._materialization_failure(
                state,
                ambiguous,
                operation="prompt",
            )
        state["runtime_circuit"] = None
        state["runtime_circuit_state"] = None
        state["wait_source_ref"] = None
        state["wait_event_identity"] = None
        state["next_check_at"] = None
        self._write_state(state["repository"], state["plan_digest"], state)
        return binding, None

    def _begin_or_adopt_attempt(
        self,
        state: dict[str, Any],
        binding,
    ):
        attempt_id = f"attempt:{state['plan_digest'][:20]}:1"
        if binding.attempt_id not in {None, attempt_id}:
            raise KernelError(
                "ATTEMPT_IDENTITY_MISMATCH",
                "Runtime Binding belongs to another Attempt",
            )
        if state.get("attempt_id") not in {None, attempt_id}:
            raise KernelError(
                "ATTEMPT_IDENTITY_MISMATCH",
                "Store Attempt identity changed",
            )
        if state.get("attempt_id") is None:
            state.update(
                {
                    "admission_state": "consumed",
                    "attempt_id": attempt_id,
                    "attempt_state": "running",
                    "status": "running",
                    "directive": "run_again",
                    "wait_condition": None,
                    "wait_source_ref": None,
                    "wait_event_identity": None,
                    "next_check_at": None,
                }
            )
            self._commit_attempt(state, attempt_id=attempt_id)
        if binding.attempt_id is None:
            binding = self.runtime.attach_attempt(binding, attempt_id)
        if binding is None or binding.attempt_id != attempt_id:
            raise KernelError(
                "ATTEMPT_READBACK_FAILED",
                "Runtime Binding did not round-trip the Attempt identity",
            )
        return binding

    def reconcile_once(self, repository: str) -> ReconcileOutcome:
        active = self.publication.read_active(repository)
        if active is None:
            raise KernelError("PLAN_NOT_ACTIVE", "repository has no active Plan Revision")
        if active.writer_generation != self.writer_generation:
            raise KernelError(
                "WRITER_GENERATION_MISMATCH",
                "Kernel does not own the active writer generation",
            )
        plan = self._validate_plan(repository, active.canonical_bytes)
        work_node, integration_node, goal, work_item = self._nodes(plan)

        state = self._read_state(repository, active.plan_digest)
        if state is not None and (
            state.get("status") == "complete"
            or state.get("admission_state") == "materialization_blocked"
        ):
            return self._outcome(state)
        if state is None:
            state = self._initial_state(
                repository=repository,
                plan_digest=active.plan_digest,
                goal=goal,
                work_item=work_item,
                work_node=work_node,
            )
            state["activation_id"] = active.activation_id
            self._commit_admission(
                state,
                work_node,
                activation_id=active.activation_id,
                dependency_keys=tuple(
                    str(edge["from_node"])
                    for edge in plan.get("edges") or ()
                    if isinstance(edge, dict)
                    and edge.get("to_node") == work_node["node_key"]
                    and isinstance(edge.get("from_node"), str)
                ),
            )
        elif (
            state.get("repository") != repository
            or state.get("plan_digest") != active.plan_digest
            or state.get("node_key") != work_node["node_key"]
        ):
            raise KernelError(
                "EXECUTION_STATE_IDENTITY_MISMATCH",
                "Store execution state does not match active Plan",
            )

        self.publication.assert_writer(
            repository=repository,
            writer_generation=self.writer_generation,
            plan_digest=active.plan_digest,
            activation_id=active.activation_id,
        )
        binding, terminal = self._adopt_or_materialize(state, work_node)
        if terminal is not None:
            return terminal
        assert binding is not None
        binding = self._begin_or_adopt_attempt(state, binding)

        try:
            observation = self.runtime.observe(binding)
            if (
                observation.result_claim is None
                and observation.lifecycle in {"idle", "ready"}
                and not state.get("resume_sent")
            ):
                state["resume_sent"] = True
                self._write_state(repository, active.plan_digest, state)
                self.runtime.resume(binding)
                observation = self.runtime.observe(binding)
        except RuntimeAdapterError as error:
            state.update(
                {
                    "status": "waiting",
                    "directive": "wait_for_runtime",
                    "attempt_state": "running",
                    "wait_condition": "runtime_observation",
                    "wait_source_ref": (
                        f"{self.runtime.adapter_name}://"
                        f"attempt/{state['attempt_id']}"
                    ),
                    "wait_event_identity": "runtime_observation",
                    "next_check_at": (
                        datetime.now(timezone.utc) + timedelta(seconds=30)
                    ).isoformat(),
                    "last_runtime_error": {
                        "code": error.code,
                        "failure_class": error.failure_class,
                    },
                }
            )
            self._write_state(repository, active.plan_digest, state)
            return self._outcome(state)

        if observation.result_claim is None:
            state.update(
                {
                    "status": "waiting",
                    "directive": "wait_for_runtime",
                    "attempt_state": "running",
                    "wait_condition": "runtime_result",
                    "wait_source_ref": (
                        f"{self.runtime.adapter_name}://"
                        f"attempt/{state['attempt_id']}"
                    ),
                    "wait_event_identity": "runtime_result",
                    "next_check_at": (
                        datetime.now(timezone.utc) + timedelta(seconds=30)
                    ).isoformat(),
                }
            )
            self._write_state(repository, active.plan_digest, state)
            return self._outcome(state)

        state.update(
            {
                "candidate_sha": observation.result_claim.candidate_sha,
                "attempt_state": "result_submitted",
                "wait_condition": None,
                "wait_source_ref": None,
                "wait_event_identity": None,
                "next_check_at": None,
            }
        )
        self._write_state(repository, active.plan_digest, state)
        decision = self.verifier.verify(
            observation.result_claim,
            work_node["output_contract"],
            observation,
        )
        if decision.status != "accepted" or decision.result is None:
            state.update(
                {
                    "status": decision.status,
                    "attempt_state": (
                        "result_submitted"
                        if decision.status == "waiting"
                        else "candidate_rejected"
                    ),
                    "directive": (
                        "wait_for_evidence"
                        if decision.status == "waiting"
                        else "invoke_coordinator"
                    ),
                    "wait_condition": (
                        "evidence_source"
                        if decision.status == "waiting"
                        else None
                    ),
                    "wait_source_ref": (
                        f"{self.runtime.adapter_name}://"
                        f"attempt/{state['attempt_id']}/evidence"
                        if decision.status == "waiting"
                        else None
                    ),
                    "wait_event_identity": (
                        f"evidence:{observation.result_claim.candidate_sha}"
                        if decision.status == "waiting"
                        else None
                    ),
                    "next_check_at": (
                        (
                            datetime.now(timezone.utc)
                            + timedelta(seconds=30)
                        ).isoformat()
                        if decision.status == "waiting"
                        else None
                    ),
                }
            )
            self._write_state(repository, active.plan_digest, state)
            return self._outcome(state)

        state["result_digest"] = decision.result.result_digest
        state["status"] = "verified"
        state["attempt_state"] = "verified"
        self._release_attempt_claims(state)

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
            if current_head != observation.result_claim.candidate_sha:
                _git(
                    self.repository_path,
                    "merge",
                    "--ff-only",
                    observation.result_claim.candidate_sha,
                )
            integrated_sha = _git(self.repository_path, "rev-parse", "HEAD")
            if integrated_sha != observation.result_claim.candidate_sha:
                raise KernelError(
                    "INTEGRATION_READBACK_FAILED",
                    "Integration branch did not reach the Candidate",
                )
            integration_evidence = TypedEvidence._capture(
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
                    "wait_condition": None,
                    "wait_source_ref": None,
                    "wait_event_identity": None,
                    "next_check_at": None,
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
