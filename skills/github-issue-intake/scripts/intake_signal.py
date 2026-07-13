#!/usr/bin/env python3
"""Validate and render one canonical, retry-stable Intake signal."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
from typing import Any


STATES = {"ISSUE_READY", "DUPLICATE", "NEEDS_INFO", "DISCUSSION_REQUIRED"}
FIELDS = ("state", "issue_topic", "repository", "evidence", "next_action")


def render_signal(payload: Any) -> str:
    if not isinstance(payload, dict):
        raise ValueError("signal-must-be-object")
    errors = [
        f"missing-or-empty:{field}"
        for field in FIELDS
        if not isinstance(payload.get(field), str) or not payload[field].strip()
    ]
    if payload.get("state") not in STATES:
        errors.append("invalid-state")
    if errors:
        raise ValueError(",".join(sorted(set(errors))))
    canonical = json.dumps(
        payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    signal_id = f"intake-{hashlib.sha256(canonical).hexdigest()[:16]}"
    return "\n".join(
        [
            "INTAKE_SIGNAL",
            f"- Signal-ID: {signal_id}",
            f"- State: {payload['state']}",
            f"- Issue/topic: {payload['issue_topic']}",
            f"- Repository: {payload['repository']}",
            f"- Evidence: {payload['evidence']}",
            f"- Next action: {payload['next_action']}",
        ]
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, help="JSON file; stdin when omitted")
    args = parser.parse_args()
    try:
        text = args.input.read_text(encoding="utf-8") if args.input else sys.stdin.read()
        print(render_signal(json.loads(text)))
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
