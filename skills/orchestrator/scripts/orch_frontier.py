"""Pure frontier admission and width-aware scheduling policy for Orchestrator."""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import Any, Literal, TypedDict


PRIORITIES = ("P0", "P1", "P2", "P3")
PRIORITY_INDEX = {value: index for index, value in enumerate(PRIORITIES)}
MAX_SEARCH_NODES = 5_000


class ChangeClaims(TypedDict):
    paths: list[str]
    resources: list[str]


class CandidateAssessment(TypedDict):
    issue: int
    disposition: Literal["design", "human", "clarify", "defer", "managed"]
    reason: str


class WavePlan(TypedDict):
    selected: list[int]
    deferred: dict[str, str]
    parallel_width: int
    execution_slots: int
    active_execution: int
    free_execution_slots: int
    integration_wip_limit: int
    integration_wip: int
    free_integration_wip: int
    dispatch_capacity: int
    search_exhausted: bool


class FrontierPlan(TypedDict):
    candidate_assessments: list[CandidateAssessment]
    ready_reserve: int
    reserve_target: int
    reserve_gap: int
    parallel_width_now: int
    frontier_starved: bool
    wave: WavePlan


def _path_parts(raw: str) -> tuple[str, ...]:
    value = raw.replace("\\", "/").strip("/")
    return PurePosixPath(value).parts


def _paths_overlap(left: str, right: str) -> bool:
    a, b = _path_parts(left), _path_parts(right)
    return a[: len(b)] == b or b[: len(a)] == a


def _manifest_resource(raw: str) -> str | None:
    path = PurePosixPath(raw.replace("\\", "/").lower())
    name = path.name
    families = {
        "node": {
            "package.json",
            "package-lock.json",
            "npm-shrinkwrap.json",
            "pnpm-lock.yaml",
            "yarn.lock",
        },
        "python": {"pyproject.toml", "poetry.lock", "pdm.lock", "uv.lock"},
        "rust": {"cargo.toml", "cargo.lock"},
        "go": {"go.mod", "go.sum"},
        "java": {"pom.xml", "gradle.lockfile"},
    }
    for family, names in families.items():
        if name in names:
            parent = path.parent.as_posix()
            return f"manifest:{family}:{'' if parent == '.' else parent}"
    return None


def _schema_resource(raw: str) -> str | None:
    path = PurePosixPath(raw.replace("\\", "/").lower())
    parts = path.parts
    if "migrations" in parts:
        index = parts.index("migrations")
        return f"schema:{PurePosixPath(*parts[:index]).as_posix()}"
    if "schema" in parts or "schema" in path.name:
        if "schema" in parts:
            index = parts.index("schema")
            scope = PurePosixPath(*parts[:index]).as_posix()
        else:
            scope = path.parent.as_posix()
        return f"schema:{'' if scope == '.' else scope}"
    return None


def _generated_resource(raw: str) -> str | None:
    path = PurePosixPath(raw.replace("\\", "/").lower())
    name = path.name
    generated_input = name.endswith((".proto", ".graphql")) or name.endswith(
        (".openapi.json", ".openapi.yaml")
    )
    generated_output = "generated" in path.parts
    if not generated_input and not generated_output:
        return None
    if generated_output:
        generated_index = path.parts.index("generated")
        owner_parts = list(path.parts[:generated_index])
    else:
        owner_parts = list(path.parent.parts)
    while owner_parts and owner_parts[-1] in {
        "api",
        "lib",
        "proto",
        "protos",
        "schema",
        "schemas",
        "src",
    }:
        owner_parts.pop()
    owner = PurePosixPath(*owner_parts).as_posix() if owner_parts else ""
    stem = name
    for suffix in (".openapi.json", ".openapi.yaml", ".graphql", ".proto", path.suffix):
        if suffix and stem.endswith(suffix):
            stem = stem[: -len(suffix)]
            break
    return f"generated:{owner}:{stem}"


def _claim_resources(claims: dict[str, Any]) -> set[str]:
    explicit = {
        value.strip().lower()
        for value in claims.get("resources") or []
        if isinstance(value, str) and value.strip()
    }
    implicit: set[str] = set()
    for raw in claims.get("paths") or []:
        if not isinstance(raw, str):
            continue
        implicit.update(
            resource
            for resource in (
                _manifest_resource(raw),
                _schema_resource(raw),
                _generated_resource(raw),
            )
            if resource is not None
        )
    return explicit | implicit


