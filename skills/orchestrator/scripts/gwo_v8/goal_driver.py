"""Host-side Goal continuation without polling or Agent-resident loops."""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
import sqlite3
from typing import Protocol

from ._canonical import canonical_bytes, digest_bytes, digest_value
from .kernel import ReconcileOutcome
from .runtime import (
    PaseoClient,
    PaseoCreateRequest,
    RuntimeProfile,
    RuntimePrompt,
)


class GoalDriverError(RuntimeError):
    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class GoalSnapshot:
    repository: str
    goal_key: str
    objective: str
    acceptance: tuple[str, ...]
    plan_digest: str
    work_items: tuple[tuple[str, str], ...]
    decision_inputs: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class CoordinatorTurnObservation:
    goal_key: str
    semantic_input_digest: str
    session_id: str
    outcome: str
    durable_reference: str
    token_use: int | None = None
    tool_calls: int | None = None
    agent_liveness: str | None = None


@dataclass(frozen=True)
class CoordinatorSession:
    session_id: str
    agent_id: str
    usable: bool
    manually_created: bool
    runtime_profile: str


@dataclass(frozen=True)
class GoalDirective:
    kind: str
    goal_key: str
    semantic_input_digest: str
    session_id: str | None = None
    wait_condition: str | None = None
    decision_gate: str | None = None
    corrective: bool = False
    runtime_profile: str | None = None


@dataclass(frozen=True)
class GoalDriverStatus:
    repository: str
    goal_key: str
    semantic_input_digest: str
    zero_outcomes: int
    continuation_outstanding: bool
    session_id: str | None
    last_observation_ref: str | None
    wait_condition: str | None
    last_wake_reference: str | None


class Reconciler(Protocol):
    def reconcile_once(self, repository: str) -> ReconcileOutcome: ...


class CoordinatorRuntime(Protocol):
    def find_manual(self, goal_key: str) -> CoordinatorSession | None: ...

    def inspect(self, session_id: str) -> CoordinatorSession | None: ...

    def continue_session(
        self,
        session: CoordinatorSession,
        snapshot: GoalSnapshot,
        *,
        corrective: bool,
    ) -> None: ...

    def create_auto(
        self,
        snapshot: GoalSnapshot,
        profile: RuntimeProfile,
    ) -> CoordinatorSession: ...


class InMemoryCoordinatorRuntime:
    """Explicit Goal Driver contract fake; it does not infer progress."""

    def __init__(self, *, manual_session: CoordinatorSession | None = None):
        self._sessions: dict[str, CoordinatorSession] = {}
        self._manual_session_id: str | None = None
        if manual_session is not None:
            self._sessions[manual_session.session_id] = manual_session
            self._manual_session_id = manual_session.session_id
        self.continue_count = 0
        self.auto_create_count = 0
        self.auto_profiles: list[RuntimeProfile] = []

    def find_manual(self, goal_key: str) -> CoordinatorSession | None:
        del goal_key
        if self._manual_session_id is None:
            return None
        session = self._sessions[self._manual_session_id]
        return session if session.usable else None

    def inspect(self, session_id: str) -> CoordinatorSession | None:
        return self._sessions.get(session_id)

    def continue_session(
        self,
        session: CoordinatorSession,
        snapshot: GoalSnapshot,
        *,
        corrective: bool,
    ) -> None:
        del snapshot, corrective
        current = self.inspect(session.session_id)
        if current is None or not current.usable:
            raise GoalDriverError(
                "COORDINATOR_UNUSABLE",
                "Coordinator session cannot be continued",
            )
        self.continue_count += 1

    def create_auto(
        self,
        snapshot: GoalSnapshot,
        profile: RuntimeProfile,
    ) -> CoordinatorSession:
        self.auto_create_count += 1
        self.continue_count += 1
        self.auto_profiles.append(profile)
        suffix = digest_value(
            {
                "goal_key": snapshot.goal_key,
                "profile": profile.digest,
                "ordinal": self.auto_create_count,
            }
        )[:20]
        session = CoordinatorSession(
            session_id=f"coordinator-session:{suffix}",
            agent_id=f"coordinator-agent:{suffix}",
            usable=True,
            manually_created=False,
            runtime_profile=profile.name,
        )
        self._sessions[session.session_id] = session
        return session


