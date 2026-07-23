"""Fail-closed routing for the explicit V8 ``/implement-gwo`` entry."""

from __future__ import annotations

from dataclasses import dataclass, replace
import json
import sqlite3
from typing import Any

from ._canonical import digest_value
from .activation import ActivationOutcome, LocalPlanPublication
from .compiler import PlanCompiler
from .goal_driver import GoalDirective, GoalDriver, GoalSnapshot


class ImplementGwoInputError(ValueError):
    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class ImplementGwoDecision:
    status: str
    next_action: str | None
    execution_entry: str | None
    work_item_keys: tuple[str, ...]
    fallbacks: tuple[str, ...] = ()


@dataclass(frozen=True)
class ImplementGwoLaunchOutcome:
    decision: ImplementGwoDecision
    activation: ActivationOutcome | None
    directive: GoalDirective | None
    activations: tuple[ActivationOutcome, ...] = ()
    directives: tuple[GoalDirective, ...] = ()


class ImplementGwoEntry:
    """Accept Ready Work Items or name the upstream Matt workflow action."""

    _KINDS = {"work_item", "ready_set", "goal", "spec"}

    @staticmethod
    def _next_for_unready(state: str) -> str:
        if state in {"needs-triage", "needs-info", "raw", "unknown"}:
            return "/triage"
        if state in {"ready-for-human", "design", "draft"}:
            return "/to-spec"
        return "/triage"

    def route(self, request: dict[str, Any]) -> ImplementGwoDecision:
        if not isinstance(request, dict):
            raise ImplementGwoInputError(
                "IMPLEMENT_GWO_INPUT_INVALID",
                "entry input must be an object",
            )
        kind = request.get("kind")
        if kind not in self._KINDS:
            raise ImplementGwoInputError(
                "IMPLEMENT_GWO_KIND_INVALID",
                "entry kind must be work_item, ready_set, goal, or spec",
            )
        work_items = request.get("work_items")
        if not isinstance(work_items, list):
            raise ImplementGwoInputError(
                "IMPLEMENT_GWO_INPUT_INVALID",
                "work_items must be a list",
            )
        if not work_items:
            spec = request.get("spec")
            accepted_spec = (
                kind == "spec"
                and isinstance(spec, dict)
                and spec.get("status") == "accepted"
            )
            return ImplementGwoDecision(
                status="not_ready",
                next_action="/to-tickets" if accepted_spec else "/to-spec",
                execution_entry=None,
                work_item_keys=(),
            )

        keys: list[str] = []
        for item in work_items:
            if not isinstance(item, dict):
                raise ImplementGwoInputError(
                    "IMPLEMENT_GWO_INPUT_INVALID",
                    "each Work Item must be an object",
                )
            key = item.get("key")
            state = item.get("tracker_state")
            if not isinstance(key, str) or not key:
                raise ImplementGwoInputError(
                    "IMPLEMENT_GWO_INPUT_INVALID",
                    "each Work Item requires a stable key",
                )
            if state != "ready-for-agent":
                return ImplementGwoDecision(
                    status="not_ready",
                    next_action=self._next_for_unready(str(state or "unknown")),
                    execution_entry=None,
                    work_item_keys=tuple(keys),
                )
            keys.append(key)

        if kind == "work_item" and len(keys) != 1:
            raise ImplementGwoInputError(
                "IMPLEMENT_GWO_INPUT_INVALID",
                "work_item entry accepts exactly one Ready Work Item",
            )
        return ImplementGwoDecision(
            status="ready",
            next_action=None,
            execution_entry="implement-gwo",
            work_item_keys=tuple(keys),
        )


