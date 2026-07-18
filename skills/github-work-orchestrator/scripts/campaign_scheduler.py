#!/usr/bin/env python3
"""Deterministically plan one parallel GWO Campaign dispatch wave."""

from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import re
from typing import Any

from contract_schema import (
    DEFAULT_MAX_DISPATCH_ATTEMPTS_PER_ISSUE,
    VERIFICATION_CLASSES,
)
from hotset_policy import (
    hotset_is_within,
    hotsets_overlap,
    normalize_hotset,
)


SCHEMA_VERSION = 1
LIFECYCLE_READY = "ready-for-agent"
REPOSITORY_RE = re.compile(r"^[^/\s]+/[^/\s]+$")
CAMPAIGN_RE = re.compile(r"^[a-z0-9][a-z0-9-]{5,63}$")
PREDECESSOR_TERMINAL_EVENTS = {"STOPPED"}
PREDECESSOR_AGENT_STATUSES = {"error", "closed", "archived"}


def _positive_integer(name: str, value: Any) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return value


def _nonnegative_integer(name: str, value: Any) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"{name} must be a nonnegative integer")
    return value


def _nonempty_text(name: str, value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be nonempty text")
    return value.strip()


def _control_plane_blockers(value: Any) -> list[str]:
    if not isinstance(value, dict):
        return ["control-plane-evidence-missing"]
    required = (
        "repository_coordinators",
        "campaign_orchestrators",
        "scope_readback",
        "provider_binding_readback",
    )
    if any(field not in value for field in required):
        return ["control-plane-evidence-missing"]
    blockers: list[str] = []
    try:
        repository_coordinators = _nonnegative_integer(
            "repository_coordinators", value["repository_coordinators"]
        )
        campaign_orchestrators = _nonnegative_integer(
            "campaign_orchestrators", value["campaign_orchestrators"]
        )
    except ValueError:
        return ["control-plane-evidence-invalid"]
    if repository_coordinators != 1:
        blockers.append("repository-coordinator-conflict")
    if campaign_orchestrators != 1:
        blockers.append("campaign-orchestrator-conflict")
    if value["scope_readback"] is not True:
        blockers.append("campaign-scope-not-read-back")
    if value["provider_binding_readback"] is not True:
        blockers.append("provider-binding-not-read-back")
    return blockers


def _capacity(value: Any) -> tuple[dict[str, int], list[str]]:
    if not isinstance(value, dict):
        raise ValueError("capacity must be an object")
    campaign_active = _nonnegative_integer(
        "campaign_active_agents", value.get("campaign_active_agents")
    )
    campaign_limit = _positive_integer(
        "campaign_agent_limit", value.get("campaign_agent_limit")
    )
    global_active = _nonnegative_integer(
        "global_active_agents", value.get("global_active_agents")
    )
    global_limit = _positive_integer(
        "global_agent_limit", value.get("global_agent_limit")
    )
    blockers: list[str] = []
    if campaign_active > campaign_limit or global_active > global_limit:
        blockers.append("capacity-already-exceeded")
    if campaign_active > global_active:
        blockers.append("capacity-counts-contradictory")
    # The repository-resident Coordinator is outside every Campaign but counts globally.
    if campaign_active and campaign_active + 1 > global_active:
        blockers.append("repository-coordinator-missing-from-global-count")
    return {
        "campaign_active": campaign_active,
        "campaign_limit": campaign_limit,
        "global_active": global_active,
        "global_limit": global_limit,
    }, blockers


def _active_dispatches(value: Any) -> tuple[list[dict[str, Any]], list[str]]:
    if not isinstance(value, list):
        raise ValueError("active_dispatches must be a list")
    normalized: list[dict[str, Any]] = []
    issues: list[int] = []
    dispatch_ids: list[str] = []
    blockers: list[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            raise ValueError(f"active_dispatches[{index}] must be an object")
        issue = _positive_integer(
            f"active_dispatches[{index}].issue", item.get("issue")
        )
        dispatch_id = _nonempty_text(
            f"active_dispatches[{index}].dispatch_id", item.get("dispatch_id")
        )
        hotset = normalize_hotset(item.get("hotset", []), allow_empty=True)
        verification_class = item.get("verification_class", "fast")
        if verification_class not in VERIFICATION_CLASSES:
            raise ValueError("active dispatch has invalid verification_class")
        issues.append(issue)
        dispatch_ids.append(dispatch_id)
        normalized.append(
            {
                "issue": issue,
                "dispatch_id": dispatch_id,
                "hotset": hotset,
                "repository_wide": not hotset,
                "verification_class": verification_class,
            }
        )
    if any(count > 1 for count in Counter(issues).values()):
        blockers.append("duplicate-active-dispatch")
    if any(count > 1 for count in Counter(dispatch_ids).values()):
        blockers.append("duplicate-active-dispatch-id")
    return normalized, blockers


def _external_hotsets(
    value: Any, current_campaign_id: str
) -> tuple[list[dict[str, Any]], list[str]]:
    if not isinstance(value, dict):
        raise ValueError("active_external_hotsets must be an object")
    result: list[dict[str, Any]] = []
    blockers: list[str] = []
    for campaign_id, hotset in sorted(value.items()):
        _nonempty_text("external campaign_id", campaign_id)
        if campaign_id == current_campaign_id:
            blockers.append("current-campaign-listed-as-external")
            continue
        normalized = normalize_hotset(hotset, allow_empty=True)
        result.append(
            {
                "campaign_id": campaign_id,
                "hotset": normalized,
                "repository_wide": not normalized,
            }
        )
    return result, blockers


def _conflicts(
    hotset: list[str],
    repository_wide: bool,
    claims: list[dict[str, Any]],
    *,
    case_sensitive_paths: bool,
) -> bool:
    for claim in claims:
        if repository_wide or claim["repository_wide"]:
            return True
        if hotsets_overlap(
            hotset, claim["hotset"], case_sensitive=case_sensitive_paths
        ):
            return True
    return False


def _dispatch_readback_blockers(value: Any, *, expected_dispatch_id: str) -> list[str]:
    if not isinstance(value, dict):
        return ["dispatch-readback-missing"]
    if value.get("dispatch_id") != expected_dispatch_id:
        return ["dispatch-readback-invalid"]
    try:
        active_matches = _nonnegative_integer(
            "dispatch_readback.active_matches", value.get("active_matches")
        )
        archived_matches = _nonnegative_integer(
            "dispatch_readback.archived_matches", value.get("archived_matches")
        )
    except ValueError:
        return ["dispatch-readback-invalid"]
    if value.get("read_back") is not True:
        return ["dispatch-readback-invalid"]
    if active_matches or archived_matches:
        return ["dispatch-identity-already-exists"]
    return []


def _predecessor_blockers(value: Any, *, issue: int, attempt: int) -> list[str]:
    if attempt == 1:
        return [] if value is None else ["unexpected-predecessor-evidence"]
    if not isinstance(value, dict):
        return ["terminal-predecessor-proof-missing"]
    expected_dispatch_id = f"dispatch-issue-{issue}-a{attempt - 1}"
    predecessor_agent_id = value.get("agent_id")
    required_true = (
        "terminal_read_back",
        "agent_reconciled",
        "ownership_unambiguous",
        "wip_durable",
    )
    valid = (
        value.get("dispatch_id") == expected_dispatch_id
        and value.get("attempt") == attempt - 1
        and isinstance(predecessor_agent_id, str)
        and bool(predecessor_agent_id.strip())
        and value.get("terminal_event") in PREDECESSOR_TERMINAL_EVENTS
        and isinstance(value.get("terminal_signal_id"), str)
        and bool(value["terminal_signal_id"].strip())
        and value.get("terminal_sender_agent_id") == predecessor_agent_id
        and value.get("agent_status") in PREDECESSOR_AGENT_STATUSES
        and all(value.get(field) is True for field in required_true)
    )
    return [] if valid else ["terminal-predecessor-proof-invalid"]


def _candidate(item: Any, index: int, max_attempts: int) -> dict[str, Any]:
    if not isinstance(item, dict):
        raise ValueError(f"candidates[{index}] must be an object")
    issue = _positive_integer(f"candidates[{index}].issue", item.get("issue"))
    rank = item.get("rank", issue)
    if not isinstance(rank, int) or isinstance(rank, bool):
        raise ValueError(f"candidates[{index}].rank must be an integer")
    assignees = item.get("assignees")
    if not isinstance(assignees, list) or any(
        not isinstance(value, str) or not value.strip() for value in assignees
    ):
        raise ValueError(f"candidates[{index}].assignees must be a text list")
    dependencies = item.get("open_dependencies")
    if not isinstance(dependencies, list):
        raise ValueError(f"candidates[{index}].open_dependencies must be a list")
    dependencies = [
        _positive_integer(f"candidates[{index}].open_dependencies", value)
        for value in dependencies
    ]
    hotset = normalize_hotset(item.get("hotset", []), allow_empty=True)
    verification_class = item.get("verification_class")
    if verification_class not in VERIFICATION_CLASSES:
        raise ValueError(f"candidates[{index}] has invalid verification_class")
    attempt = _positive_integer(f"candidates[{index}].attempt", item.get("attempt"))
    dispatch_id = f"dispatch-issue-{issue}-a{attempt}"
    blockers: list[str] = []
    if item.get("lifecycle") != LIFECYCLE_READY:
        blockers.append("lifecycle-not-ready")
    if item.get("contract_valid") is not True:
        blockers.append("execution-contract-invalid")
    if assignees:
        blockers.append("issue-already-claimed")
    if dependencies:
        blockers.append("open-dependencies")
    blockers.extend(
        _dispatch_readback_blockers(
            item.get("dispatch_readback"), expected_dispatch_id=dispatch_id
        )
    )
    blockers.extend(
        _predecessor_blockers(
            item.get("previous_dispatch"), issue=issue, attempt=attempt
        )
    )
    if attempt > max_attempts:
        blockers.append("retry-limit-exhausted")
    return {
        "issue": issue,
        "rank": rank,
        "hotset": hotset,
        "repository_wide": not hotset,
        "verification_class": verification_class,
        "attempt": attempt,
        "dispatch_id": dispatch_id,
        "blockers": blockers,
    }


def plan_wave(snapshot: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(snapshot, dict):
        raise ValueError("snapshot must be an object")
    if snapshot.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("snapshot schema_version must be 1")
    repository = _nonempty_text("repository", snapshot.get("repository"))
    campaign_id = _nonempty_text("campaign_id", snapshot.get("campaign_id"))
    if not REPOSITORY_RE.fullmatch(repository):
        raise ValueError("repository must be owner/name")
    if not CAMPAIGN_RE.fullmatch(campaign_id):
        raise ValueError("campaign_id is invalid")
    case_sensitive_paths = snapshot.get("case_sensitive_paths")
    if not isinstance(case_sensitive_paths, bool):
        raise ValueError("case_sensitive_paths readback must be boolean")
    max_attempts = _positive_integer(
        "max_dispatch_attempts",
        snapshot.get("max_dispatch_attempts", DEFAULT_MAX_DISPATCH_ATTEMPTS_PER_ISSUE),
    )
    if max_attempts > DEFAULT_MAX_DISPATCH_ATTEMPTS_PER_ISSUE:
        raise ValueError("max_dispatch_attempts must not exceed 3")
    campaign_hotset = normalize_hotset(
        snapshot.get("campaign_hotset", []), allow_empty=True
    )
    campaign_repository_wide = not campaign_hotset
    capacity, capacity_blockers = _capacity(snapshot.get("capacity"))
    active, active_blockers = _active_dispatches(snapshot.get("active_dispatches", []))
    external, external_blockers = _external_hotsets(
        snapshot.get("active_external_hotsets", {}), campaign_id
    )
    global_blockers = sorted(
        set(
            _control_plane_blockers(snapshot.get("control_plane"))
            + capacity_blockers
            + active_blockers
            + external_blockers
        )
    )
    if capacity["campaign_active"] < 1:
        global_blockers.append("campaign-orchestrator-missing-from-campaign-count")
    if capacity["global_active"] < 1 + capacity["campaign_active"] + len(external):
        global_blockers.append("external-campaign-count-contradicts-capacity")
    if any(
        campaign_repository_wide
        or claim["repository_wide"]
        or hotsets_overlap(
            campaign_hotset,
            claim["hotset"],
            case_sensitive=case_sensitive_paths,
        )
        for claim in external
    ):
        global_blockers.append("campaign-hotset-conflict")

    review_agent = snapshot.get("review_agent")
    if (
        not isinstance(review_agent, dict)
        or not isinstance(review_agent.get("exists"), bool)
        or not isinstance(review_agent.get("reusable"), bool)
    ):
        global_blockers.append("review-agent-evidence-missing")
    elif review_agent["exists"] and capacity["campaign_active"] < 2:
        global_blockers.append("review-agent-missing-from-campaign-count")
    minimum_campaign_agents = 1 + len(active)
    if isinstance(review_agent, dict) and review_agent.get("exists") is True:
        minimum_campaign_agents += 1
    if capacity["campaign_active"] < minimum_campaign_agents:
        global_blockers.append("active-dispatch-count-contradicts-capacity")

    raw_candidates = snapshot.get("candidates")
    if not isinstance(raw_candidates, list):
        raise ValueError("candidates must be a list")
    candidates = [
        _candidate(item, index, max_attempts)
        for index, item in enumerate(raw_candidates)
    ]
    if any(
        count > 1 for count in Counter(item["issue"] for item in candidates).values()
    ):
        global_blockers.append("duplicate-candidate-issue")

    active_issues = {item["issue"] for item in active}
    base_ready: list[dict[str, Any]] = []
    deferred: list[dict[str, Any]] = []
    for item in sorted(candidates, key=lambda value: (value["rank"], value["issue"])):
        blockers = list(item["blockers"])
        if item["issue"] in active_issues:
            blockers.append("already-dispatched")
        if (
            not item["repository_wide"]
            and not campaign_repository_wide
            and not hotset_is_within(
                item["hotset"],
                campaign_hotset,
                case_sensitive=case_sensitive_paths,
            )
        ):
            blockers.append("dispatch-hotset-outside-campaign")
        if blockers:
            deferred.append(
                {
                    "issue": item["issue"],
                    "rank": item["rank"],
                    "blockers": sorted(set(blockers)),
                    "next_action": (
                        "post-escalation-and-set-ready-for-human"
                        if "retry-limit-exhausted" in blockers
                        else "wait-for-reconciliation"
                    ),
                }
            )
        else:
            base_ready.append(item)

    review_required = any(
        item["verification_class"] in {"standard", "strict"}
        for item in [*base_ready, *active]
    )
    review_slot_reserved = bool(
        review_required
        and isinstance(review_agent, dict)
        and review_agent.get("exists") is False
    )
    if (
        review_required
        and isinstance(review_agent, dict)
        and review_agent.get("exists") is True
        and review_agent.get("reusable") is False
    ):
        global_blockers.append("review-agent-not-reusable")
    campaign_remaining = max(
        0, capacity["campaign_limit"] - capacity["campaign_active"]
    )
    global_remaining = max(0, capacity["global_limit"] - capacity["global_active"])
    dispatch_slots = min(campaign_remaining, global_remaining)
    if review_slot_reserved:
        dispatch_slots = max(0, dispatch_slots - 1)

    dispatches: list[dict[str, Any]] = []
    claims: list[dict[str, Any]] = [*active, *external]
    global_blockers = sorted(set(global_blockers))
    if global_blockers:
        global_deferred_blockers = [f"global:{blocker}" for blocker in global_blockers]
        for item in deferred:
            item["blockers"] = sorted(set(item["blockers"] + global_deferred_blockers))
        for item in base_ready:
            deferred.append(
                {
                    "issue": item["issue"],
                    "rank": item["rank"],
                    "blockers": global_deferred_blockers,
                    "next_action": "wait-for-reconciliation",
                }
            )
    if not global_blockers:
        for item in base_ready:
            blockers: list[str] = []
            if len(dispatches) >= dispatch_slots:
                blockers.append("capacity-exhausted")
            elif _conflicts(
                item["hotset"],
                item["repository_wide"],
                claims,
                case_sensitive_paths=case_sensitive_paths,
            ):
                blockers.append("hotset-conflict")
            if blockers:
                deferred.append(
                    {
                        "issue": item["issue"],
                        "rank": item["rank"],
                        "blockers": blockers,
                        "next_action": "wait-for-reconciliation",
                    }
                )
                continue
            dispatches.append(
                {
                    "issue": item["issue"],
                    "dispatch_id": item["dispatch_id"],
                    "attempt": item["attempt"],
                    "action": "claim-and-create-worker",
                    "hotset": item["hotset"],
                    "exclusive_scope": "repository"
                    if item["repository_wide"]
                    else "hotset",
                    "verification_class": item["verification_class"],
                }
            )
            claims.append(
                {
                    "hotset": item["hotset"],
                    "repository_wide": item["repository_wide"],
                }
            )

    deferred.sort(key=lambda value: (value["rank"], value["issue"]))
    return {
        "schema_version": SCHEMA_VERSION,
        "repository": repository,
        "campaign_id": campaign_id,
        "case_sensitive_paths": case_sensitive_paths,
        "status": "blocked" if global_blockers else "eligible",
        "automatic_execution": not global_blockers and bool(dispatches),
        "dispatches": dispatches if not global_blockers else [],
        "deferred": deferred,
        "global_blockers": global_blockers,
        "slots": {
            "campaign_remaining": campaign_remaining,
            "global_remaining": global_remaining,
            "review_slot_reserved": review_slot_reserved,
            "dispatch_slots": dispatch_slots,
            "selected": len(dispatches) if not global_blockers else 0,
        },
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    plan = subparsers.add_parser("plan-wave")
    plan.add_argument("--snapshot", type=Path, required=True)
    return parser


def main() -> int:
    arguments = _parser().parse_args()
    try:
        payload = json.loads(arguments.snapshot.read_text(encoding="utf-8"))
        report = plan_wave(payload)
    except (OSError, json.JSONDecodeError, ValueError) as error:
        print(json.dumps({"ok": False, "error": str(error)}, sort_keys=True))
        return 2
    print(json.dumps({"ok": True, "plan": report}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
