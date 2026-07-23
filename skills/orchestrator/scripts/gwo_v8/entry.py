"""Fail-closed routing for the explicit V8 ``/implement-gwo`` entry."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

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
        if len(keys) > 1:
            return ImplementGwoDecision(
                status="planning_required",
                next_action="compile-ready-set",
                execution_entry="implement-gwo",
                work_item_keys=tuple(keys),
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
        source_keys = tuple(
            item.get("work_item_key")
            for item in source_snapshot.get("work_items", ())
            if isinstance(item, dict)
        )
        if source_keys != decision.work_item_keys:
            raise ImplementGwoInputError(
                "IMPLEMENT_GWO_SOURCE_MISMATCH",
                "Ready entry and authoritative source snapshot name different Work Items",
            )
        compiled = self.compiler.compile(
            plan_intent,
            source_snapshot,
            policy_snapshot,
        )
        activation = self.publication.publish_and_activate(
            compiled,
            expected_active_digest=expected_active_digest,
            writer_generation=self.writer_generation,
        )
        directive = self.goal_driver.run_once(
            replace(
                goal_snapshot,
                plan_digest=compiled.digest,
            )
        )
        return ImplementGwoLaunchOutcome(
            decision=decision,
            activation=activation,
            directive=directive,
        )
