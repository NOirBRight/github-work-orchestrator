#!/usr/bin/env python3
"""Validate GitHub Issue lifecycle state without modifying the repository."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from ready_frontier import (
    CANONICAL_LABELS,
    GhError,
    fetch_issues,
    label_names,
    native_open_blocker_counts,
    resolve_repo,
    textual_blockers,
)


ACTIVE_LABELS = {
    "needs-triage",
    "needs-info",
    "ready-for-agent",
    "ready-for-human",
}


def finding(
    severity: str, issue: dict[str, Any], code: str, message: str
) -> dict[str, Any]:
    return {
        "severity": severity,
        "code": code,
        "number": int(issue["number"]),
        "title": issue["title"],
        "url": issue["url"],
        "message": message,
    }


def validate(
    repo: str | None, cwd: Path | None, limit: int
) -> dict[str, Any]:
    resolved_repo = resolve_repo(repo, cwd)
    issues = fetch_issues(resolved_repo, cwd, state="all", limit=limit)
    states = {int(issue["number"]): str(issue["state"]).upper() for issue in issues}
    ready_numbers = [
        int(issue["number"])
        for issue in issues
        if str(issue["state"]).upper() == "OPEN"
        and "ready-for-agent" in label_names(issue)
    ]
    native_counts = native_open_blocker_counts(
        resolved_repo, ready_numbers, cwd
    )
    findings: list[dict[str, Any]] = []

    for issue in issues:
        labels = label_names(issue)
        statuses = sorted(labels & CANONICAL_LABELS)
        state = str(issue["state"]).upper()

        if state == "OPEN" and not statuses:
            findings.append(
                finding(
                    "warning",
                    issue,
                    "missing-status",
                    "open Issue has no canonical lifecycle label",
                )
            )
        if state == "OPEN" and len(statuses) > 1:
            findings.append(
                finding(
                    "error",
                    issue,
                    "multiple-statuses",
                    f"open Issue has conflicting labels: {', '.join(statuses)}",
                )
            )
        if state == "OPEN" and "wontfix" in statuses:
            findings.append(
                finding(
                    "warning",
                    issue,
                    "open-wontfix",
                    "wontfix Issue remains open",
                )
            )
        stale = sorted(labels & ACTIVE_LABELS) if state == "CLOSED" else []
        if stale:
            findings.append(
                finding(
                    "warning",
                    issue,
                    "closed-active-label",
                    f"closed Issue retains active labels: {', '.join(stale)}",
                )
            )

        if state == "OPEN" and "ready-for-agent" in labels:
            native_count = native_counts[int(issue["number"])]
            unresolved_body_refs = (
                [
                    ref
                    for ref in textual_blockers(issue.get("body") or "")
                    if states.get(ref, "UNKNOWN") != "CLOSED"
                ]
                if native_count is None
                else []
            )
            blocker_count = (
                native_count
                if native_count is not None
                else len(unresolved_body_refs)
            )
            if blocker_count:
                if native_count is not None:
                    detail = f"{native_count} native"
                else:
                    detail = "textual " + ", ".join(
                        f"#{ref}" for ref in unresolved_body_refs
                    )
                findings.append(
                    finding(
                        "warning",
                        issue,
                        "ready-with-blocker",
                        "ready-for-agent has open blockers: " + detail,
                    )
                )

    findings.sort(key=lambda item: (item["number"], item["severity"], item["code"]))
    return {
        "repository": resolved_repo,
        "issue_count": len(issues),
        "errors": sum(item["severity"] == "error" for item in findings),
        "warnings": sum(item["severity"] == "warning" for item in findings),
        "findings": findings,
    }


def print_human(report: dict[str, Any]) -> None:
    print(f"Repository: {report['repository']}")
    print(
        f"Issues: {report['issue_count']}; "
        f"errors: {report['errors']}; warnings: {report['warnings']}"
    )
    for item in report["findings"]:
        print(
            f"  {item['severity'].upper()} #{item['number']} "
            f"[{item['code']}] {item['message']}"
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", help="GitHub owner/repository; infer from cwd by default")
    parser.add_argument("--cwd", type=Path, help="Repository working directory")
    parser.add_argument("--limit", type=int, default=1000)
    parser.add_argument("--json", action="store_true", dest="as_json")
    parser.add_argument(
        "--strict-warnings",
        action="store_true",
        help="return exit code 1 when warnings are present",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        report = validate(args.repo, args.cwd, args.limit)
    except (GhError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if args.as_json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print_human(report)
    if report["errors"] or (args.strict_warnings and report["warnings"]):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
