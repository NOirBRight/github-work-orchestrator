#!/usr/bin/env python3
"""Validate one normalized v2 GitHub Worker dispatch contract."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys
from typing import Any


VERIFICATION_CLASSES = {"fast", "standard", "strict"}
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
REQUIRED_TEXT = (
    "issue",
    "repository",
    "base_branch",
    "feature_branch",
    "manual_evidence",
    "model_profile",
    "model_binding",
    "model_binding_evidence",
    "callback_task",
    "pr_target",
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
    if phase == "candidate":
        return {
            "targeted_checks": True,
            "local_full_suite": verification_class in {"standard", "strict"},
            "manual_evidence": manual_evidence.strip().lower() != "none",
            "worker_review_runs": 0,
            "orchestrator_review": (
                "direct" if verification_class == "fast" else "standards-spec"
            ),
            "formal_review_round_limit": 1,
        }
    return {
        "targeted_checks": True,
        "local_full_suite": boundary_changed,
        "manual_evidence": False,
        "worker_review_runs": 0,
        "orchestrator_review": "delta-only",
        "formal_review_round_limit": 1,
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

    if contract.get("execution_contract") != "v2":
        errors.append("execution-contract-must-be-v2")
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
    if contract.get("model_binding_status") != "verified":
        errors.append("model-binding-must-be-verified")
    base_sha = contract.get("base_sha")
    if not isinstance(base_sha, str) or not SHA_RE.fullmatch(base_sha):
        errors.append("base-sha-must-be-lowercase-40-hex")

    permissions = contract.get("permission_profile")
    if not isinstance(permissions, dict):
        errors.append("permission-profile-must-be-object")
    else:
        for field in ("filesystem", "network", "approval"):
            if not nonempty_text(permissions.get(field)):
                errors.append(f"missing-permission:{field}")

    if contract.get("architecture_decision") == "discussion-required":
        errors.append("architecture-decision-open")

    errors = sorted(set(errors))
    return {
        "schema_version": 1,
        "status": "valid" if not errors else "invalid",
        "dispatchable": not errors,
        "verification_class": verification_class,
        "verification_plan": (
            verification_plan(
                verification_class,
                manual_evidence=contract["manual_evidence"],
            )
            if verification_class in VERIFICATION_CLASSES
            else None
        ),
        "errors": errors,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input",
        type=Path,
        help="JSON file; read stdin when omitted",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        payload = (
            args.input.read_text(encoding="utf-8")
            if args.input
            else sys.stdin.read()
        )
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