def claims_overlap(left: dict[str, Any], right: dict[str, Any]) -> bool:
    """Return whether two normalized write/resource claims cannot run together."""

    left_paths = [path for path in left.get("paths") or [] if isinstance(path, str)]
    right_paths = [path for path in right.get("paths") or [] if isinstance(path, str)]
    if any(_paths_overlap(a, b) for a in left_paths for b in right_paths):
        return True
    return bool(_claim_resources(left) & _claim_resources(right))


def _issue_claims(issue: dict[str, Any]) -> ChangeClaims:
    claims = issue.get("change_claims")
    if isinstance(claims, dict):
        return {
            "paths": list(claims.get("paths") or []),
            "resources": list(claims.get("resources") or []),
        }
    return {"paths": list(issue.get("hotset") or []), "resources": []}


def _exclusive_claims(claims: dict[str, Any]) -> bool:
    paths = claims.get("paths")
    if not isinstance(paths, list) or not paths:
        return True
    for raw in paths:
        if not isinstance(raw, str) or not raw.strip():
            return True
        value = raw.replace("\\", "/")
        parts = PurePosixPath(value).parts
        if value.startswith("/") or (len(value) > 1 and value[1] == ":"):
            return True
        if ".." in parts or not parts or value in {".", "./"}:
            return True
        if any(character in value for character in ("*", "?", "[", "]", "\x00")):
            return True
    resources = claims.get("resources") or []
    return not isinstance(resources, list) or any(
        not isinstance(value, str) or not value.strip() for value in resources
    )


def issue_claims(issue: dict[str, Any]) -> ChangeClaims:
    """Return normalized conflict claims for a scheduler Issue."""

    return _issue_claims(issue)


def exclusive_claims(claims: dict[str, Any]) -> bool:
    """Return whether incomplete path claims require repository exclusivity."""

    return _exclusive_claims(claims)


def _counts_as_integration_wip(issue: dict[str, Any]) -> bool:
    dispatch = issue.get("dispatch") or {}
    if dispatch.get("parked") is True or dispatch.get("status") in {
        "merged",
        "retired",
    }:
        return False
    if dispatch.get("status") in {
        "claiming",
        "running",
        "parking",
        "resuming",
        "review",
        "ready-to-merge",
        "blocked",
    }:
        return True
    return issue.get("state") in {"active", "review", "ready-to-merge", "blocked"}


def _counts_as_execution(issue: dict[str, Any]) -> bool:
    dispatch = issue.get("dispatch") or {}
    if dispatch.get("parked") is True:
        return False
    if issue.get("state") in {"review", "ready-to-merge", "merged"}:
        return False
    status = dispatch.get("status")
    if status in {"claiming", "running", "parking", "resuming"}:
        return True
    if status in {"review", "ready-to-merge", "blocked", "merged", "retired"}:
        return False
    return issue.get("state") == "active"


def counts_as_execution(issue: dict[str, Any]) -> bool:
    """Return whether an Issue currently occupies live execution capacity."""

    return _counts_as_execution(issue)


def counts_as_integration_wip(issue: dict[str, Any]) -> bool:
    """Return whether an Issue retains integration capacity and conflict claims."""

    return _counts_as_integration_wip(issue)


def _dispatch_after(issue: dict[str, Any]) -> list[int]:
    value = issue.get("dispatch_after")
    if value is None:
        value = issue.get("dependencies") or []
    return [item for item in value if isinstance(item, int)]


def _candidate_order(issue: dict[str, Any]) -> tuple[Any, ...]:
    return (
        PRIORITY_INDEX.get(issue.get("priority"), len(PRIORITIES)),
        issue.get("milestone_due") or "9999-12-31",
        -int(issue.get("unlocks", 0)),
        int(issue["number"]),
    )


def _score(issues: list[dict[str, Any]]) -> tuple[Any, ...]:
    counts = tuple(
        sum(issue.get("priority") == priority for issue in issues)
        for priority in PRIORITIES
    )
    unlocks = sum(int(issue.get("unlocks", 0)) for issue in issues)
    numbers = tuple(
        -number for number in sorted(int(issue["number"]) for issue in issues)
    )
    return (*counts, unlocks, numbers)