class PaseoCoordinatorRuntime:
    """Production Coordinator continuation over the Paseo client boundary."""

    def __init__(
        self,
        client: PaseoClient,
        *,
        repository_path: Path,
        base_sha: str,
        manual_agent_id: str | None = None,
        parent_agent_id: str | None = None,
    ):
        self.client = client
        self.repository_path = Path(repository_path).resolve()
        self.base_sha = base_sha
        self.manual_agent_id = manual_agent_id
        self.parent_agent_id = parent_agent_id

    @staticmethod
    def _session(agent, *, manually_created: bool) -> CoordinatorSession:
        usable = not agent.archived and agent.lifecycle not in {
            "error",
            "failed",
            "closed",
            "archived",
        }
        return CoordinatorSession(
            session_id=agent.session_id,
            agent_id=agent.agent_id,
            usable=usable,
            manually_created=manually_created,
            runtime_profile=agent.labels.get("gwo.runtime_profile", "manual-runtime"),
        )

    def find_manual(self, goal_key: str) -> CoordinatorSession | None:
        if self.manual_agent_id is not None:
            agent = self.client.inspect(self.manual_agent_id)
            session = self._session(agent, manually_created=True)
            return session if session.usable else None
        matches = self.client.find_by_labels(
            {
                "gwo.goal": goal_key,
                "gwo.role": "coordinator",
                "gwo.auto": "false",
            }
        )
        usable = [
            self._session(agent, manually_created=True)
            for agent in matches
            if self._session(agent, manually_created=True).usable
        ]
        if len(usable) > 1:
            raise GoalDriverError(
                "COORDINATOR_IDENTITY_AMBIGUOUS",
                "multiple manual Coordinators match one Goal",
            )
        return None if not usable else usable[0]

    def inspect(self, session_id: str) -> CoordinatorSession | None:
        matches = [
            agent
            for agent in self.client.find_by_labels({"gwo.role": "coordinator"})
            if agent.session_id == session_id
        ]
        if len(matches) > 1:
            raise GoalDriverError(
                "COORDINATOR_IDENTITY_AMBIGUOUS",
                "multiple Coordinators share one session identity",
            )
        if not matches:
            return None
        return self._session(
            matches[0],
            manually_created=matches[0].labels.get("gwo.auto") != "true",
        )

    @staticmethod
    def _prompt(snapshot: GoalSnapshot, *, corrective: bool) -> RuntimePrompt:
        text = canonical_bytes(
            {
                "goal": {
                    "goal_key": snapshot.goal_key,
                    "objective": snapshot.objective,
                    "acceptance": list(snapshot.acceptance),
                    "plan_digest": snapshot.plan_digest,
                    "work_items": [list(item) for item in snapshot.work_items],
                    "decision_inputs": [
                        list(item) for item in snapshot.decision_inputs
                    ],
                },
                "directive": (
                    "Produce one concrete Goal outcome. The previous turn "
                    "produced no executable work, Wait Condition, Decision Gate, "
                    "or completion proposal."
                    if corrective
                    else "Continue the incomplete Goal and produce one concrete outcome."
                ),
            }
        ).decode("utf-8")
        return RuntimePrompt(
            text=text,
            digest=digest_bytes(text.encode("utf-8")),
        )

    def continue_session(
        self,
        session: CoordinatorSession,
        snapshot: GoalSnapshot,
        *,
        corrective: bool,
    ) -> None:
        current = self.inspect(session.session_id)
        if current is None or not current.usable:
            raise GoalDriverError(
                "COORDINATOR_UNUSABLE",
                "Paseo Coordinator session cannot be continued",
            )
        prompt = self._prompt(snapshot, corrective=corrective)
        self.client.send_prompt(
            current.agent_id,
            prompt,
            action_key=f"{snapshot.goal_key}:{prompt.digest}",
        )

    def create_auto(
        self,
        snapshot: GoalSnapshot,
        profile: RuntimeProfile,
    ) -> CoordinatorSession:
        prompt = self._prompt(snapshot, corrective=False)
        agent = self.client.create(
            PaseoCreateRequest(
                action_key=f"{snapshot.goal_key}:coordinator:auto",
                title=f"GWO Coordinator {snapshot.goal_key}",
                labels={
                    "gwo.repository": snapshot.repository,
                    "gwo.goal": snapshot.goal_key,
                    "gwo.role": "coordinator",
                    "gwo.auto": "true",
                    "gwo.runtime_profile": profile.name,
                    "gwo.profile_digest": profile.digest,
                },
                prompt=prompt,
                repository_path=str(self.repository_path),
                base_sha=self.base_sha,
                profile=profile,
                parent_agent_id=self.parent_agent_id,
            )
        )
        readback = self.client.inspect(agent.agent_id)
        if (
            readback.session_id != agent.session_id
            or readback.profile_digest != profile.digest
            or readback.provider != profile.provider
            or readback.model != profile.model
            or readback.thinking != profile.thinking
            or readback.mode != profile.mode
            or readback.features != profile.features
            or readback.labels.get("gwo.goal") != snapshot.goal_key
        ):
            raise GoalDriverError(
                "COORDINATOR_READBACK_FAILED",
                "Paseo auto-Coordinator identity or runtime profile changed",
            )
        return self._session(readback, manually_created=False)


