#!/usr/bin/env python3
"""Validate one provider-neutral v3 GitHub/Paseo execution contract."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys
from typing import Any

from contract_schema import EXECUTION_MODES, ROLE_CATEGORIES, VERIFICATION_CLASSES

FORBIDDEN_RUNTIME_FIELDS = {
    "callback_task",
    "execution_lane",
    "model_binding",
    "model_binding_evidence",
    "model_binding_requirement",
    "model_binding_status",
    "model_profile",
    "model_reasoning_effort",
    "task_id",
}
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
CAMPAIGN_RE = re.compile(r"^[a-z0-9][a-z0-9-]{5,63}$")
DISPATCH_RE = re.compile(r"^[a-z0-9][a-z0-9-]{5,95}$")
ROOM_RE = re.compile(r"^gwo-[a-z0-9][a-z0-9-]{5,63}$")
FEATURE_BRANCH_RE = re.compile(r"^work/issue-[1-9][0-9]*-[a-z0-9][a-z0-9-]*$")

REQUIRED_TEXT = (
    "issue",
    "repository",
    "base_branch",
    "feature_branch",
    "manual_evidence",
    "execution_mode",
    "pr_target",
    "done_when",
)
PASEO_REQUIRED_TEXT = (
    "group_label",
    "campaign_id",
    "dispatch_id",
    "agent_role",
    "role_category",
    "room",
    "parent_agent_id",
    "runtime_mode_id",
)


def nonempty_text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def nonempty_text_list(value: Any) -> bool:
    return (
        isinstance(value, list)
        and bool(value)
        and all(nonempty_text(item) for item in value)
    )


def verification_plan(
    verification_class: str,
    *,
    manual_evidence: str = "none",
    phase: str = "candidate",
    boundary_changed: bool = False,
) -> dict[str, Any]:
    if verification_class not in VERIFICATION_CLASSES:
        raise ValueError("invalid verification class")
    if phase not in {"candidate", "review-fix"}:
        raise ValueError("invalid phase")
    pipeline = {
        "local_green_before_ci": True,
        "ci_run_mode": "one-per-locally-green-candidate",
        "integration_gates": "parallel",
        "post_merge_rebuild": "tree-delta-or-repository-requirement-only",
    }
    if phase == "candidate":
        return {
            "targeted_checks": True,
            "local_full_suite": verification_class in {"standard", "strict"},
            "manual_evidence": manual_evidence.strip().lower() != "none",
            "manual_evidence_timing": (
                "pre-merge"
                if manual_evidence.strip().lower() != "none"
                else "not-required"
            ),
            "worker_review_runs": 0,
            "orchestrator_review": (
                "direct" if verification_class == "fast" else "standards-spec"
            ),
            "formal_review_round_limit": 1,
            **pipeline,
        }
    return {
        "targeted_checks": True,
        "local_full_suite": boundary_changed,
        "manual_evidence": False,
        "manual_evidence_timing": "pre-merge-if-affected",
        "worker_review_runs": 0,
        "orchestrator_review": "delta-only",
        "formal_review_round_limit": 1,
        **pipeline,
    }


def validate_contract(contract: Any) -> dict[str, Any]:
    errors: list[str] = []
    if not isinstance(contract, dict):
        return {
            "schema_version": 1,
            "status": "invalid",
            "dispatchable": False,
            "errors": ["contract-must-be-object"],
        }

    for field in REQUIRED_TEXT:
        if not nonempty_text(contract.get(field)):
            errors.append(f"missing-or-empty:{field}")

    if contract.get("execution_contract") != "v3":
        errors.append("execution-contract-must-be-v3")
    verification_class = contract.get("verification_class")
    if verification_class not in VERIFICATION_CLASSES:
        errors.append("invalid-verification-class")
    if not nonempty_text_list(contract.get("verification_commands")):
        errors.append("verification-commands-must-be-nonempty-list")
    if not nonempty_text_list(contract.get("hotset")):
        errors.append("hotset-must-be-nonempty-list")
    if contract.get("architecture_decision") not in {
        "resolved",
        "discussion-required",
    }:
        errors.append("invalid-architecture-decision")
    if contract.get("review_owner") != "orchestrator":
        errors.append("review-owner-must-be-orchestrator")

    execution_mode = contract.get("execution_mode")
    if execution_mode not in EXECUTION_MODES:
        errors.append("invalid-execution-mode")
    if execution_mode == "paseo-agent":
        for field in PASEO_REQUIRED_TEXT:
            if not nonempty_text(contract.get(field)):
                errors.append(f"missing-or-empty:{field}")
        campaign_id = contract.get("campaign_id")
        dispatch_id = contract.get("dispatch_id")
        room = contract.get("room")
        if nonempty_text(campaign_id) and not CAMPAIGN_RE.fullmatch(campaign_id):
            errors.append("invalid-campaign-id")
        if nonempty_text(dispatch_id) and not DISPATCH_RE.fullmatch(dispatch_id):
            errors.append("invalid-dispatch-id")
        if nonempty_text(room) and not ROOM_RE.fullmatch(room):
            errors.append("invalid-room")
        if (
            nonempty_text(campaign_id)
            and nonempty_text(room)
            and room != f"gwo-{campaign_id}"
        ):
            errors.append("room-must-match-campaign")

        agent_role = contract.get("agent_role")
        role_category = contract.get("role_category")
        if agent_role not in ROLE_CATEGORIES:
            errors.append("invalid-agent-role")
        elif role_category not in ROLE_CATEGORIES[agent_role]:
            errors.append("role-category-mismatch")
        if contract.get("relationship") != "subagent":
            errors.append("relationship-must-be-subagent")
        if contract.get("notify_on_finish") is not True:
            errors.append("notify-on-finish-must-be-true")

    base_sha = contract.get("base_sha")
    if not isinstance(base_sha, str) or not SHA_RE.fullmatch(base_sha):
        errors.append("base-sha-must-be-lowercase-40-hex")
    if contract.get("base_branch") != "dev":
        errors.append("base-branch-must-be-dev")
    if contract.get("pr_target") != "dev":
        errors.append("pr-target-must-be-dev")
    feature_branch = contract.get("feature_branch")
    if nonempty_text(feature_branch) and not FEATURE_BRANCH_RE.fullmatch(feature_branch):
        errors.append("invalid-feature-branch")

    permissions = contract.get("permission_profile")
    if not isinstance(permissions, dict):
        errors.append("permission-profile-must-be-object")
    else:
        for field in ("filesystem", "network", "approval"):
            if not nonempty_text(permissions.get(field)):
                errors.append(f"missing-permission:{field}")
        if permissions.get("approval") != "never":
            errors.append("approval-must-be-never")
        if permissions.get("unexpected_request_fallback") != "parent":
            errors.append("permission-fallback-must-be-parent")

    for field in sorted(FORBIDDEN_RUNTIME_FIELDS & contract.keys()):
        errors.append(f"provider-specific-field-forbidden:{field}")
    if contract.get("architecture_decision") == "discussion-required":
        errors.append("architecture-decision-open")

    errors = sorted(set(errors))
    return {
        "schema_version": 1,
        "status": "valid" if not errors else "invalid",
        "dispatchable": not errors,
        "execution_mode": execution_mode,
        "agent_role": contract.get("agent_role"),
        "role_category": contract.get("role_category"),
        "verification_class": verification_class,
        "verification_plan": (
            verification_plan(
                verification_class,
                manual_evidence=contract.get("manual_evidence", "none"),
            )
            if verification_class in VERIFICATION_CLASSES
            else None
        ),
        "errors": errors,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, help="JSON file; read stdin when omitted")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        payload = args.input.read_text(encoding="utf-8") if args.input else sys.stdin.read()
        contract = json.loads(payload)
    except (OSError, json.JSONDecodeError) as exc:
        print(
            json.dumps(
                {
                    "schema_version": 1,
                    "status": "error",
                    "dispatchable": False,
                    "errors": [type(exc).__name__],
                },
                indent=2,
            )
        )
        return 2
    report = validate_contract(contract)
    print(json.dumps(report, indent=2))
    return 0 if report["dispatchable"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
