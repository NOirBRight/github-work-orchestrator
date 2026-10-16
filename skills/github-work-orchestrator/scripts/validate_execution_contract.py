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
MODEL_BINDING_REQUIREMENTS = {"best-effort", "exact-runtime"}
MODEL_BINDING_STATUSES = {
    "request-accepted",
    "runtime-verified",
    "rejected",
    "unknown",
}
NATIVE_WORKER_OPERATIONS = {"create", "follow-up"}
NATIVE_WORKER_PURPOSES = {"bootstrap", "full-contract"}
REQUEST_RECEIPTS = {"accepted", "rejected", "unknown"}
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
PROFILE_ROW_RE = re.compile(
    r"^\|\s*`(?P<profile>[^`]+)`\s*\|\s*"
    r"`(?P<model>[^`/]+?)\s*/\s*(?P<thinking>[^`]+)`\s*\|"
)
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


def model_profile_bindings() -> dict[str, tuple[str, str]]:
    """Load the one central profile table shipped with this Skill package."""
    profile_path = (
        Path(__file__).resolve().parents[1]
        / "references"
        / "shared"
        / "model-profiles.md"
    )
    try:
        lines = profile_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return {}
    bindings: dict[str, tuple[str, str]] = {}
    for line in lines:
        match = PROFILE_ROW_RE.match(line)
        if match:
            bindings[match["profile"]] = (
                match["model"].strip(),
                match["thinking"].strip(),
            )
    return bindings


def nonempty_text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def nonempty_text_list(value: Any) -> bool:
    return (
        isinstance(value, list)
        and bool(value)
        and all(nonempty_text(item) for item in value)
    )


def binding_text(model: str, thinking: str) -> str:
    return f"{model} / {thinking}"


def validate_native_worker_requests(
    value: Any,
    expected_binding: tuple[str, str] | None,
) -> list[str]:
    """Fail closed unless every recorded Worker request carries the binding."""
    if not isinstance(value, list) or not value:
        return ["native-worker-requests-must-be-nonempty-list"]

    errors: list[str] = []
    full_contract_bindings: list[tuple[str, str]] = []
    bootstrap_bindings: list[tuple[str, str]] = []
    for request in value:
        if not isinstance(request, dict):
            errors.append("native-worker-request-must-be-object")
            continue
        if request.get("operation") not in NATIVE_WORKER_OPERATIONS:
            errors.append("invalid-native-worker-request-operation")
        purpose = request.get("purpose")
        if purpose not in NATIVE_WORKER_PURPOSES:
            errors.append("invalid-native-worker-request-purpose")
        model = request.get("model")
        thinking = request.get("thinking")
        if not nonempty_text(model):
            errors.append("missing-native-worker-request:model")
        if not nonempty_text(thinking):
            errors.append("missing-native-worker-request:thinking")
            continue
        request_binding = (model.strip(), thinking.strip())
        if expected_binding is not None and request_binding != expected_binding:
            errors.append("model-binding-does-not-match-native-request")
        if purpose == "full-contract":
            full_contract_bindings.append(request_binding)
        elif purpose == "bootstrap":
            bootstrap_bindings.append(request_binding)

    if not full_contract_bindings:
        errors.append("missing-full-contract-model-request")
    elif any(
        bootstrap_binding != full_contract_bindings[0]
        for bootstrap_binding in bootstrap_bindings
    ):
        errors.append("model-binding-bootstrap-full-contract-mismatch")
    return errors


def validate_model_binding_evidence(
    value: Any,
    *,
    status: Any,
    expected_binding: tuple[str, str] | None,
) -> list[str]:
    """Validate request receipts without manufacturing runtime identity."""
    if not isinstance(value, dict):
        return ["model-binding-evidence-must-be-object"]

    errors: list[str] = []
    receipt = value.get("request_receipt")
    if receipt not in REQUEST_RECEIPTS:
        errors.append("invalid-model-binding-request-receipt")

    readback = value.get("effective_readback")
    if not isinstance(readback, dict):
        return errors + ["effective-readback-must-be-object"]
    available = readback.get("available")
    if not isinstance(available, bool):
        errors.append("effective-readback-availability-must-be-boolean")
        return errors

    observed_model = readback.get("model")
    observed_thinking = readback.get("thinking")
    if available:
        if not nonempty_text(observed_model):
            errors.append("missing-effective-readback:model")
        if not nonempty_text(observed_thinking):
            errors.append("missing-effective-readback:thinking")
        if (
            expected_binding is not None
            and nonempty_text(observed_model)
            and nonempty_text(observed_thinking)
            and (observed_model.strip(), observed_thinking.strip())
            != expected_binding
        ):
            errors.append("model-binding-runtime-contradiction")
    elif observed_model is not None or observed_thinking is not None:
        errors.append("unavailable-effective-readback-must-omit-binding")

    if status == "request-accepted":
        if receipt != "accepted":
            errors.append("request-accepted-status-requires-accepted-receipt")
        if available:
            errors.append(
                "request-accepted-status-requires-unavailable-effective-readback"
            )
        if not nonempty_text(value.get("limitation_note")):
            errors.append("request-accepted-status-requires-limitation-note")
    elif status == "runtime-verified":
        if receipt != "accepted":
            errors.append("runtime-verified-status-requires-accepted-receipt")
        if not available:
            errors.append("runtime-verified-status-requires-effective-readback")
    elif status == "rejected":
        errors.append("model-binding-rejected")
    elif status == "unknown":
        errors.append("model-binding-unknown")
    return errors


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
            "candidate_target_minutes": (
                30
                if verification_class == "fast"
                else 90 if verification_class == "standard" else None
            ),
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
        "candidate_target_minutes": 15,
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
