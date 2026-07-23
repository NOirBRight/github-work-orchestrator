"""Host-side Goal continuation without polling or Agent-resident loops."""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from datetime import datetime, timedelta, timezone
import base64
import json
from pathlib import Path
import sqlite3
from typing import Any, Protocol

from ._canonical import canonical_bytes, digest_bytes, digest_value
from .activation import GitHubContentClient
from .kernel import ReconcileOutcome
from .runtime import (
    PaseoClient,
    PaseoCreateRequest,
    RuntimeProfile,
    RuntimePrompt,
    read_bounded_outcome,
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
    node_states: tuple[tuple[str, str], ...] = ()
    evidence_manifests: tuple[tuple[str, str], ...] = ()
    capability_configuration_digest: str | None = None
    base_identity: str | None = None


@dataclass(frozen=True)
class CoordinatorTurnObservation:
    goal_key: str
    semantic_input_digest: str
    session_id: str
    outcome: str
    durable_reference: str
    fact_reference: str | None = None
    fact_digest: str | None = None
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
    wait_source_ref: str | None = None
    wait_event_identity: str | None = None
    next_check_at: str | None = None
    decision_gate: str | None = None
    corrective: bool = False
    runtime_profile: str | None = None


@dataclass(frozen=True)
class GoalDriverStatus:
    repository: str
    goal_key: str
    semantic_input_digest: str
    zero_outcomes: int
    turn_sequence: int
    continuation_outstanding: bool
    session_id: str | None
    last_observation_ref: str | None
    wait_condition: str | None
    wait_source_ref: str | None
    wait_event_identity: str | None
    next_check_at: str | None
    last_wake_reference: str | None


@dataclass(frozen=True)
class DurableWake:
    goal_key: str
    semantic_input_digest: str
    wait_condition: str
    source_ref: str
    event_identity: str
    durable_reference: str


class DurableGoalControl(Protocol):
    """Read exact immutable Coordinator outcomes and wake events."""

    def read_observation(
        self,
        repository: str,
        reference: str,
    ) -> CoordinatorTurnObservation | None: ...

    def read_wake(
        self,
        repository: str,
        reference: str,
    ) -> DurableWake | None: ...

    def publish_observation(
        self,
        repository: str,
        observation: CoordinatorTurnObservation,
    ) -> None: ...

    def publish_wake(self, repository: str, wake: DurableWake) -> None: ...

    def publish_fact(
        self,
        repository: str,
        reference: str,
        content: bytes,
    ) -> str: ...

    def read_fact_digest(
        self,
        repository: str,
        reference: str,
    ) -> str | None: ...


class InMemoryDurableGoalControl:
    """Contract fake that still requires publish-then-readback semantics."""

    def __init__(self):
        self._observations: dict[
            tuple[str, str], CoordinatorTurnObservation
        ] = {}
        self._wakes: dict[tuple[str, str], DurableWake] = {}
        self._facts: dict[tuple[str, str], tuple[bytes, str]] = {}

    def publish_observation(
        self,
        repository: str,
        observation: CoordinatorTurnObservation,
    ) -> None:
        key = (repository, observation.durable_reference)
        existing = self._observations.get(key)
        if existing is not None and existing != observation:
            raise GoalDriverError(
                "DURABLE_OBSERVATION_IMMUTABLE",
                "Coordinator observation reference cannot be rewritten",
            )
        self._observations[key] = observation

    def publish_wake(self, repository: str, wake: DurableWake) -> None:
        key = (repository, wake.durable_reference)
        existing = self._wakes.get(key)
        if existing is not None and existing != wake:
            raise GoalDriverError(
                "DURABLE_WAKE_IMMUTABLE",
                "wake reference cannot be rewritten",
            )
        self._wakes[key] = wake

    def read_observation(
        self,
        repository: str,
        reference: str,
    ) -> CoordinatorTurnObservation | None:
        return self._observations.get((repository, reference))

    def read_wake(
        self,
        repository: str,
        reference: str,
    ) -> DurableWake | None:
        return self._wakes.get((repository, reference))

    def publish_fact(
        self,
        repository: str,
        reference: str,
        content: bytes,
    ) -> str:
        digest = digest_bytes(content)
        key = (repository, reference)
        existing = self._facts.get(key)
        if existing is not None and existing != (content, digest):
            raise GoalDriverError(
                "DURABLE_FACT_IMMUTABLE",
                "semantic fact reference cannot be rewritten",
            )
        self._facts[key] = (bytes(content), digest)
        return digest

    def read_fact_digest(
        self,
        repository: str,
        reference: str,
    ) -> str | None:
        fact = self._facts.get((repository, reference))
        return None if fact is None else fact[1]


class GitHubDurableGoalControl:
    """Immutable Goal events stored on the same GitHub control branch."""

    def __init__(
        self,
        client: GitHubContentClient,
        *,
        branch: str = "gwo-control",
        root: str = ".gwo/v8/goals",
    ):
        self.client = client
        self.branch = branch
        self.root = root.strip("/")

    def _path(self, kind: str, reference: str) -> str:
        return f"{self.root}/{kind}/{digest_value(reference)}.json"

    def _publish(
        self,
        repository: str,
        kind: str,
        reference: str,
        value: dict[str, Any],
    ) -> None:
        path = self._path(kind, reference)
        content = canonical_bytes(value)
        existing = self.client.read(repository, self.branch, path)
        if existing is not None:
            if existing.content != content:
                raise GoalDriverError(
                    "DURABLE_GOAL_EVENT_IMMUTABLE",
                    f"{kind} reference cannot be rewritten",
                )
            return
        self.client.compare_and_swap(
            repository,
            self.branch,
            path,
            content,
            expected_blob_sha=None,
            message=f"Publish GWO {kind} {digest_value(reference)[:12]}",
        )

    def publish_observation(
        self,
        repository: str,
        observation: CoordinatorTurnObservation,
    ) -> None:
        self._publish(
            repository,
            "observations",
            observation.durable_reference,
            asdict(observation),
        )

    def publish_wake(self, repository: str, wake: DurableWake) -> None:
        self._publish(
            repository,
            "wakes",
            wake.durable_reference,
            asdict(wake),
        )

    def publish_fact(
        self,
        repository: str,
        reference: str,
        content: bytes,
    ) -> str:
        digest = digest_bytes(content)
        self._publish(
            repository,
            "facts",
            reference,
            {
                "schema_version": 1,
                "reference": reference,
                "content_base64": base64.b64encode(content).decode("ascii"),
                "content_digest": digest,
            },
        )
        return digest

    def read_fact_digest(
        self,
        repository: str,
        reference: str,
    ) -> str | None:
        value = self._read(repository, "facts", reference)
        if value is None:
            prefix = f"github://{repository}/refs/heads/{self.branch}/"
            if not reference.startswith(prefix):
                return None
            path = reference[len(prefix) :]
            blob = self.client.read(repository, self.branch, path)
            return None if blob is None else digest_bytes(blob.content)
        try:
            content = base64.b64decode(
                value["content_base64"],
                validate=True,
            )
        except (KeyError, TypeError, ValueError) as error:
            raise GoalDriverError(
                "DURABLE_FACT_INVALID",
                "durable semantic fact is malformed",
            ) from error
        digest = digest_bytes(content)
        if (
            value.get("schema_version") != 1
            or value.get("reference") != reference
            or value.get("content_digest") != digest
        ):
            raise GoalDriverError(
                "DURABLE_FACT_INVALID",
                "durable semantic fact failed exact readback",
            )
        return digest

    def _read(
        self,
        repository: str,
        kind: str,
        reference: str,
    ) -> dict[str, Any] | None:
        blob = self.client.read(
            repository,
            self.branch,
            self._path(kind, reference),
        )
        if blob is None:
            return None
        try:
            value = json.loads(blob.content)
        except (TypeError, json.JSONDecodeError) as error:
            raise GoalDriverError(
                "DURABLE_GOAL_EVENT_INVALID",
                f"durable {kind} record is invalid",
            ) from error
        if not isinstance(value, dict):
            raise GoalDriverError(
                "DURABLE_GOAL_EVENT_INVALID",
                f"durable {kind} record is not an object",
            )
        return value

    def read_observation(
        self,
        repository: str,
        reference: str,
    ) -> CoordinatorTurnObservation | None:
        value = self._read(repository, "observations", reference)
        if value is None:
            return None
        try:
            observation = CoordinatorTurnObservation(**value)
        except TypeError as error:
            raise GoalDriverError(
                "DURABLE_OBSERVATION_INVALID",
                "durable Coordinator observation fields are invalid",
            ) from error
        return observation

    def read_wake(
        self,
        repository: str,
        reference: str,
    ) -> DurableWake | None:
        value = self._read(repository, "wakes", reference)
        if value is None:
            return None
        try:
            return DurableWake(**value)
        except TypeError as error:
            raise GoalDriverError(
                "DURABLE_WAKE_INVALID",
                "durable wake fields are invalid",
            ) from error


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
        action_key: str,
    ) -> None: ...

    def create_auto(
        self,
        snapshot: GoalSnapshot,
        profile: RuntimeProfile,
        *,
        action_key: str,
    ) -> CoordinatorSession: ...

    def read_observation(
        self,
        session: CoordinatorSession,
        snapshot: GoalSnapshot,
        *,
        action_key: str,
        semantic_input_digest: str,
        durable_reference: str,
    ) -> CoordinatorTurnObservation | None: ...


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
        action_key: str,
    ) -> None:
        del snapshot, corrective, action_key
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
        *,
        action_key: str,
    ) -> CoordinatorSession:
        del action_key
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

    def read_observation(
        self,
        session: CoordinatorSession,
        snapshot: GoalSnapshot,
        *,
        action_key: str,
        semantic_input_digest: str,
        durable_reference: str,
    ) -> CoordinatorTurnObservation | None:
        del (
            session,
            snapshot,
            action_key,
            semantic_input_digest,
            durable_reference,
        )
        return None


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
    def _prompt(
        snapshot: GoalSnapshot,
        *,
        corrective: bool,
        action_key: str,
    ) -> RuntimePrompt:
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
                "outcome_protocol": {
                    "action_key": action_key,
                    "allowed_outcomes": sorted(GoalDriver._OUTCOMES),
                    "instruction": (
                        "End the final response with exactly one line "
                        '`GWO_COORDINATOR_OUTCOME {"schema_version":1,'
                        f'"action_key":"{action_key}",'
                        '"outcome":"<allowed_outcome>",'
                        '"fact_reference":"<durable-ref-or-null>",'
                        '"fact_digest":"<sha256-or-null>"}`. '
                        "Any non-zero outcome without an exact durable fact "
                        "reference and digest is counted as zero_outcome."
                    ),
                    "marker": "GWO_COORDINATOR_OUTCOME",
                },
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
        action_key: str,
    ) -> None:
        current = self.inspect(session.session_id)
        if current is None or not current.usable:
            raise GoalDriverError(
                "COORDINATOR_UNUSABLE",
                "Paseo Coordinator session cannot be continued",
            )
        prompt = self._prompt(
            snapshot,
            corrective=corrective,
            action_key=action_key,
        )
        self.client.send_prompt(
            current.agent_id,
            prompt,
            action_key=f"{snapshot.goal_key}:{prompt.digest}",
        )

    def create_auto(
        self,
        snapshot: GoalSnapshot,
        profile: RuntimeProfile,
        *,
        action_key: str,
    ) -> CoordinatorSession:
        prompt = self._prompt(
            snapshot,
            corrective=False,
            action_key=action_key,
        )
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
                    **(
                        {}
                        if self.parent_agent_id is None
                        else {"gwo.parent_agent": self.parent_agent_id}
                    ),
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

    def read_observation(
        self,
        session: CoordinatorSession,
        snapshot: GoalSnapshot,
        *,
        action_key: str,
        semantic_input_digest: str,
        durable_reference: str,
    ) -> CoordinatorTurnObservation | None:
        current = self.inspect(session.session_id)
        if current is None or not current.usable:
            return None
        agent = self.client.inspect(current.agent_id)
        if agent.lifecycle not in {"idle", "completed", "ready"}:
            return None
        envelope = read_bounded_outcome(
            self.client.read_output(current.agent_id),
            marker="GWO_COORDINATOR_OUTCOME",
            action_key=action_key,
        )
        if envelope is None:
            return None
        outcome = envelope.get("outcome")
        if outcome not in GoalDriver._OUTCOMES:
            raise GoalDriverError(
                "COORDINATOR_OUTCOME_INVALID",
                "Paseo Coordinator returned an unsupported bounded outcome",
            )
        return CoordinatorTurnObservation(
            goal_key=snapshot.goal_key,
            semantic_input_digest=semantic_input_digest,
            session_id=session.session_id,
            outcome=str(outcome),
            durable_reference=durable_reference,
            fact_reference=(
                str(envelope["fact_reference"])
                if isinstance(envelope.get("fact_reference"), str)
                else None
            ),
            fact_digest=(
                str(envelope["fact_digest"])
                if isinstance(envelope.get("fact_digest"), str)
                else None
            ),
        )


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
        durable: DurableGoalControl | None = None,
    ):
        self.store_path = Path(store_path)
        self.reconciler = reconciler
        self.coordinators = coordinators
        self.auto_profile = auto_profile
        if durable is None and isinstance(coordinators, PaseoCoordinatorRuntime):
            raise GoalDriverError(
                "DURABLE_GOAL_CONTROL_REQUIRED",
                "production Paseo continuation requires durable Goal control",
            )
        self.durable = durable or InMemoryDurableGoalControl()
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
                    turn_sequence INTEGER NOT NULL DEFAULT 0,
                    continuation_outstanding INTEGER NOT NULL,
                    session_id TEXT,
                    last_observation_ref TEXT,
                    wait_condition TEXT,
                    wait_source_ref TEXT,
                    wait_event_identity TEXT,
                    next_check_at TEXT,
                    last_wake_reference TEXT,
                    PRIMARY KEY (repository, goal_key)
                )
                """
            )
            columns = {
                str(row["name"])
                for row in connection.execute(
                    "PRAGMA table_info(v8_goal_driver_state)"
                )
            }
            for column in (
                "wait_source_ref",
                "wait_event_identity",
                "next_check_at",
            ):
                if column not in columns:
                    connection.execute(
                        f"ALTER TABLE v8_goal_driver_state ADD COLUMN {column} TEXT"
                    )
            if "turn_sequence" not in columns:
                connection.execute(
                    """
                    ALTER TABLE v8_goal_driver_state
                    ADD COLUMN turn_sequence INTEGER NOT NULL DEFAULT 0
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
                "node_states": [list(item) for item in snapshot.node_states],
                "evidence_manifests": [
                    list(item) for item in snapshot.evidence_manifests
                ],
                "capability_configuration_digest": (
                    snapshot.capability_configuration_digest
                ),
                "base_identity": snapshot.base_identity,
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
            turn_sequence=0,
            continuation_outstanding=False,
            session_id=None,
            last_observation_ref=None,
            wait_condition=None,
            wait_source_ref=None,
            wait_event_identity=None,
            next_check_at=None,
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
            turn_sequence=int(row["turn_sequence"]),
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
            wait_source_ref=(
                None
                if row["wait_source_ref"] is None
                else str(row["wait_source_ref"])
            ),
            wait_event_identity=(
                None
                if row["wait_event_identity"] is None
                else str(row["wait_event_identity"])
            ),
            next_check_at=(
                None
                if row["next_check_at"] is None
                else str(row["next_check_at"])
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
                    turn_sequence,
                    continuation_outstanding,
                    session_id,
                    last_observation_ref,
                    wait_condition,
                    wait_source_ref,
                    wait_event_identity,
                    next_check_at,
                    last_wake_reference
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(repository, goal_key) DO UPDATE SET
                    semantic_input_digest = excluded.semantic_input_digest,
                    zero_outcomes = excluded.zero_outcomes,
                    turn_sequence = excluded.turn_sequence,
                    continuation_outstanding = excluded.continuation_outstanding,
                    session_id = excluded.session_id,
                    last_observation_ref = excluded.last_observation_ref,
                    wait_condition = excluded.wait_condition,
                    wait_source_ref = excluded.wait_source_ref,
                    wait_event_identity = excluded.wait_event_identity,
                    next_check_at = excluded.next_check_at,
                    last_wake_reference = excluded.last_wake_reference
                """,
                (
                    status.repository,
                    status.goal_key,
                    status.semantic_input_digest,
                    status.zero_outcomes,
                    status.turn_sequence,
                    int(status.continuation_outstanding),
                    status.session_id,
                    status.last_observation_ref,
                    status.wait_condition,
                    status.wait_source_ref,
                    status.wait_event_identity,
                    status.next_check_at,
                    status.last_wake_reference,
                ),
            )

    def _apply_observation(
        self,
        status: GoalDriverStatus,
        observation: CoordinatorTurnObservation,
    ) -> GoalDriverStatus:
        durable = self.durable.read_observation(
            status.repository,
            observation.durable_reference,
        )
        if (
            not status.continuation_outstanding
            or observation.durable_reference == status.last_observation_ref
            or durable != observation
            or
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
        effective_outcome = observation.outcome
        if observation.outcome != "zero_outcome":
            durable_fact_digest = (
                None
                if observation.fact_reference is None
                else self.durable.read_fact_digest(
                    status.repository,
                    observation.fact_reference,
                )
            )
            if (
                durable_fact_digest is None
                or observation.fact_digest != durable_fact_digest
            ):
                effective_outcome = "zero_outcome"
        return GoalDriverStatus(
            repository=status.repository,
            goal_key=status.goal_key,
            semantic_input_digest=status.semantic_input_digest,
            zero_outcomes=(
                status.zero_outcomes + 1
                if effective_outcome == "zero_outcome"
                else 0
            ),
            turn_sequence=status.turn_sequence,
            continuation_outstanding=False,
            session_id=status.session_id,
            last_observation_ref=observation.durable_reference,
            wait_condition=status.wait_condition,
            wait_source_ref=status.wait_source_ref,
            wait_event_identity=status.wait_event_identity,
            next_check_at=status.next_check_at,
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
        *,
        action_key: str,
    ) -> tuple[CoordinatorSession, bool]:
        if status.session_id is not None:
            existing = self.coordinators.inspect(status.session_id)
            if existing is not None and existing.usable:
                return existing, False
        manual = self.coordinators.find_manual(snapshot.goal_key)
        if manual is not None and manual.usable:
            return manual, False
        return (
            self.coordinators.create_auto(
                snapshot,
                self.auto_profile,
                action_key=action_key,
            ),
            True,
        )

    @staticmethod
    def _turn_action_key(status: GoalDriverStatus) -> str:
        return "coordinator-turn:" + digest_value(
            {
                "goal_key": status.goal_key,
                "semantic_input_digest": status.semantic_input_digest,
                "ordinal": status.turn_sequence + 1,
            }
        )[:24]

    @staticmethod
    def _turn_reference(
        status: GoalDriverStatus,
        action_key: str,
    ) -> str:
        return "gwo-observation://" + digest_value(
            {
                "action_key": action_key,
                "repository": status.repository,
            }
        )

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
        if (
            status.wait_condition == "coordinator_turn"
            and status.continuation_outstanding
            and status.session_id is not None
        ):
            expected_reference = status.wait_source_ref
            action_key = status.wait_event_identity
            if expected_reference is None or action_key is None:
                raise GoalDriverError(
                    "COORDINATOR_WAIT_INVALID",
                    "outstanding Coordinator turn lacks a durable action identity",
                )
            if observation is None:
                observation = self.durable.read_observation(
                    snapshot.repository,
                    expected_reference,
                )
            if observation is None:
                session = self.coordinators.inspect(status.session_id)
                if session is not None and session.usable:
                    observation = self.coordinators.read_observation(
                        session,
                        snapshot,
                        action_key=action_key,
                        semantic_input_digest=status.semantic_input_digest,
                        durable_reference=expected_reference,
                    )
                    if observation is not None:
                        self.durable.publish_observation(
                            snapshot.repository,
                            observation,
                        )
            if observation is None:
                return self._directive(
                    "wait",
                    status,
                    session_id=status.session_id,
                    wait_condition=status.wait_condition,
                    wait_source_ref=status.wait_source_ref,
                    wait_event_identity=status.wait_event_identity,
                    next_check_at=status.next_check_at,
                )
            if observation.durable_reference != expected_reference:
                raise GoalDriverError(
                    "COORDINATOR_OBSERVATION_INVALID",
                    "Coordinator observation does not match the outstanding action",
                )
            status = self._apply_observation(status, observation)
            status = replace(
                status,
                wait_condition=None,
                wait_source_ref=None,
                wait_event_identity=None,
                next_check_at=None,
            )
            observation = None
            self._write_status(status)
        elif status.wait_condition is not None:
            wake = (
                None
                if wake_reference is None
                else self.durable.read_wake(
                    snapshot.repository,
                    wake_reference,
                )
            )
            if (
                wake_reference is None
                or wake_reference == status.last_wake_reference
                or wake is None
                or wake.durable_reference != wake_reference
                or wake.goal_key != status.goal_key
                or wake.semantic_input_digest != status.semantic_input_digest
                or wake.wait_condition != status.wait_condition
                or wake.source_ref != status.wait_source_ref
                or wake.event_identity != status.wait_event_identity
            ):
                return self._directive(
                    "wait",
                    status,
                    wait_condition=status.wait_condition,
                    wait_source_ref=status.wait_source_ref,
                    wait_event_identity=status.wait_event_identity,
                    next_check_at=status.next_check_at,
                )
            status = replace(
                status,
                wait_condition=None,
                wait_source_ref=None,
                wait_event_identity=None,
                next_check_at=None,
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
            remaining = [
                work_item_key
                for work_item_key, work_item_state in snapshot.work_items
                if work_item_key != outcome.work_item_key
                and work_item_state not in {"integrated", "completed"}
            ]
            if remaining:
                return self._directive("run_next", status)
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
                wait_source_ref=outcome.wait_source_ref,
                wait_event_identity=outcome.wait_event_identity,
                next_check_at=outcome.next_check_at,
            )
            self._write_status(status)
            return self._directive(
                "wait",
                status,
                wait_condition=outcome.wait_condition,
                wait_source_ref=outcome.wait_source_ref,
                wait_event_identity=outcome.wait_event_identity,
                next_check_at=outcome.next_check_at,
            )
        if status.zero_outcomes >= 2:
            self._write_status(status)
            return self._directive(
                "decision",
                status,
                decision_gate="coordinator_zero_outcome",
            )
        if status.continuation_outstanding and observation is None:
            action_key = self._turn_action_key(status)
            source_ref = self._turn_reference(status, action_key)
            return self._directive(
                "wait",
                status,
                session_id=status.session_id,
                wait_condition="coordinator_turn",
                wait_source_ref=source_ref,
                wait_event_identity=action_key,
                next_check_at=(
                    datetime.now(timezone.utc) + timedelta(seconds=30)
                ).isoformat(),
            )

        action_key = self._turn_action_key(status)
        wait_source_ref = self._turn_reference(status, action_key)
        session, created = self._select_coordinator(
            snapshot,
            status,
            action_key=action_key,
        )
        corrective = status.zero_outcomes == 1
        if not created:
            self.coordinators.continue_session(
                session,
                snapshot,
                corrective=corrective,
                action_key=action_key,
            )
        status = GoalDriverStatus(
            repository=status.repository,
            goal_key=status.goal_key,
            semantic_input_digest=status.semantic_input_digest,
            zero_outcomes=status.zero_outcomes,
            turn_sequence=status.turn_sequence + 1,
            continuation_outstanding=True,
            session_id=session.session_id,
            last_observation_ref=status.last_observation_ref,
            wait_condition="coordinator_turn",
            wait_source_ref=wait_source_ref,
            wait_event_identity=action_key,
            next_check_at=(
                datetime.now(timezone.utc) + timedelta(seconds=30)
            ).isoformat(),
            last_wake_reference=status.last_wake_reference,
        )
        self._write_status(status)
        return self._directive(
            "continue_coordinator",
            status,
            session_id=session.session_id,
            corrective=corrective,
            runtime_profile=session.runtime_profile,
            wait_condition=status.wait_condition,
            wait_source_ref=status.wait_source_ref,
            wait_event_identity=status.wait_event_identity,
            next_check_at=status.next_check_at,
        )