class ImplementGwoLauncher:
    """Execute the Phase 2 vertical path after the Ready-only gate."""

    def __init__(
        self,
        *,
        compiler: PlanCompiler,
        publication: LocalPlanPublication,
        goal_driver: GoalDriver,
        writer_generation: str,
        entry: ImplementGwoEntry | None = None,
    ):
        self.compiler = compiler
        self.publication = publication
        self.goal_driver = goal_driver
        self.writer_generation = writer_generation
        self.entry = entry or ImplementGwoEntry()
        with sqlite3.connect(self.publication.store_path) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS v8_ready_set_progress (
                    repository TEXT NOT NULL,
                    set_digest TEXT NOT NULL,
                    state_json TEXT NOT NULL,
                    PRIMARY KEY (repository, set_digest)
                )
                """
            )

    def _read_ready_set_state(
        self,
        repository: str,
        set_digest: str,
    ) -> dict[str, Any]:
        with sqlite3.connect(self.publication.store_path) as connection:
            row = connection.execute(
                """
                SELECT state_json FROM v8_ready_set_progress
                WHERE repository = ? AND set_digest = ?
                """,
                (repository, set_digest),
            ).fetchone()
        if row is None:
            return {
                "completed": [],
                "active_key": None,
                "active_plan_digest": None,
                "active_parent_digest": None,
            }
        value = json.loads(row[0])
        if not isinstance(value, dict):
            raise ImplementGwoInputError(
                "IMPLEMENT_GWO_PROGRESS_INVALID",
                "Ready Set progress is malformed",
            )
        return value

    def _write_ready_set_state(
        self,
        repository: str,
        set_digest: str,
        state: dict[str, Any],
    ) -> None:
        rendered = json.dumps(
            state,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        with sqlite3.connect(self.publication.store_path) as connection:
            connection.execute(
                """
                INSERT INTO v8_ready_set_progress (
                    repository, set_digest, state_json
                ) VALUES (?, ?, ?)
                ON CONFLICT(repository, set_digest) DO UPDATE SET
                    state_json = excluded.state_json
                """,
                (repository, set_digest, rendered),
            )

    def launch(
        self,
        request: dict[str, Any],
        *,
        plan_intent: dict[str, Any],
        source_snapshot: dict[str, Any],
        policy_snapshot: dict[str, Any],
        goal_snapshot: GoalSnapshot,
        expected_active_digest: str | None,
    ) -> ImplementGwoLaunchOutcome:
        decision = self.entry.route(request)
        if decision.status != "ready":
            return ImplementGwoLaunchOutcome(
                decision=decision,
                activation=None,
                directive=None,
            )
        source_items = {
            item.get("work_item_key"): item
            for item in source_snapshot.get("work_items", ())
            if isinstance(item, dict)
            and isinstance(item.get("work_item_key"), str)
        }
        nodes = {
            item.get("work_item_key"): item
            for item in plan_intent.get("nodes", ())
            if isinstance(item, dict)
            and isinstance(item.get("work_item_key"), str)
        }
        if (
            tuple(source_items) != decision.work_item_keys
            or set(nodes) != set(decision.work_item_keys)
        ):
            raise ImplementGwoInputError(
                "IMPLEMENT_GWO_SOURCE_MISMATCH",
                "Ready entry, source snapshot, and Plan Intent name different Work Items",
            )
        repository = source_snapshot.get("repository")
        if not isinstance(repository, str):
            raise ImplementGwoInputError(
                "IMPLEMENT_GWO_SOURCE_MISMATCH",
                "source snapshot has no repository identity",
            )
        set_digest = digest_value(
            {
                "repository": repository,
                "work_item_keys": list(decision.work_item_keys),
                "goal_key": goal_snapshot.goal_key,
                "plan_intent_digest": digest_value(plan_intent),
                "source_contract_digest": digest_value(
                    [
                        {
                            key: item.get(key)
                            for key in (
                                "work_item_key",
                                "source_ref",
                                "title",
                                "outcome_contract",
                            )
                        }
                        for item in source_items.values()
                    ]
                ),
                "policy_snapshot_digest": digest_value(policy_snapshot),
            }
        )
        state = self._read_ready_set_state(repository, set_digest)
        completed = set(state.get("completed") or ())
        activations: list[ActivationOutcome] = []
        directives: list[GoalDirective] = []
        for work_item_key in decision.work_item_keys:
            if work_item_key in completed:
                continue
            if state.get("active_key") not in {None, work_item_key}:
                raise ImplementGwoInputError(
                    "IMPLEMENT_GWO_PROGRESS_CONFLICT",
                    "another Ready Set item is already active",
                )
            parent_digest = (
                state.get("active_parent_digest")
                if state.get("active_key") == work_item_key
                else (
                    activations[-1].plan_digest
                    if activations
                    else expected_active_digest
                )
            )
            compiled = self.compiler.compile(
                {
                    **plan_intent,
                    "parent_plan_digest": parent_digest,
                    "nodes": [nodes[work_item_key]],
                    "edges": [],
                },
                {
                    **source_snapshot,
                    "work_items": [source_items[work_item_key]],
                },
                policy_snapshot,
            )
            activation = self.publication.publish_and_activate(
                compiled,
                expected_active_digest=(
                    state.get("active_plan_digest")
                    if state.get("active_key") == work_item_key
                    else parent_digest
                ),
                writer_generation=self.writer_generation,
            )
            state.update(
                {
                    "active_key": work_item_key,
                    "active_plan_digest": compiled.digest,
                    "active_parent_digest": parent_digest,
                }
            )
            self._write_ready_set_state(repository, set_digest, state)
            directive = self.goal_driver.run_once(
                replace(
                    goal_snapshot,
                    plan_digest=compiled.digest,
                )
            )
            activations.append(activation)
            directives.append(directive)
            if directive.kind != "finish":
                break
            completed.add(work_item_key)
            state.update(
                {
                    "completed": [
                        key
                        for key in decision.work_item_keys
                        if key in completed
                    ],
                    "active_key": None,
                    "active_plan_digest": None,
                    "active_parent_digest": None,
                }
            )
            self._write_ready_set_state(repository, set_digest, state)
        return ImplementGwoLaunchOutcome(
            decision=decision,
            activation=None if not activations else activations[-1],
            directive=None if not directives else directives[-1],
            activations=tuple(activations),
            directives=tuple(directives),
        )