def _optimistic_score(
    selected: list[dict[str, Any]],
    remaining: list[dict[str, Any]],
    capacity: int,
) -> tuple[Any, ...]:
    """Bound a search branch while ignoring conflicts but preserving score order."""

    slots = capacity - len(selected)
    additions: list[dict[str, Any]] = []
    priority_groups = [
        [issue for issue in remaining if issue.get("priority") == priority]
        for priority in PRIORITIES
    ]
    priority_groups.append(
        [issue for issue in remaining if issue.get("priority") not in PRIORITY_INDEX]
    )
    for group in priority_groups:
        if slots <= 0:
            break
        group.sort(
            key=lambda issue: (-int(issue.get("unlocks", 0)), int(issue["number"]))
        )
        chosen = group[:slots]
        additions.extend(chosen)
        slots -= len(chosen)
    return _score([*selected, *additions])


def _best_compatible_subset(
    candidates: list[dict[str, Any]], capacity: int
) -> tuple[list[dict[str, Any]], bool]:
    ordered = sorted(candidates, key=_candidate_order)
    claims_by_number = {int(issue["number"]): _issue_claims(issue) for issue in ordered}

    def conflict(left: dict[str, Any], right: dict[str, Any]) -> bool:
        left_claims = claims_by_number[int(left["number"])]
        right_claims = claims_by_number[int(right["number"])]
        return (
            _exclusive_claims(left_claims)
            or _exclusive_claims(right_claims)
            or claims_overlap(left_claims, right_claims)
        )

    def greedy(order: list[dict[str, Any]]) -> list[dict[str, Any]]:
        selected: list[dict[str, Any]] = []
        for issue in order:
            if len(selected) >= capacity:
                break
            if all(not conflict(issue, other) for other in selected):
                selected.append(issue)
        return selected

    degrees = {
        int(issue["number"]): sum(
            conflict(issue, other) for other in ordered if other is not issue
        )
        for issue in ordered
    }
    low_conflict_order = sorted(
        ordered,
        key=lambda issue: (
            PRIORITY_INDEX.get(issue.get("priority"), len(PRIORITIES)),
            degrees[int(issue["number"])],
            _candidate_order(issue),
        ),
    )
    seeds = [greedy(ordered), greedy(low_conflict_order)]
    best = max(seeds, key=_score, default=[])
    best_score: tuple[Any, ...] = _score(best)
    optimal_score = _optimistic_score([], ordered, capacity)
    visited_nodes = 0
    search_exhausted = False
    optimum_reached = best_score == optimal_score

    def visit(index: int, selected: list[dict[str, Any]]) -> None:
        nonlocal best, best_score, visited_nodes, search_exhausted, optimum_reached
        if search_exhausted or optimum_reached:
            return
        visited_nodes += 1
        if visited_nodes > MAX_SEARCH_NODES:
            search_exhausted = True
            return
        score = _score(selected)
        if score > best_score:
            best, best_score = list(selected), score
            if best_score == optimal_score:
                optimum_reached = True
                return
        if len(selected) >= capacity or index >= len(ordered):
            return
        remaining = ordered[index:]
        if _optimistic_score(selected, remaining, capacity) <= best_score:
            return

        issue = ordered[index]
        if all(not conflict(issue, other) for other in selected):
            selected.append(issue)
            visit(index + 1, selected)
            selected.pop()
        visit(index + 1, selected)

    visit(0, [])
    return sorted(best, key=_candidate_order), search_exhausted


