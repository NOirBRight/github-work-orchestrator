#!/usr/bin/env python3
"""Plan reusable, independent Spec and Quality review work for one Campaign."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
from typing import Any


SCHEMA_VERSION = 1
AXES = ("spec", "quality")
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
REPOSITORY_RE = re.compile(r"^[^/\s]+/[^/\s]+$")
CAMPAIGN_RE = re.compile(r"^[a-z0-9][a-z0-9-]{5,63}$")
IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{2,127}$")
VERIFICATION_CLASSES = {"fast", "standard", "strict"}
REVIEWER_STATUSES = {"idle", "running", "initializing", "error", "closed"}


def _text(name: str, value: Any, pattern: re.Pattern[str] | None = None) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be nonempty text")
    result = value.strip()
    if pattern is not None and not pattern.fullmatch(result):
        raise ValueError(f"{name} is invalid")
    return result


def _timestamp(value: Any) -> str:
    timestamp = _text("verified_ready_at", value)
    try:
        parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError("verified_ready_at must be ISO-8601") from error
    if parsed.tzinfo is None:
        raise ValueError("verified_ready_at must include a timezone")
    return (
        parsed.astimezone(timezone.utc)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z")
    )


def _candidate(value: Any, index: int) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"candidates[{index}] must be an object")
    issue = value.get("issue")
    if not isinstance(issue, int) or isinstance(issue, bool) or issue <= 0:
        raise ValueError(f"candidates[{index}].issue must be a positive integer")
    verification_class = value.get("verification_class")
    if verification_class not in VERIFICATION_CLASSES:
        raise ValueError(f"candidates[{index}].verification_class is invalid")
    review_round = value.get("review_round")
    if not isinstance(review_round, int) or isinstance(review_round, bool) or review_round < 1:
        raise ValueError(f"candidates[{index}].review_round must be positive")
    scope = value.get("scope")
    previous_sha = value.get("previous_candidate_sha")
    if scope == "full":
        if review_round != 1 or previous_sha is not None:
            raise ValueError("full review must be round 1 without previous_candidate_sha")
    elif scope == "delta":
        if (
            review_round < 2
            or not isinstance(previous_sha, str)
            or not SHA_RE.fullmatch(previous_sha)
        ):
            raise ValueError("delta review requires previous_candidate_sha")
    else:
        raise ValueError(f"candidates[{index}].scope is invalid")
    candidate_sha = _text(
        f"candidates[{index}].candidate_sha", value.get("candidate_sha"), SHA_RE
    )
    if previous_sha == candidate_sha:
        raise ValueError("delta candidate must differ from previous_candidate_sha")
    return {
        "issue": issue,
        "dispatch_id": _text(
            f"candidates[{index}].dispatch_id",
            value.get("dispatch_id"),
            IDENTIFIER_RE,
        ),
        "verified_ready_at": _timestamp(value.get("verified_ready_at")),
        "verification_class": verification_class,
        "candidate_sha": candidate_sha,
        "base_sha": _text(
            f"candidates[{index}].base_sha", value.get("base_sha"), SHA_RE
        ),
        "diff_sha256": _text(
            f"candidates[{index}].diff_sha256", value.get("diff_sha256"), SHA256_RE
        ),
        "acceptance_sha256": _text(
            f"candidates[{index}].acceptance_sha256",
            value.get("acceptance_sha256"),
            SHA256_RE,
        ),
        "review_round": review_round,
        "scope": scope,
        "previous_candidate_sha": previous_sha,
    }


def _reviewers(
    value: Any, *, repository: str, campaign_id: str, campaign_agent_id: str
) -> tuple[dict[str, dict[str, Any]], list[str]]:
    if not isinstance(value, dict) or set(value) != set(AXES):
        raise ValueError("reviewers must contain exactly spec and quality")
    result: dict[str, dict[str, Any]] = {}
    blockers: list[str] = []
    seen_agents: set[str] = set()
    for axis in AXES:
        item = value.get(axis)
        if not isinstance(item, dict) or item.get("axis") != axis:
            raise ValueError(f"{axis} reviewer evidence is invalid")
        if item.get("read_back") is not True:
            blockers.append(f"{axis}-reviewer-not-read-back")
        exists = item.get("exists")
        if not isinstance(exists, bool):
            raise ValueError(f"{axis} reviewer exists must be boolean")
        if not exists:
            if any(
                item.get(field) is not None
                for field in ("agent_id", "relationship", "parent_agent_id", "labels")
            ) or item.get("status") != "missing":
                blockers.append(f"{axis}-missing-reviewer-evidence-contradictory")
            result[axis] = {"exists": False, "agent_id": None, "status": "missing"}
            continue
        agent_id = _text(f"{axis}.agent_id", item.get("agent_id"), IDENTIFIER_RE)
        if agent_id in seen_agents:
            blockers.append("review-pair-agent-identity-duplicate")
        seen_agents.add(agent_id)
        status = item.get("status")
        if status not in REVIEWER_STATUSES:
            raise ValueError(f"{axis} reviewer status is invalid")
        if item.get("relationship") != "subagent" or item.get("parent_agent_id") != campaign_agent_id:
            blockers.append(f"{axis}-reviewer-parentage-invalid")
        labels = item.get("labels")
        required_labels = {
            "repository": repository,
            "campaign_id": campaign_id,
            "role": "review",
            "review_axis": axis,
        }
        if not isinstance(labels, dict) or any(
            labels.get(field) != expected
            for field, expected in required_labels.items()
        ):
            blockers.append(f"{axis}-reviewer-labels-invalid")
        if status in {"error", "closed"}:
            blockers.append(f"{axis}-reviewer-unavailable")
        result[axis] = {
            "exists": True,
            "agent_id": agent_id,
            "status": status,
            "labels": labels,
        }
    return result, blockers


def _capacity(value: Any, *, existing_reviewers: int) -> tuple[dict[str, int], list[str]]:
    if not isinstance(value, dict):
        raise ValueError("capacity must be an object")
    fields = (
        "campaign_active_agents",
        "campaign_agent_limit",
        "campaign_active_reviewers",
        "campaign_review_limit",
        "global_active_agents",
        "global_agent_limit",
    )
    result: dict[str, int] = {}
    for field in fields:
        item = value.get(field)
        if not isinstance(item, int) or isinstance(item, bool) or item < 0:
            raise ValueError(f"capacity.{field} must be nonnegative")
        result[field] = item
    blockers: list[str] = []
    if value.get("read_back") is not True:
        blockers.append("review-capacity-not-read-back")
    if (
        result["campaign_agent_limit"] < 1
        or result["campaign_review_limit"] != 2
        or result["global_agent_limit"] < 1
    ):
        blockers.append("review-capacity-limits-invalid")
    if (
        result["campaign_active_agents"] > result["campaign_agent_limit"]
        or result["campaign_active_reviewers"] > result["campaign_review_limit"]
        or result["global_active_agents"] > result["global_agent_limit"]
        or result["campaign_active_agents"] > result["global_active_agents"]
    ):
        blockers.append("review-capacity-counts-invalid")
    if (
        result["campaign_active_agents"] < 1
        or result["global_active_agents"] < result["campaign_active_agents"] + 1
    ):
        blockers.append("review-control-plane-counts-invalid")
    if result["campaign_active_reviewers"] != existing_reviewers:
        blockers.append("reviewer-count-contradicts-capacity")
    return result, blockers


def _active_review(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("active_review must be an object or null")
    if value.get("read_back") is not True:
        raise ValueError("active_review must be read back")
    lock = value.get("lock")
    reviewer_agent_ids = value.get("reviewer_agent_ids")
    if not isinstance(lock, dict) or set(lock) != {
        "dispatch_id",
        "candidate_sha",
        "base_sha",
        "diff_sha256",
        "acceptance_sha256",
        "review_round",
        "scope",
        "previous_candidate_sha",
    }:
        raise ValueError("active_review.lock must be complete")
    if not isinstance(reviewer_agent_ids, dict) or set(reviewer_agent_ids) != set(AXES):
        raise ValueError("active_review reviewer_agent_ids must contain both axes")
    review_round = lock.get("review_round")
    if not isinstance(review_round, int) or isinstance(review_round, bool) or review_round < 1:
        raise ValueError("active_review.lock.review_round must be positive")
    for field, pattern in (
        ("dispatch_id", IDENTIFIER_RE),
        ("candidate_sha", SHA_RE),
        ("base_sha", SHA_RE),
        ("diff_sha256", SHA256_RE),
        ("acceptance_sha256", SHA256_RE),
    ):
        _text(f"active_review.lock.{field}", lock.get(field), pattern)
    if lock.get("scope") not in {"full", "delta"}:
        raise ValueError("active_review.lock.scope is invalid")
    previous_sha = lock.get("previous_candidate_sha")
    if lock["scope"] == "full":
        if review_round != 1 or previous_sha is not None:
            raise ValueError("active full review lock is invalid")
    elif (
        review_round < 2
        or not isinstance(previous_sha, str)
        or not SHA_RE.fullmatch(previous_sha)
        or previous_sha == lock["candidate_sha"]
    ):
        raise ValueError("active delta review lock is invalid")
    reviewer_ids = {
        axis: _text(
            f"active_review.reviewer_agent_ids.{axis}",
            reviewer_agent_ids.get(axis),
            IDENTIFIER_RE,
        )
        for axis in AXES
    }
    return {
        "lock": lock,
        "reviewer_agent_ids": reviewer_ids,
    }


def _lock(candidate: dict[str, Any]) -> dict[str, Any]:
    return {
        field: candidate[field]
        for field in (
            "dispatch_id",
            "candidate_sha",
            "base_sha",
            "diff_sha256",
            "acceptance_sha256",
            "review_round",
            "scope",
            "previous_candidate_sha",
        )
    }


def plan_review(snapshot: Any) -> dict[str, Any]:
    if not isinstance(snapshot, dict):
        raise ValueError("snapshot must be an object")
    if snapshot.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("snapshot schema_version must be 1")
    repository = _text("repository", snapshot.get("repository"), REPOSITORY_RE)
    campaign_id = _text("campaign_id", snapshot.get("campaign_id"), CAMPAIGN_RE)
    campaign_agent_id = _text(
        "campaign_agent_id", snapshot.get("campaign_agent_id"), IDENTIFIER_RE
    )
    reviewers, blockers = _reviewers(
        snapshot.get("reviewers"),
        repository=repository,
        campaign_id=campaign_id,
        campaign_agent_id=campaign_agent_id,
    )
    existing_count = sum(1 for axis in AXES if reviewers[axis]["exists"])
    capacity, capacity_blockers = _capacity(
        snapshot.get("capacity"), existing_reviewers=existing_count
    )
    blockers.extend(capacity_blockers)
    active_review = _active_review(snapshot.get("active_review"))
    raw_candidates = snapshot.get("candidates")
    if not isinstance(raw_candidates, list):
        raise ValueError("candidates must be a list")
    candidates = sorted(
        (_candidate(item, index) for index, item in enumerate(raw_candidates)),
        key=lambda item: (item["verified_ready_at"], item["issue"]),
    )
    dispatch_ids = [item["dispatch_id"] for item in candidates]
    if len(dispatch_ids) != len(set(dispatch_ids)):
        blockers.append("duplicate-review-candidate")

    existing = [axis for axis in AXES if reviewers[axis]["exists"]]
    busy = [
        axis
        for axis in existing
        if reviewers[axis]["status"] in {"running", "initializing"}
    ]
    idle = [axis for axis in existing if reviewers[axis]["status"] == "idle"]
    if active_review is not None and set(busy) != set(AXES):
        blockers.append("active-review-pair-state-contradictory")
    if active_review is None and busy:
        blockers.append("busy-reviewer-without-active-review")
    if active_review is not None:
        if any(
            active_review["reviewer_agent_ids"].get(axis)
            != reviewers[axis].get("agent_id")
            for axis in AXES
        ):
            blockers.append("active-review-reviewer-identity-mismatch")
        same_dispatch = [
            item
            for item in candidates
            if item["dispatch_id"] == active_review["lock"]["dispatch_id"]
        ]
        if same_dispatch and not any(
            _lock(item) == active_review["lock"] for item in same_dispatch
        ):
            blockers.append("active-review-lock-mismatch")

    creation_actions: list[dict[str, Any]] = []
    review_actions: list[dict[str, Any]] = []
    actions: list[dict[str, Any]] = []
    selected: dict[str, Any] | None = None
    queue = list(candidates)
    if not blockers and candidates:
        head = candidates[0]
        if head["verification_class"] == "fast":
            selected = head
            queue = candidates[1:]
            actions.append(
                {
                    "action": "campaign-inline-dual-axis-review",
                    "issue": head["issue"],
                    "dispatch_id": head["dispatch_id"],
                    "lock": _lock(head),
                }
            )
        elif len(existing) < 2:
            missing = [axis for axis in AXES if not reviewers[axis]["exists"]]
            available = min(
                capacity["campaign_review_limit"]
                - capacity["campaign_active_reviewers"],
                capacity["campaign_agent_limit"] - capacity["campaign_active_agents"],
                capacity["global_agent_limit"] - capacity["global_active_agents"],
            )
            if available <= 0:
                blockers.append("review-capacity-insufficient")
            for axis in missing[: max(0, available)]:
                if not reviewers[axis]["exists"]:
                    reviewer_name = "Spec Reviewer" if axis == "spec" else "Quality Reviewer"
                    labels = {
                        "repository": repository,
                        "campaign_id": campaign_id,
                        "role": "review",
                        "review_axis": axis,
                    }
                    creation_actions.append(
                        {
                            "action": "create-paseo-reviewer",
                            "axis": axis,
                            "name": reviewer_name,
                            "relationship": "subagent",
                            "parent_agent_id": campaign_agent_id,
                            "workspace": "campaign-control",
                            "labels": labels,
                            "expected_readback": {
                                "name": reviewer_name,
                                "relationship": "subagent",
                                "parent_agent_id": campaign_agent_id,
                                "labels": labels,
                            },
                        }
                    )
            actions.extend(creation_actions)
        elif active_review is None and set(idle) == set(AXES):
            selected = head
            queue = candidates[1:]
            lock = _lock(head)
            for axis in AXES:
                review_actions.append(
                    {
                        "action": "dispatch-review-axis",
                        "axis": axis,
                        "agent_id": reviewers[axis]["agent_id"],
                        "issue": head["issue"],
                        "dispatch_id": head["dispatch_id"],
                        "lock": lock,
                    }
                )
            actions.extend(review_actions)

    return {
        "schema_version": SCHEMA_VERSION,
        "repository": repository,
        "campaign_id": campaign_id,
        "status": "protected" if blockers else "eligible",
        "automatic_execution": not blockers and bool(actions),
        "actions": actions if not blockers else [],
        "reviewer_creation_actions": creation_actions if not blockers else [],
        "review_dispatch_actions": review_actions if not blockers else [],
        "selected": selected,
        "active_review": active_review,
        "queue": queue,
        "blockers": sorted(set(blockers)),
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    plan = subparsers.add_parser("plan-review")
    plan.add_argument("--snapshot", type=Path, required=True)
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        snapshot = json.loads(args.snapshot.read_text(encoding="utf-8"))
        report = plan_review(snapshot)
    except (OSError, json.JSONDecodeError, ValueError) as error:
        print(json.dumps({"ok": False, "error": str(error)}, sort_keys=True))
        return 2
    print(json.dumps({"ok": True, "plan": report}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
