#!/usr/bin/env python3
"""Validate and render one canonical, retry-stable Worker signal."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
from typing import Any


STATES = {
    "DISCUSSION_REQUIRED",
    "BLOCKED",
    "PR_OPENED",
    "READY_FOR_REVIEW",
    "STOPPED",
}
VERIFICATION_CLASSES = {"fast", "standard", "strict"}
BASE_FIELDS = (
    "state",
    "issue",
    "branch",
    "commit",
    "pr",
    "verification_class",
    "verification",
    "phase_timings",
    "full_suite_runs",
    "review_runs",
    "scope_delta",
    "hotset",
)


def _text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def validate_signal(payload: Any) -> list[str]:
    if not isinstance(payload, dict):
        return ["signal-must-be-object"]
    errors: list[str] = []
    for field in BASE_FIELDS:
        if field not in payload:
            errors.append(f"missing:{field}")
    if payload.get("state") not in STATES:
        errors.append("invalid-state")
    if payload.get("verification_class") not in VERIFICATION_CLASSES:
        errors.append("invalid-verification-class")
    for field in ("issue", "branch", "commit", "pr", "verification", "scope_delta"):
        if not _text(payload.get(field)):
            errors.append(f"missing-or-empty:{field}")
    timings = payload.get("phase_timings")
    if not isinstance(timings, dict):
        errors.append("phase-timings-must-be-object")
    else:
        for phase in ("plan", "implementation", "verification", "waiting"):
            if not _text(timings.get(phase)):
                errors.append(f"missing-phase:{phase}")
    if not isinstance(payload.get("full_suite_runs"), int) or payload.get("full_suite_runs", -1) < 0:
        errors.append("invalid-full-suite-runs")
    if payload.get("review_runs") != 0:
        errors.append("worker-review-runs-must-be-zero")
    hotset = payload.get("hotset")
    if not isinstance(hotset, list) or not hotset or not all(_text(item) for item in hotset):
        errors.append("hotset-must-be-nonempty-text-list")
    if payload.get("state") == "DISCUSSION_REQUIRED":
        for field in ("decision", "options", "recommendation", "safe_work"):
            if not _text(payload.get(field)):
                errors.append(f"missing-or-empty:{field}")
    elif not _text(payload.get("blocker_next_action")):
        errors.append("missing-or-empty:blocker_next_action")
    return sorted(set(errors))


def stable_signal_id(payload: dict[str, Any]) -> str:
    canonical = json.dumps(
        payload,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return f"worker-{hashlib.sha256(canonical).hexdigest()[:16]}"


def render_signal(payload: dict[str, Any]) -> str:
    errors = validate_signal(payload)
    if errors:
        raise ValueError(",".join(errors))
    timings = payload["phase_timings"]
    lines = [
        "WORKER_SIGNAL",
        f"- Signal-ID: {stable_signal_id(payload)}",
        f"- State: {payload['state']}",
        f"- Issue: {payload['issue']}",
        f"- Branch: {payload['branch']}",
        f"- Commit: {payload['commit']}",
        f"- PR: {payload['pr']}",
        f"- Verification-Class: {payload['verification_class']}",
        f"- Verification: {payload['verification']}",
        "- Phase-Timings: " + "; ".join(
            f"{phase}={timings[phase]}"
            for phase in ("plan", "implementation", "verification", "waiting")
        ),
        f"- Full-Suite-Runs: {payload['full_suite_runs']}",
        "- Review-Runs: 0",
        f"- Scope-Delta: {payload['scope_delta']}",
        f"- Hotset: {'; '.join(payload['hotset'])}",
    ]
    if payload["state"] == "DISCUSSION_REQUIRED":
        lines.extend(
            [
                f"- Decision: {payload['decision']}",
                f"- Options: {payload['options']}",
                f"- Recommendation: {payload['recommendation']}",
                f"- Safe work: {payload['safe_work']}",
            ]
        )
    else:
        lines.append(f"- Blocker/next action: {payload['blocker_next_action']}")
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, help="JSON file; stdin when omitted")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        text = args.input.read_text(encoding="utf-8") if args.input else sys.stdin.read()
        payload = json.loads(text)
        print(render_signal(payload))
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(
            json.dumps(
                {"schema_version": 1, "status": "error", "errors": [str(exc)]},
                indent=2,
            )
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