class GoalDriver:
    """Run one Kernel pass and return one bounded host continuation directive."""

    _OUTCOMES = {
        "zero_outcome",
        "executable_work",
        "wait_condition",
        "decision_gate",
        "completion_proposal",
    }

    def __init__(
        self,
        *,
        store_path: Path,
        reconciler: Reconciler,
        coordinators: CoordinatorRuntime,
        auto_profile: RuntimeProfile,
    ):
        self.store_path = Path(store_path)
        self.reconciler = reconciler
        self.coordinators = coordinators
        self.auto_profile = auto_profile
        if (
            auto_profile.provider != "kimi-cli"
            or auto_profile.model != "kimi-code/k3"
            or auto_profile.thinking != "max"
        ):
            raise GoalDriverError(
                "COORDINATOR_PROFILE_INVALID",
                "automatic Coordinator must use the configured Kimi K3 Max profile",
            )
        self.store_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS v8_goal_driver_state (
                    repository TEXT NOT NULL,
                    goal_key TEXT NOT NULL,
                    semantic_input_digest TEXT NOT NULL,
                    zero_outcomes INTEGER NOT NULL,
                    continuation_outstanding INTEGER NOT NULL,
                    session_id TEXT,
                    last_observation_ref TEXT,
                    wait_condition TEXT,
                    last_wake_reference TEXT,
                    PRIMARY KEY (repository, goal_key)
                )
                """
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.store_path)
        connection.row_factory = sqlite3.Row
        return connection

    @staticmethod
    def semantic_input_digest(snapshot: GoalSnapshot) -> str:
        return digest_value(
            {
                "repository": snapshot.repository,
                "goal_key": snapshot.goal_key,
                "objective": snapshot.objective,
                "acceptance": list(snapshot.acceptance),
                "plan_digest": snapshot.plan_digest,
                "work_items": [list(item) for item in snapshot.work_items],
                "decision_inputs": [
                    list(item) for item in snapshot.decision_inputs
                ],
            }
        )

    def _new_status(
        self,
        snapshot: GoalSnapshot,
        digest: str,
    ) -> GoalDriverStatus:
        return GoalDriverStatus(
            repository=snapshot.repository,
            goal_key=snapshot.goal_key,
            semantic_input_digest=digest,
            zero_outcomes=0,
            continuation_outstanding=False,
            session_id=None,
            last_observation_ref=None,
            wait_condition=None,
            last_wake_reference=None,
        )

    def read_status(
        self,
        repository: str,
        goal_key: str,
    ) -> GoalDriverStatus | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT *
                FROM v8_goal_driver_state
                WHERE repository = ? AND goal_key = ?
                """,
                (repository, goal_key),
            ).fetchone()
        if row is None:
            return None
        return GoalDriverStatus(
            repository=str(row["repository"]),
            goal_key=str(row["goal_key"]),
            semantic_input_digest=str(row["semantic_input_digest"]),
            zero_outcomes=int(row["zero_outcomes"]),
            continuation_outstanding=bool(row["continuation_outstanding"]),
            session_id=(
                None if row["session_id"] is None else str(row["session_id"])
            ),
            last_observation_ref=(
                None
                if row["last_observation_ref"] is None
                else str(row["last_observation_ref"])
            ),
            wait_condition=(
                None
                if row["wait_condition"] is None
                else str(row["wait_condition"])
            ),
            last_wake_reference=(
                None
                if row["last_wake_reference"] is None
                else str(row["last_wake_reference"])
            ),
        )

    def _write_status(self, status: GoalDriverStatus) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO v8_goal_driver_state (
                    repository,
                    goal_key,
                    semantic_input_digest,
                    zero_outcomes,
                    continuation_outstanding,
                    session_id,
                    last_observation_ref,
                    wait_condition,
                    last_wake_reference
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(repository, goal_key) DO UPDATE SET
                    semantic_input_digest = excluded.semantic_input_digest,
                    zero_outcomes = excluded.zero_outcomes,
                    continuation_outstanding = excluded.continuation_outstanding,
                    session_id = excluded.session_id,
                    last_observation_ref = excluded.last_observation_ref,
                    wait_condition = excluded.wait_condition,
                    last_wake_reference = excluded.last_wake_reference
                """,
                (
                    status.repository,
                    status.goal_key,
                    status.semantic_input_digest,
                    status.zero_outcomes,
                    int(status.continuation_outstanding),
                    status.session_id,
                    status.last_observation_ref,
                    status.wait_condition,
                    status.last_wake_reference,
                ),
            )

    def _apply_observation(
        self,
        status: GoalDriverStatus,
        observation: CoordinatorTurnObservation,
    ) -> GoalDriverStatus:
        if (
            observation.goal_key != status.goal_key
            or observation.semantic_input_digest
            != status.semantic_input_digest
            or observation.outcome not in self._OUTCOMES
            or not observation.durable_reference
            or status.session_id != observation.session_id
        ):
            raise GoalDriverError(
                "COORDINATOR_OBSERVATION_INVALID",
                "Coordinator Turn Observation does not match outstanding semantic input",
            )
        return GoalDriverStatus(
            repository=status.repository,
            goal_key=status.goal_key,
            semantic_input_digest=status.semantic_input_digest,
            zero_outcomes=(
                status.zero_outcomes + 1
                if observation.outcome == "zero_outcome"
                else 0
            ),
            continuation_outstanding=False,
            session_id=status.session_id,
            last_observation_ref=observation.durable_reference,
            wait_condition=status.wait_condition,
            last_wake_reference=status.last_wake_reference,
        )

    def _directive(
        self,
        kind: str,
        status: GoalDriverStatus,
        **values,
    ) -> GoalDirective:
        return GoalDirective(
            kind=kind,
            goal_key=status.goal_key,
            semantic_input_digest=status.semantic_input_digest,
            **values,
        )

    def _select_coordinator(
        self,
        snapshot: GoalSnapshot,
        status: GoalDriverStatus,
    ) -> tuple[CoordinatorSession, bool]:
        if status.session_id is not None:
            existing = self.coordinators.inspect(status.session_id)
            if existing is not None and existing.usable:
                return existing, False
        manual = self.coordinators.find_manual(snapshot.goal_key)
        if manual is not None and manual.usable:
            return manual, False
        return self.coordinators.create_auto(snapshot, self.auto_profile), True

    def run_once(
        self,
        snapshot: GoalSnapshot,
        *,
        observation: CoordinatorTurnObservation | None = None,
        wake_reference: str | None = None,
    ) -> GoalDirective:
        digest = self.semantic_input_digest(snapshot)
        status = self.read_status(snapshot.repository, snapshot.goal_key)
        if status is None or status.semantic_input_digest != digest:
            status = self._new_status(snapshot, digest)
        if status.wait_condition is not None:
            if (
                wake_reference is None
                or wake_reference == status.last_wake_reference
            ):
                return self._directive(
                    "wait",
                    status,
                    wait_condition=status.wait_condition,
                )
            status = replace(
                status,
                wait_condition=None,
                last_wake_reference=wake_reference,
            )
            self._write_status(status)
        if observation is not None:
            status = self._apply_observation(status, observation)
            self._write_status(status)

        outcome = self.reconciler.reconcile_once(snapshot.repository)
        if outcome.goal_key != snapshot.goal_key:
            raise GoalDriverError(
                "KERNEL_GOAL_MISMATCH",
                "Kernel reconciliation returned another Goal",
            )
        if outcome.directive == "goal_complete" and outcome.goal_state == "completed":
            status = GoalDriverStatus(
                **{
                    **status.__dict__,
                    "continuation_outstanding": False,
                }
            )
            self._write_status(status)
            return self._directive("finish", status)
        if outcome.status == "blocked" or outcome.directive == "request_decision":
            self._write_status(status)
            return self._directive(
                "decision",
                status,
                decision_gate="kernel_blocked",
            )
        if outcome.wait_condition is not None:
            status = replace(
                status,
                continuation_outstanding=False,
                wait_condition=outcome.wait_condition,
            )
            self._write_status(status)
            return self._directive(
                "wait",
                status,
                wait_condition=outcome.wait_condition,
            )
        if status.zero_outcomes >= 2:
            self._write_status(status)
            return self._directive(
                "decision",
                status,
                decision_gate="coordinator_zero_outcome",
            )
        if status.continuation_outstanding and observation is None:
            return self._directive(
                "wait",
                status,
                session_id=status.session_id,
                wait_condition="coordinator_turn",
            )

        session, created = self._select_coordinator(snapshot, status)
        corrective = status.zero_outcomes == 1
        if not created:
            self.coordinators.continue_session(
                session,
                snapshot,
                corrective=corrective,
            )
        status = GoalDriverStatus(
            repository=status.repository,
            goal_key=status.goal_key,
            semantic_input_digest=status.semantic_input_digest,
            zero_outcomes=status.zero_outcomes,
            continuation_outstanding=True,
            session_id=session.session_id,
            last_observation_ref=status.last_observation_ref,
            wait_condition=None,
            last_wake_reference=status.last_wake_reference,
        )
        self._write_status(status)
        return self._directive(
            "continue_coordinator",
            status,
            session_id=session.session_id,
            corrective=corrective,
            runtime_profile=session.runtime_profile,
        )
