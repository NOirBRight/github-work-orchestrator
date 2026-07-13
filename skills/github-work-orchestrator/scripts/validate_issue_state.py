#!/usr/bin/env python3
"""Validate GitHub Issue lifecycle state without modifying the repository."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

from ready_frontier import (
    CANONICAL_LABELS,
    GhError,
    fetch_issues,
    label_names,
    resolve_repo,
)


ACTIVE_LABELS = {
    "needs-triage",
    "needs-info",
    "ready-for-agent",
    "ready-for-human",
}
CONTRACT_FIELDS = (
    "Execution-Contract",
    "Verification-Class",
    "Verification-Commands",
    "Manual-Evidence",
    "Architecture-Decision",
    "Review-Owner",
)


def parse_contract_fields(body: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for name in CONTRACT_FIELDS:
        match = re.search(rf"(?im)^\s*{re.escape(name)}\s*:\s*(.+?)\s*$", body)
        if match:
            fields[name] = match.group(1).strip()
    return fields


def execution_contract_findings(issue: dict[str, Any]) -> list[dict[str, Any]]:
    fields = parse_contract_fields(issue.get("body") or "")
    if not fields:
        return [
            finding(
                "warning",
                issue,
                "legacy-execution-contract",
                "ready Issue has no Execution-Contract: v2 metadata",
            )
        ]
    findings: list[dict[str, Any]] = []
    expected = {
        "Execution-Contract": {"v2"},
        "Verification-Class": {"fast", "standard", "strict"},
        "Architecture-Decision": {"resolved", "discussion-required"},
        "Review-Owner": {"orchestrator"},
    }
    for name in CONTRACT_FIELDS:
        if not fields.get(name):
            findings.append(
                finding(
                    "error",
                    issue,
                    "missing-execution-field",
                    f"v2 execution contract is missing {name}",
                )
            )
    for name, allowed in expected.items():
        value = fields.get(name)
        if value and value not in allowed:
            findings.append(
                finding(
                    "error",
                    issue,
                    "invalid-execution-field",
                    f"{name} has unsupported value {value!r}",
                )
            )
    if fields.get("Architecture-Decision") == "discussion-required":
        findings.append(
            finding(
                "error",
                issue,
                "open-architecture-decision",
                "ready Issue still requires an architecture decision",
            )
        )
    return findings


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
        if state == "OPEN" and statuses == ["ready-for-agent"]:
            findings.extend(execution_contract_findings(issue))
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