def select_wave(snapshot: dict[str, Any]) -> WavePlan:
    """Choose the highest-value compatible Dispatch set for current capacity."""

    execution_slots = int(
        snapshot.get("execution_slots", snapshot.get("worker_slots", 3))
    )
    integration_limit = int(snapshot.get("integration_wip_limit", execution_slots))
    if not 1 <= execution_slots <= 5:
        raise ValueError("execution slots must be between 1 and 5")
    if integration_limit < execution_slots or integration_limit > 20:
        raise ValueError("integration WIP limit must be between execution slots and 20")

    issues = list(snapshot.get("issues") or [])
    closed = set(snapshot.get("closed_issues") or [])
    integration_wip = [issue for issue in issues if _counts_as_integration_wip(issue)]
    active_execution = [issue for issue in issues if _counts_as_execution(issue)]
    free_execution = max(0, execution_slots - len(active_execution))
    free_integration = max(0, integration_limit - len(integration_wip))
    capacity = min(free_execution, free_integration)
    held_claims = [_issue_claims(issue) for issue in integration_wip]
    held_exclusive = any(_exclusive_claims(claims) for claims in held_claims)
    deferred: dict[str, str] = {}
    candidates: list[dict[str, Any]] = []

    for issue in sorted(
        (
            issue
            for issue in issues
            if issue.get("state") == "ready" and not _counts_as_integration_wip(issue)
        ),
        key=_candidate_order,
    ):
        number = str(issue["number"])
        if not issue.get("contract_valid", False):
            deferred[number] = "contract-invalid"
            continue
        if any(dependency not in closed for dependency in _dispatch_after(issue)):
            deferred[number] = "open-dispatch-dependencies"
            continue
        claims = _issue_claims(issue)
        exclusive = _exclusive_claims(claims)
        if held_exclusive or (exclusive and integration_wip):
            deferred[number] = "exclusive-claims"
            continue
        if any(claims_overlap(claims, other) for other in held_claims):
            deferred[number] = "claim-conflict"
            continue
        candidates.append(issue)

    selected_issues, search_exhausted = _best_compatible_subset(candidates, capacity)
    selected_numbers = {int(issue["number"]) for issue in selected_issues}
    selected_claims = [_issue_claims(issue) for issue in selected_issues]
    for issue in candidates:
        number = int(issue["number"])
        if number in selected_numbers:
            continue
        claims = _issue_claims(issue)
        if selected_issues and (
            _exclusive_claims(claims)
            or any(_exclusive_claims(other) for other in selected_claims)
        ):
            deferred[str(number)] = "exclusive-claims"
        elif selected_issues and any(
            claims_overlap(claims, other) for other in selected_claims
        ):
            deferred[str(number)] = (
                "width-optimized"
                if len(selected_issues) == capacity and capacity > 0
                else "claim-conflict"
            )
        else:
            deferred[str(number)] = "capacity"

    return {
        "selected": [int(issue["number"]) for issue in selected_issues],
        "deferred": deferred,
        "parallel_width": len(selected_issues),
        "execution_slots": execution_slots,
        "active_execution": len(active_execution),
        "free_execution_slots": free_execution,
        "integration_wip_limit": integration_limit,
        "integration_wip": len(integration_wip),
        "free_integration_wip": free_integration,
        "dispatch_capacity": capacity,
        "search_exhausted": search_exhausted,
    }


def analyze_frontier(
    candidates: list[dict[str, Any]],
    scheduler_snapshot: dict[str, Any],
    policy: dict[str, Any],
) -> FrontierPlan:
    """Assess backlog admission and current Ready Reserve health without mutation."""

    include = {str(label).casefold() for label in policy.get("include_labels") or []}
    human = {str(label).casefold() for label in policy.get("human_labels") or []}
    clarify = {str(label).casefold() for label in policy.get("clarify_labels") or []}
    core_labels = {"orch:ready", "orch:active", "orch:blocked"}
    assessments: list[CandidateAssessment] = []
    for issue in sorted(candidates, key=lambda item: int(item["number"])):
        labels = {
            str(label.get("name") if isinstance(label, dict) else label).casefold()
            for label in issue.get("labels") or []
        }
        if labels & core_labels:
            disposition, reason = "managed", "orchestration-state-present"
        elif labels & human:
            disposition, reason = "human", "human-label"
        elif labels & clarify:
            disposition, reason = "clarify", "clarify-label"
        elif include and not labels & include:
            disposition, reason = "defer", "candidate-label-missing"
        else:
            disposition, reason = "design", "candidate-label-match"
        assessments.append(
            {
                "issue": int(issue["number"]),
                "disposition": disposition,
                "reason": reason,
            }
        )

    wave = select_wave(scheduler_snapshot)
    ready_reserve = sum(
        issue.get("state") == "ready"
        and issue.get("contract_valid", False)
        and not _counts_as_integration_wip(issue)
        for issue in scheduler_snapshot.get("issues") or []
    )
    reserve_target = int(
        policy.get("reserve_target", max(6, wave["execution_slots"] * 2))
    )
    return {
        "candidate_assessments": assessments,
        "ready_reserve": ready_reserve,
        "reserve_target": reserve_target,
        "reserve_gap": max(0, reserve_target - ready_reserve),
        "parallel_width_now": wave["parallel_width"],
        "frontier_starved": (
            wave["dispatch_capacity"] > 0
            and wave["parallel_width"] < wave["dispatch_capacity"]
        ),
        "wave": wave,
    }
