"""Deep Review convergence module behind the Kernel's narrow typed decision.

ADR-0039 models Review as a Candidate Evidence gate. This module concentrates
the whole convergence of that gate: stable axis action identity, Standards and
Spec parallel scheduling, materialization retry, Prompt readback and adoption,
axis persistence, and Candidate invalidation. Kernel callers receive one typed
waiting/accepted/rejected/blocked decision and no longer know axis-level
scheduling, retry, or persistence details. Runtime-specific transport stays
behind the Runtime Adapter; Store writes and writer fencing stay behind the
injected narrow persistence and writer callbacks.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
import subprocess
from typing import Any, Callable, Literal

from ._canonical import canonical_bytes, digest_bytes, digest_value
from .evidence import EvidenceVerifier, TypedEvidence
from .retirement import (
    RetirementError,
    ReviewRetirementAuthorization,
    authorize_review_after_evidence,
    completed_review_retirement,
    failed_review_retirement,
    pending_review_retirement,
    validate_review_retirement_records,
)
from .runtime import (
    ReviewAxisBinding,
    ReviewAxisObservation,
    ReviewAxisRequest,
    RuntimeAdapter,
    RuntimeAdapterError,
    RuntimeBinding,
    RuntimeObservation,
    RuntimeProfile,
    resolve_review_profile,
)

REVIEW_AXIS_MATERIALIZATION_EXECUTIONS = 3
REVIEW_AXIS_OBSERVATION_READBACKS = 3


class ReviewConvergenceError(RuntimeError):
    """Invariant violation inside Review convergence.

    The Kernel boundary translates this one module-local typed error into its
    own error type, so callers observe no new failure surface.
    """

    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code = code
        self.detail = detail


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _runtime_error_record(error: RuntimeAdapterError) -> dict[str, str]:
    return {
        "code": error.code,
        "failure_class": error.failure_class,
        "detail": error.detail[:1_024],
    }


def _stable_review_evidence(
    state: dict[str, Any],
    fresh: TypedEvidence,
) -> TypedEvidence:
    """Reuse exact durable Evidence when fresh convergence is semantically equal."""

    saved_value = state.get("review_evidence")
    if not isinstance(saved_value, dict):
        return fresh
    try:
        saved = TypedEvidence(**saved_value)
    except TypeError:
        return fresh
    if (
        saved.has_valid_digest()
        and saved.kind == fresh.kind
        and saved.subject == fresh.subject
        and saved.observer_type == fresh.observer_type
        and saved.observer_id == fresh.observer_id
        and saved.source_ref == fresh.source_ref
        and saved.payload == fresh.payload
    ):
        return saved
    return fresh


def _git(repository: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repository), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if result.returncode != 0:
        raise ReviewConvergenceError(
            "GIT_OPERATION_FAILED",
            result.stderr.strip() or result.stdout.strip() or "git failed",
        )
    return result.stdout.strip()


@dataclass(frozen=True)
class ReviewConvergenceDecision:
    """The one narrow typed Review Gate verdict a Kernel caller receives."""

    status: Literal["waiting", "accepted", "rejected", "blocked"]
    observation: RuntimeObservation
    capture_deferred_checks: bool = False
    findings: tuple[str, ...] = ()


@dataclass(frozen=True)
class _AxisJob:
    axis: str
    key: str
    request: ReviewAxisRequest
    profile: RuntimeProfile
    binding: ReviewAxisBinding | None
    executions: int | None


@dataclass(frozen=True)
class _AxisResult:
    axis: str
    key: str
    request: ReviewAxisRequest
    binding: ReviewAxisBinding | None
    observation: ReviewAxisObservation | None
    error: RuntimeAdapterError | None
    executions: int | None


@dataclass(frozen=True)
class _AxisOutcome:
    request: ReviewAxisRequest
    observation: ReviewAxisObservation | None
    error: RuntimeAdapterError | None


class ReviewConvergence:
    """Converge one Candidate's Review Gate behind a narrow typed decision."""

    def __init__(
        self,
        *,
        runtime: RuntimeAdapter,
        verifier: EvidenceVerifier,
        runtime_config: dict[str, Any] | None,
        assert_writer: Callable[[dict[str, Any]], None],
        persist_state: Callable[[dict[str, Any]], None],
    ):
        self.runtime = runtime
        self.verifier = verifier
        self.runtime_config = runtime_config
        self._assert_writer = assert_writer
        self._persist_state = persist_state

    @staticmethod
    def initial_fields() -> dict[str, Any]:
        """The Review-internal persisted fields owned and reset by this module."""

        return {
            "review_candidate_sha": None,
            "review_bindings": {},
            "review_observations": {},
            "review_materialization_actions": {},
            "review_axis_errors": {},
            "review_materialization_waiting_actions": [],
            "review_children_retired": False,
            "review_retirements": {},
            "review_evidence": None,
            "review_gate_status": None,
            "review_check_manifest_digest": None,
        }

    @staticmethod
    def invalidate_candidate(state: dict[str, Any]) -> None:
        state.update(ReviewConvergence.initial_fields())

    @staticmethod
    def prior_context(state: dict[str, Any]) -> dict[str, Any]:
        prior_findings: list[dict[str, str]] = []
        for captured in (state.get("review_observations") or {}).values():
            if not isinstance(captured, dict):
                continue
            for finding in captured.get("findings") or ():
                if isinstance(finding, dict):
                    prior_findings.append(
                        {
                            str(key): str(value)[:1000]
                            for key, value in finding.items()
                            if isinstance(key, str)
                        }
                    )
        return {
            "candidate_sha": state.get("candidate_sha"),
            "findings": prior_findings[:32],
        }

    @staticmethod
    def _review_binding_from_state(value: dict[str, Any]) -> ReviewAxisBinding:
        return ReviewAxisBinding(**value)

    @staticmethod
    def _review_observation_from_state(
        value: dict[str, Any],
    ) -> ReviewAxisObservation:
        body = dict(value)
        body["findings"] = tuple(
            dict(finding) for finding in body.get("findings") or ()
        )
        return ReviewAxisObservation(**body)

    @staticmethod
    def compact_review_contract(value: Any) -> Any:
        """Replace authoritative file payloads with exact workspace references."""

        if isinstance(value, list):
            return [
                ReviewConvergence.compact_review_contract(item)
                for item in value
            ]
        if not isinstance(value, dict):
            return value
        path = value.get("path")
        content = value.get("content")
        compacted = {
            str(key): ReviewConvergence.compact_review_contract(item)
            for key, item in value.items()
            if key != "content"
        }
        if isinstance(path, str) and path and isinstance(content, str):
            encoded = content.encode("utf-8")
            compacted["content_digest"] = digest_bytes(encoded)
            compacted["content_bytes"] = len(encoded)
        elif "content" in value:
            compacted["content"] = ReviewConvergence.compact_review_contract(
                content
            )
        return compacted

    @staticmethod
    def _review_request(
        *,
        state: dict[str, Any],
        goal: dict[str, Any],
        work_item: dict[str, Any],
        binding,
        observation,
        axis: str,
        recovery_ordinal: int,
    ) -> ReviewAxisRequest:
        assert observation.result_claim is not None
        check_manifest_digest = state.get("review_check_manifest_digest")
        if not isinstance(check_manifest_digest, str):
            check_manifest_digest = digest_value(
                sorted(
                    evidence.content_digest
                    for evidence in observation.evidence
                    if isinstance(evidence, TypedEvidence)
                    and evidence.kind == "check"
                    and evidence.has_valid_digest()
                )
            )
        workspace = Path(binding.workspace).resolve()
        commit_output = _git(
            workspace,
            "log",
            "--format=%H %s",
            f"{state['base_sha']}..{observation.result_claim.candidate_sha}",
        )
        spec_text = canonical_bytes(
            {
                "goal_acceptance": goal.get("acceptance") or [],
                "outcome_contract": ReviewConvergence.compact_review_contract(
                    work_item.get("outcome_contract") or {}
                ),
            }
        ).decode("utf-8")
        prior_context = state.get("prior_review_context") or {}
        prior_candidate = prior_context.get("candidate_sha")
        candidate_delta = None
        if (
            isinstance(prior_candidate, str)
            and prior_candidate
            and prior_candidate != observation.result_claim.candidate_sha
        ):
            candidate_delta = _git(
                workspace,
                "diff",
                "--stat",
                prior_candidate,
                observation.result_claim.candidate_sha,
            )[:4000]
        return ReviewAxisRequest(
            repository=state["repository"],
            attempt_id=observation.result_claim.attempt_id,
            candidate_sha=observation.result_claim.candidate_sha,
            base_sha=state["base_sha"],
            axis=axis,
            recovery_ordinal=recovery_ordinal,
            workspace=workspace,
            diff_command=(
                "git",
                "diff",
                f"{state['base_sha']}...{observation.result_claim.candidate_sha}",
            ),
            commit_list=tuple(commit_output.splitlines()),
            spec_source_ref=str(work_item.get("source_ref") or ""),
            spec_text=spec_text,
            standards_sources=("AGENTS.md", "CONTEXT.md"),
            check_manifest_digest=check_manifest_digest,
            prior_findings=tuple(
                dict(finding)
                for finding in prior_context.get("findings") or ()
                if isinstance(finding, dict)
            ),
            candidate_delta=candidate_delta,
        )

    def _review_wait(
        self,
        state: dict[str, Any],
        *,
        observation: RuntimeObservation,
        candidate_sha: str,
    ) -> ReviewConvergenceDecision:
        state.update(
            {
                "status": "waiting",
                "directive": "wait_for_review",
                "attempt_state": "reviewing",
                "wait_condition": "review_axis",
                "wait_source_ref": (
                    f"{self.runtime.adapter_name}://review/{candidate_sha}"
                ),
                "wait_event_identity": f"review:{candidate_sha}",
                "review_materialization_waiting_actions": [],
                "next_check_at": (
                    datetime.now(timezone.utc) + timedelta(seconds=30)
                ).isoformat(),
            }
        )
        self._persist_state(state)
        return ReviewConvergenceDecision(
            status="waiting",
            observation=observation,
            capture_deferred_checks=True,
        )

    def _review_prompt_wait(
        self,
        state: dict[str, Any],
        *,
        observation: RuntimeObservation,
        request: ReviewAxisRequest,
        error: RuntimeAdapterError,
    ) -> ReviewConvergenceDecision:
        state.update(
            {
                "status": "waiting",
                "directive": "wait_for_runtime_readback",
                "attempt_state": "reviewing",
                "wait_condition": "review_prompt_readback",
                "wait_source_ref": (
                    f"{self.runtime.adapter_name}://review/"
                    f"{request.candidate_sha}/action/{request.action_key}"
                ),
                "wait_event_identity": f"{request.action_key}:prompt_readback",
                "review_materialization_waiting_actions": [],
                "next_check_at": (
                    datetime.now(timezone.utc) + timedelta(seconds=30)
                ).isoformat(),
                "last_runtime_error": _runtime_error_record(error),
            }
        )
        self._persist_state(state)
        return ReviewConvergenceDecision(
            status="waiting",
            observation=observation,
        )

    def _review_retirement_wait(
        self,
        candidate_sha: str,
        *,
        error_code: str,
    ) -> dict[str, Any]:
        return {
            "status": "waiting",
            "directive": "wait_for_review_retirement",
            "attempt_state": "reviewing",
            "wait_condition": "review_retirement",
            "wait_source_ref": (
                f"{self.runtime.adapter_name}://review/"
                f"{candidate_sha}/retirement"
            ),
            "wait_event_identity": f"review-retirement:{candidate_sha}",
            "next_check_at": (
                datetime.now(timezone.utc) + timedelta(seconds=30)
            ).isoformat(),
            "last_runtime_error": {
                "code": error_code,
                "failure_class": "ambiguous",
            },
        }

    def _review_materialization_wait(
        self,
        state: dict[str, Any],
        *,
        observation: RuntimeObservation,
        requests: tuple[ReviewAxisRequest, ...],
    ) -> ReviewConvergenceDecision:
        if not requests:
            raise ReviewConvergenceError(
                "REVIEW_AXIS_WAIT_INVALID",
                "Review materialization wait requires at least one action",
            )
        candidate_sha = requests[0].candidate_sha
        waiting_actions = [
            {
                "axis": request.axis,
                "recovery_ordinal": request.recovery_ordinal,
                "action_key": request.action_key,
            }
            for request in requests
        ]
        first_request = requests[0]
        first_action = (
            state.get("review_materialization_actions") or {}
        ).get(f"{first_request.axis}:{first_request.recovery_ordinal}")
        last_error = (
            first_action.get("last_error")
            if isinstance(first_action, dict)
            else None
        )
        state.update(
            {
                "status": "waiting",
                "directive": "wait_for_runtime_readback",
                "attempt_state": "reviewing",
                "wait_condition": "runtime_available",
                "wait_source_ref": (
                    f"{self.runtime.adapter_name}://review/"
                    f"{candidate_sha}/materialization"
                ),
                "wait_event_identity": "review_axis_materialization",
                "review_materialization_waiting_actions": waiting_actions,
                **(
                    {"last_runtime_error": last_error}
                    if isinstance(last_error, dict)
                    else {}
                ),
                "next_check_at": (
                    datetime.now(timezone.utc) + timedelta(seconds=30)
                ).isoformat(),
            }
        )
        self._persist_state(state)
        return ReviewConvergenceDecision(
            status="waiting",
            observation=observation,
        )

    def converge(
        self,
        state: dict[str, Any],
        work_node: dict[str, Any],
        goal: dict[str, Any],
        work_item: dict[str, Any],
        binding: RuntimeBinding,
        observation: RuntimeObservation,
    ) -> ReviewConvergenceDecision:
        requirement = (work_node.get("output_contract") or {}).get(
            "review_requirement"
        ) or {
            "mode": "none",
            "axes": [],
            "specialist_requirements": [],
            "human_decision_required": False,
        }
        # The module owns the Review check manifest digest from seed to
        # evidence-bound value; the Kernel never writes it.
        state["review_check_manifest_digest"] = digest_value(
            {
                "candidate_sha": observation.result_claim.candidate_sha,
                "definitions": sorted(
                    str(check.get("definition_digest"))
                    for check in (work_node.get("output_contract") or {}).get(
                        "checks"
                    )
                    or ()
                    if isinstance(check, dict)
                    and check.get("hosted_only") is not True
                ),
            }
        )
        if requirement.get("mode") == "none":
            return ReviewConvergenceDecision(
                status="accepted",
                observation=observation,
            )
        if observation.result_claim is None:
            raise ReviewConvergenceError(
                "REVIEW_CANDIDATE_MISSING",
                "Review cannot begin before one Result Claim",
            )
        if self.runtime_config is None:
            raise ReviewConvergenceError(
                "REVIEW_RUNTIME_CONFIG_MISSING",
                "reviewed work requires host-local Review Profiles",
            )
        materialize = getattr(self.runtime, "materialize_review_axis", None)
        observe_axis = getattr(self.runtime, "observe_review_axis", None)
        if not callable(materialize) or not callable(observe_axis):
            raise ReviewConvergenceError(
                "REVIEW_RUNTIME_UNSUPPORTED",
                "Runtime Adapter does not support Review Internal Subagents",
            )
        candidate_sha = observation.result_claim.candidate_sha
        if state.get("review_candidate_sha") != candidate_sha:
            self.invalidate_candidate(state)
            state.pop("last_runtime_error", None)
        state["review_candidate_sha"] = candidate_sha
        affected_ids = {
            str(check["check_id"])
            for check in (work_node.get("output_contract") or {}).get("checks")
            or ()
            if isinstance(check, dict)
            and check.get("suite") == "affected"
            and check.get("hosted_only") is not True
        }
        state["review_check_manifest_digest"] = digest_value(
            sorted(
                item.content_digest
                for item in observation.evidence
                if item.kind == "check"
                and item.payload.get("check_id") in affected_ids
                and item.has_valid_digest()
            )
        )
        bindings = state.setdefault("review_bindings", {})
        observations = state.setdefault("review_observations", {})
        materialization_actions = state.setdefault(
            "review_materialization_actions",
            {},
        )

        def collect_axes(
            axis_ordinals: tuple[tuple[str, int], ...],
        ) -> dict[str, _AxisOutcome]:
            results: dict[str, _AxisOutcome] = {}
            jobs: list[_AxisJob] = []
            materialization_reserved = False
            for axis, recovery_ordinal in axis_ordinals:
                key = f"{axis}:{recovery_ordinal}"
                request = self._review_request(
                    state=state,
                    goal=goal,
                    work_item=work_item,
                    binding=binding,
                    observation=observation,
                    axis=axis,
                    recovery_ordinal=recovery_ordinal,
                )
                saved_observation = observations.get(key)
                if isinstance(saved_observation, dict):
                    captured = self._review_observation_from_state(
                        saved_observation
                    )
                    if captured.lifecycle == "completed":
                        results[axis] = _AxisOutcome(
                            request=request,
                            observation=captured,
                            error=None,
                        )
                        continue
                selector = (
                    "recovery_axis"
                    if recovery_ordinal > 0
                    else (
                        "strict_specialist"
                        if axis.startswith("specialist:")
                        else "standard_axis"
                    )
                )
                try:
                    profile = resolve_review_profile(
                        self.runtime_config,
                        repository=state["repository"],
                        selector=selector,
                    )
                except RuntimeAdapterError as error:
                    results[axis] = _AxisOutcome(
                        request=request,
                        observation=None,
                        error=error,
                    )
                    continue
                saved_binding = bindings.get(key)
                if isinstance(saved_binding, dict):
                    child_binding = self._review_binding_from_state(
                        saved_binding
                    )
                    jobs.append(
                        _AxisJob(
                            axis=axis,
                            key=key,
                            request=request,
                            profile=profile,
                            binding=child_binding,
                            executions=None,
                        )
                    )
                    continue
                action = materialization_actions.setdefault(
                    key,
                    {
                        "action_key": request.action_key,
                        "executions": 0,
                        "state": "pending",
                    },
                )
                if action.get("action_key") != request.action_key:
                    raise ReviewConvergenceError(
                        "REVIEW_AXIS_ACTION_IDENTITY_MISMATCH",
                        "persisted Review materialization action changed identity",
                    )
                executions = action.get("executions")
                if (
                    not isinstance(executions, int)
                    or isinstance(executions, bool)
                    or executions < 0
                ):
                    raise ReviewConvergenceError(
                        "REVIEW_AXIS_ACTION_STATE_INVALID",
                        "persisted Review materialization budget is invalid",
                    )
                if executions >= REVIEW_AXIS_MATERIALIZATION_EXECUTIONS:
                    results[axis] = _AxisOutcome(
                        request=request,
                        observation=None,
                        error=RuntimeAdapterError(
                            "REVIEW_AXIS_MATERIALIZATION_RETRIES_EXHAUSTED",
                            (
                                "review child materialization exhausted three "
                                "reconcile-cycle executions"
                            ),
                            failure_class="permanent",
                        ),
                    )
                    continue
                action.update(
                    {
                        "executions": executions + 1,
                        "state": "executing",
                    }
                )
                materialization_reserved = True
                jobs.append(
                    _AxisJob(
                        axis=axis,
                        key=key,
                        request=request,
                        profile=profile,
                        binding=None,
                        executions=executions,
                    )
                )
            if materialization_reserved:
                self._assert_writer(state)
                self._persist_state(state)

            def execute_axis(job: _AxisJob) -> _AxisResult:
                materialization_error = None
                child_binding = job.binding
                if child_binding is None:
                    try:
                        child_binding = materialize(
                            job.request,
                            job.profile,
                            parent_agent_id=binding.agent_id,
                        )
                    except RuntimeAdapterError as error:
                        materialization_error = error
                if materialization_error is not None:
                    return _AxisResult(
                        axis=job.axis,
                        key=job.key,
                        request=job.request,
                        binding=None,
                        observation=None,
                        error=materialization_error,
                        executions=job.executions,
                    )
                try:
                    captured = observe_axis(job.request, child_binding)
                except RuntimeAdapterError as error:
                    return _AxisResult(
                        axis=job.axis,
                        key=job.key,
                        request=job.request,
                        binding=child_binding,
                        observation=None,
                        error=error,
                        executions=job.executions,
                    )
                return _AxisResult(
                    axis=job.axis,
                    key=job.key,
                    request=job.request,
                    binding=child_binding,
                    observation=captured,
                    error=None,
                    executions=job.executions,
                )

            executed: list[_AxisResult] = []
            if jobs:
                with ThreadPoolExecutor(
                    max_workers=len(jobs),
                    thread_name_prefix="gwo-review-axis",
                ) as executor:
                    futures = tuple(executor.submit(execute_axis, job) for job in jobs)
                    executed = [future.result() for future in futures]

            for result in executed:
                axis = result.axis
                key = result.key
                request = result.request
                child_binding = result.binding
                captured = result.observation
                error = result.error
                materialized_here = result.executions is not None
                if child_binding is not None and materialized_here:
                    action = materialization_actions[key]
                    action.update(
                        {
                            "state": "materialized",
                            "last_error": None,
                        }
                    )
                    state.setdefault("review_axis_errors", {}).pop(key, None)
                    bindings[key] = asdict(child_binding)
                if child_binding is not None:
                    action = materialization_actions[key]
                    if (
                        error is not None
                        and error.code
                        not in {
                            "REVIEW_AXIS_OUTPUT_MISSING",
                            "REVIEW_AXIS_OUTPUT_INVALID",
                        }
                        and error.failure_class in {"ambiguous", "transient"}
                    ):
                        observation_readbacks = (
                            int(action.get("observation_readbacks", 0)) + 1
                        )
                        action.update(
                            {
                                "observation_readbacks": observation_readbacks,
                                "last_error": _runtime_error_record(error),
                            }
                        )
                        state.setdefault("review_axis_errors", {})[key] = error.code
                        if (
                            observation_readbacks
                            < REVIEW_AXIS_OBSERVATION_READBACKS
                        ):
                            action["state"] = "observation_pending"
                            error = RuntimeAdapterError(
                                "REVIEW_AXIS_OBSERVATION_PENDING",
                                (
                                    f"{error.code}: Review child observation "
                                    "will retry against the existing binding"
                                ),
                                failure_class=error.failure_class,
                            )
                        else:
                            action["state"] = "blocked"
                            error = RuntimeAdapterError(
                                "REVIEW_AXIS_OBSERVATION_RETRIES_EXHAUSTED",
                                (
                                    "Review child observation exhausted three "
                                    "readback-first reconcile cycles"
                                ),
                                failure_class="permanent",
                            )
                    elif error is None:
                        action.pop("observation_readbacks", None)
                        action["last_error"] = None
                        state.setdefault("review_axis_errors", {}).pop(key, None)
                if child_binding is None and materialized_here:
                    assert error is not None
                    action = materialization_actions[key]
                    if error.code == "PROMPT_DELIVERY_AMBIGUOUS":
                        action["executions"] = result.executions
                    action.update(
                        {
                            "state": (
                                "blocked"
                                if error.failure_class == "permanent"
                                else "retry_pending"
                            ),
                            "last_error": _runtime_error_record(error),
                        }
                    )
                    state.setdefault("review_axis_errors", {})[key] = error.code
                    if (
                        error.code != "PROMPT_DELIVERY_AMBIGUOUS"
                        and error.failure_class != "permanent"
                        and action["executions"]
                        < REVIEW_AXIS_MATERIALIZATION_EXECUTIONS
                    ):
                        error = RuntimeAdapterError(
                            "REVIEW_AXIS_MATERIALIZATION_PENDING",
                            (
                                f"{error.code}: Review child materialization "
                                "will retry by stable action readback"
                            ),
                            failure_class=error.failure_class,
                        )
                    elif (
                        error.code != "PROMPT_DELIVERY_AMBIGUOUS"
                        and error.failure_class != "permanent"
                    ):
                        action["state"] = "blocked"
                        error = RuntimeAdapterError(
                            "REVIEW_AXIS_MATERIALIZATION_RETRIES_EXHAUSTED",
                            (
                                "review child materialization exhausted three "
                                "reconcile-cycle executions"
                            ),
                            failure_class="permanent",
                        )
                if captured is not None:
                    observations[key] = asdict(captured)
                results[axis] = _AxisOutcome(
                    request=request,
                    observation=captured,
                    error=error,
                )
            if executed:
                ordered_errors = [
                    results[axis].error
                    for axis, _ordinal in axis_ordinals
                    if axis in results and results[axis].error is not None
                ]
                if ordered_errors:
                    state["last_runtime_error"] = _runtime_error_record(
                        ordered_errors[0]
                    )
                else:
                    state.pop("last_runtime_error", None)
                self._persist_state(state)
            return results

        required_axes = (
            *tuple(requirement.get("axes") or ()),
            *tuple(
                f"specialist:{item}"
                for item in requirement.get("specialist_requirements") or ()
            ),
        )
        primary: dict[str, ReviewAxisObservation] = {}
        requests: dict[str, ReviewAxisRequest] = {}
        running = False
        materialization_pending: list[ReviewAxisRequest] = []
        prompt_pending: list[tuple[ReviewAxisRequest, RuntimeAdapterError]] = []
        try:
            primary_outcomes = collect_axes(
                tuple((str(axis), 0) for axis in required_axes)
            )
            for axis in required_axes:
                outcome = primary_outcomes[str(axis)]
                requests[str(axis)] = outcome.request
                error = outcome.error
                captured = outcome.observation
                if error is not None:
                    if error.code == "PROMPT_DELIVERY_AMBIGUOUS":
                        prompt_pending.append((outcome.request, error))
                        continue
                    if error.code == "REVIEW_AXIS_MATERIALIZATION_PENDING":
                        materialization_pending.append(outcome.request)
                        continue
                    if error.code == "REVIEW_AXIS_OBSERVATION_PENDING":
                        running = True
                        continue
                    if error.code not in {
                        "REVIEW_AXIS_OUTPUT_MISSING",
                        "REVIEW_AXIS_OUTPUT_INVALID",
                    }:
                        raise error
                    state.setdefault("review_axis_errors", {})[str(axis)] = error.code
                    continue
                assert captured is not None
                if captured.lifecycle == "completed":
                    primary[str(axis)] = captured
                else:
                    running = True
        except RuntimeAdapterError as error:
            state.update(
                {
                    "status": "blocked",
                    "directive": "request_decision",
                    "attempt_state": "review_runtime_blocked",
                    "wait_condition": None,
                    "wait_source_ref": None,
                    "wait_event_identity": None,
                    "next_check_at": None,
                    "last_runtime_error": _runtime_error_record(error),
                }
            )
            self._persist_state(state)
            return ReviewConvergenceDecision(
                status="blocked",
                observation=observation,
            )
        if materialization_pending:
            return self._review_materialization_wait(
                state,
                observation=observation,
                requests=tuple(materialization_pending),
            )
        if prompt_pending:
            request, error = prompt_pending[0]
            return self._review_prompt_wait(
                state,
                observation=observation,
                request=request,
                error=error,
            )
        state["review_materialization_waiting_actions"] = []
        if running:
            return self._review_wait(
                state,
                observation=observation,
                candidate_sha=candidate_sha,
            )

        first_request = requests[required_axes[0]]
        gate = self.verifier.assemble_review_evidence(
            observation.result_claim,
            requirement,
            tuple(primary[axis] for axis in required_axes if axis in primary),
            acceptance_digest=first_request.spec_digest,
            check_manifest_digest=first_request.check_manifest_digest,
            observer_id=binding.runtime_id,
        )
        if gate.missing_axes:
            recovered = dict(primary)
            recovery_running = False
            recovery_materialization_pending: list[ReviewAxisRequest] = []
            recovery_prompt_pending: list[
                tuple[ReviewAxisRequest, RuntimeAdapterError]
            ] = []
            try:
                recovery_outcomes = collect_axes(
                    tuple((axis, 1) for axis in gate.missing_axes)
                )
                for axis in gate.missing_axes:
                    outcome = recovery_outcomes[axis]
                    requests[axis] = outcome.request
                    error = outcome.error
                    captured = outcome.observation
                    if error is not None:
                        if error.code == "PROMPT_DELIVERY_AMBIGUOUS":
                            recovery_prompt_pending.append((outcome.request, error))
                            continue
                        if error.code == "REVIEW_AXIS_MATERIALIZATION_PENDING":
                            recovery_materialization_pending.append(
                                outcome.request
                            )
                            continue
                        if error.code == "REVIEW_AXIS_OBSERVATION_PENDING":
                            recovery_running = True
                            continue
                        raise error
                    assert captured is not None
                    if captured.lifecycle == "completed":
                        recovered[axis] = captured
                    else:
                        recovery_running = True
            except RuntimeAdapterError as error:
                state.update(
                    {
                        "status": "blocked",
                        "directive": "request_decision",
                        "attempt_state": "review_recovery_blocked",
                        "wait_condition": None,
                        "last_runtime_error": _runtime_error_record(error),
                    }
                )
                self._persist_state(state)
                return ReviewConvergenceDecision(
                    status="blocked",
                    observation=observation,
                )
            if recovery_materialization_pending:
                return self._review_materialization_wait(
                    state,
                    observation=observation,
                    requests=tuple(recovery_materialization_pending),
                )
            if recovery_prompt_pending:
                request, error = recovery_prompt_pending[0]
                return self._review_prompt_wait(
                    state,
                    observation=observation,
                    request=request,
                    error=error,
                )
            state["review_materialization_waiting_actions"] = []
            if recovery_running:
                return self._review_wait(
                    state,
                    observation=observation,
                    candidate_sha=candidate_sha,
                )
            gate = self.verifier.assemble_review_evidence(
                observation.result_claim,
                requirement,
                tuple(recovered[axis] for axis in required_axes if axis in recovered),
                acceptance_digest=first_request.spec_digest,
                check_manifest_digest=first_request.check_manifest_digest,
                observer_id=binding.runtime_id,
            )
        if gate.evidence is None:
            state.update(
                {
                    "status": "blocked",
                    "directive": "request_decision",
                    "attempt_state": "review_evidence_invalid",
                    "wait_condition": None,
                }
            )
            self._persist_state(state)
            return ReviewConvergenceDecision(
                status="blocked",
                observation=observation,
            )
        review_evidence = _stable_review_evidence(
            state,
            gate.evidence,
        )
        state["review_evidence"] = asdict(review_evidence)
        state["review_gate_status"] = gate.status
        state["wait_condition"] = None
        state["wait_source_ref"] = None
        state["wait_event_identity"] = None
        state["review_materialization_waiting_actions"] = []
        state["next_check_at"] = None
        self._persist_state(state)
        retire_review = getattr(
            self.runtime,
            "retire_review_after_evidence",
            None,
        )
        if not callable(retire_review):
            raise ReviewConvergenceError(
                "REVIEW_RETIREMENT_UNSUPPORTED",
                "Runtime Adapter has no typed Review retirement seam",
            )
        retirements = state.setdefault("review_retirements", {})
        try:
            retirement_validation = validate_review_retirement_records(
                records=retirements,
                worker_binding=binding,
                review_bindings={
                    key: self._review_binding_from_state(saved)
                    for key, saved in bindings.items()
                    if isinstance(saved, dict)
                },
                review_evidence=review_evidence,
            )
        except RetirementError as error:
            state["review_children_retired"] = False
            state.update(
                self._review_retirement_wait(
                    candidate_sha,
                    error_code=error.code,
                )
            )
            self._persist_state(state)
            return ReviewConvergenceDecision(
                status="waiting",
                observation=observation,
            )
        derived_children_retired = (
            retirement_validation.children_retired
        )
        if state.get("review_children_retired") != derived_children_retired:
            state["review_children_retired"] = derived_children_retired
            self._persist_state(state)
        if not state.get("review_children_retired"):
            self._assert_writer(state)
            for key, saved in bindings.items():
                if not isinstance(saved, dict):
                    continue
                child = self._review_binding_from_state(saved)
                record = retirements.get(key)
                authorization = None
                if isinstance(record, dict) and isinstance(
                    record.get("authorization"),
                    dict,
                ):
                    try:
                        authorization = ReviewRetirementAuthorization(
                            **record["authorization"]
                        )
                        authorization.assert_valid_digest()
                    except (RetirementError, TypeError) as error:
                        code = (
                            error.code
                            if isinstance(error, RetirementError)
                            else "REVIEW_RETIREMENT_AUTHORIZATION_INVALID"
                        )
                        retirements[key] = failed_review_retirement(
                            None,
                            code=code,
                            failure_class="ambiguous",
                        )
                        authorization = None
                if authorization is None:
                    try:
                        authorization = authorize_review_after_evidence(
                            worker_binding=binding,
                            review_binding=child,
                            review_evidence=review_evidence,
                        )
                    except RetirementError as error:
                        retirements[key] = failed_review_retirement(
                            None,
                            code=error.code,
                            failure_class="ambiguous",
                        )
                        state.update(
                            self._review_retirement_wait(
                                candidate_sha,
                                error_code=error.code,
                            )
                        )
                        self._persist_state(state)
                        return ReviewConvergenceDecision(
                            status="waiting",
                            observation=observation,
                        )
                    retirements[key] = pending_review_retirement(
                        authorization
                    )
                    self._persist_state(state)
                try:
                    receipt = retire_review(child, authorization)
                    retirements[key] = completed_review_retirement(
                        authorization,
                        receipt,
                    )
                except RuntimeAdapterError as error:
                    retirements[key] = failed_review_retirement(
                        authorization,
                        code=error.code,
                        failure_class=error.failure_class,
                    )
                    state.update(
                        self._review_retirement_wait(
                            candidate_sha,
                            error_code=error.code,
                        )
                    )
                    state["last_runtime_error"] = _runtime_error_record(error)
                    self._persist_state(state)
                    return ReviewConvergenceDecision(
                        status="waiting",
                        observation=observation,
                    )
                except RetirementError as error:
                    retirements[key] = failed_review_retirement(
                        authorization,
                        code=error.code,
                        failure_class="ambiguous",
                    )
                    state.update(
                        self._review_retirement_wait(
                            candidate_sha,
                            error_code=error.code,
                        )
                    )
                    self._persist_state(state)
                    return ReviewConvergenceDecision(
                        status="waiting",
                        observation=observation,
                    )
                self._persist_state(state)
            completed_validation = validate_review_retirement_records(
                records=retirements,
                worker_binding=binding,
                review_bindings={
                    key: self._review_binding_from_state(saved)
                    for key, saved in bindings.items()
                    if isinstance(saved, dict)
                },
                review_evidence=review_evidence,
            )
            state["review_children_retired"] = (
                completed_validation.children_retired
            )
            if not state["review_children_retired"]:
                raise ReviewConvergenceError(
                    "REVIEW_RETIREMENT_READBACK_INCOMPLETE",
                    "typed Review retirement records did not derive completion",
                )
            self._persist_state(state)
        if gate.status == "rejected":
            # A rejected gate is authoritative on its own; human approval is
            # only relevant to an otherwise acceptable gate, so the typed
            # rejection returns before any human wait or deny logic.
            return ReviewConvergenceDecision(
                status="rejected",
                observation=replace(
                    observation,
                    evidence=observation.evidence + (review_evidence,),
                ),
                capture_deferred_checks=True,
                findings=gate.blockers,
            )
        if requirement.get("human_decision_required") is True:
            decision = state.get("human_decision")
            if (
                not isinstance(decision, dict)
                or decision.get("candidate_sha") != candidate_sha
            ):
                if not state.get("worker_parked_for_decision"):
                    self.runtime.interrupt(binding)
                    state["worker_parked_for_decision"] = True
                state.update(
                    {
                        "status": "waiting",
                        "directive": "wait_for_decision",
                        "attempt_state": "reviewing",
                        "wait_condition": "human_decision",
                        "wait_source_ref": str(work_item.get("source_ref") or ""),
                        "wait_event_identity": (f"human-decision:{candidate_sha}"),
                        "next_check_at": None,
                    }
                )
                self._persist_state(state)
                return ReviewConvergenceDecision(
                    status="waiting",
                    observation=observation,
                )
            if decision.get("approved") is not True:
                state.update(
                    {
                        "status": "blocked",
                        "directive": "request_decision",
                        "attempt_state": "candidate_rejected",
                        "wait_condition": None,
                    }
                )
                self._persist_state(state)
                return ReviewConvergenceDecision(
                    status="blocked",
                    observation=observation,
                )
            decision_evidence = TypedEvidence._capture(
                kind="decision",
                subject=candidate_sha,
                observer_type="human",
                observer_id=str(decision["source_ref"]),
                observed_at=_now(),
                source_ref=str(decision["source_ref"]),
                payload={
                    "candidate_sha": candidate_sha,
                    "approved": True,
                    "decision_kind": "strict_review_human",
                },
            )
            state["human_decision_evidence"] = asdict(decision_evidence)
            self._persist_state(state)
        return ReviewConvergenceDecision(
            status="accepted",
            observation=replace(
                observation,
                evidence=observation.evidence
                + (review_evidence,)
                + (
                    ()
                    if requirement.get("human_decision_required") is not True
                    else (decision_evidence,)
                ),
            ),
            capture_deferred_checks=True,
        )
