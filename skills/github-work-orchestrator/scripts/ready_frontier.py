#!/usr/bin/env python3
"""Compute a read-only GitHub Issue frontier for Codex orchestration."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


CANONICAL_LABELS = {
    "needs-triage",
    "needs-info",
    "ready-for-agent",
    "ready-for-human",
    "wontfix",
}
BLOCKED_BY_RE = re.compile(r"(?im)^\s*blocked\s+by\s*:\s*(.+)$")
ISSUE_REF_RE = re.compile(r"#(\d+)")
RETRYABLE_GH_ERRORS = (
    "connection",
    "dial tcp",
    "eof",
    "http 502",
    "http 503",
    "http 504",
    "stream error",
    "timed out",
    "timeout",
    "tls handshake",
)


class GhError(RuntimeError):
    """Raised when the GitHub CLI cannot return the requested state."""


def run_gh(arguments: list[str], cwd: Path | None = None) -> str:
    command = ["gh", *arguments]
    for attempt in range(3):
        try:
            result = subprocess.run(
                command,
                cwd=str(cwd) if cwd else None,
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
        except FileNotFoundError as exc:
            raise GhError("GitHub CLI `gh` is not installed or not on PATH") from exc
        if result.returncode == 0:
            return result.stdout
        detail = result.stderr.strip() or result.stdout.strip()
        retryable = any(token in detail.lower() for token in RETRYABLE_GH_ERRORS)
        if not retryable or attempt == 2:
            raise GhError(f"`{' '.join(command)}` failed: {detail}")
        time.sleep(0.5 * (2**attempt))
    raise AssertionError("unreachable")


def resolve_repo(repo: str | None, cwd: Path | None) -> str:
    if repo:
        return repo
    return run_gh(
        ["repo", "view", "--json", "nameWithOwner", "--jq", ".nameWithOwner"],
        cwd,
    ).strip()


def fetch_issues(
    repo: str,
    cwd: Path | None,
    *,
    state: str = "open",
    label: str | None = None,
    limit: int = 1000,
) -> list[dict[str, Any]]:
    fields = "number,title,url,state,body,assignees,labels"
    arguments = [
        "issue",
        "list",
        "--repo",
        repo,
        "--state",
        state,
        "--limit",
        str(limit),
        "--json",
        fields,
    ]
    if label:
        arguments.extend(["--label", label])
    payload = json.loads(run_gh(arguments, cwd) or "[]")
    return sorted(payload, key=lambda issue: int(issue["number"]))


def label_names(issue: dict[str, Any]) -> set[str]:
    return {
        label["name"]
        for label in issue.get("labels", [])
        if isinstance(label, dict) and isinstance(label.get("name"), str)
    }


def textual_blockers(body: str) -> list[int]:
    blockers: set[int] = set()
    for match in BLOCKED_BY_RE.finditer(body or ""):
        blockers.update(int(number) for number in ISSUE_REF_RE.findall(match.group(1)))
    return sorted(blockers)


def native_open_blocker_count(
    repo: str, number: int, cwd: Path | None
) -> int | None:
    payload = json.loads(run_gh(["api", f"repos/{repo}/issues/{number}"], cwd))
    summary = payload.get("issue_dependencies_summary")
    if not isinstance(summary, dict) or "blocked_by" not in summary:
        return None
    blocked_by = summary["blocked_by"]
    if isinstance(blocked_by, int):
        return blocked_by
    if isinstance(blocked_by, dict):
        for key in ("total_count", "open", "open_count"):
            value = blocked_by.get(key)
            if isinstance(value, int):
                return value
    return None


def native_open_blocker_counts(
    repo: str,
    numbers: list[int],
    cwd: Path | None,
    *,
    workers: int = 4,
) -> dict[int, int | None]:
    if not numbers:
        return {}
    worker_count = min(max(workers, 1), len(numbers))
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        counts = executor.map(
            lambda number: native_open_blocker_count(repo, number, cwd), numbers
        )
        return dict(zip(numbers, counts, strict=True))


def classify_frontier(
    issues: list[dict[str, Any]],
    all_issue_states: dict[int, str],
    native_counts: dict[int, int | None],
) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = {
        "ready": [],
        "claimed": [],
        "blocked": [],
        "invalid": [],
    }
    for issue in issues:
        number = int(issue["number"])
        labels = label_names(issue)
        statuses = sorted(labels & CANONICAL_LABELS)
        item = {
            "number": number,
            "title": issue["title"],
            "url": issue["url"],
            "assignees": sorted(
                assignee["login"]
                for assignee in issue.get("assignees", [])
                if isinstance(assignee, dict) and assignee.get("login")
            ),
            "status_labels": statuses,
        }
        if statuses != ["ready-for-agent"]:
            item["reason"] = "ready-for-agent is combined with another status label"
            result["invalid"].append(item)
            continue

        native_count = native_counts[number]
        body_refs = textual_blockers(issue.get("body") or "")
        if native_count is None:
            item["dependency_check"] = "body-fallback"
            unresolved_body_refs = [
                ref
                for ref in body_refs
                if all_issue_states.get(ref, "UNKNOWN") != "CLOSED"
            ]
            item["open_blocker_count"] = len(unresolved_body_refs)
            if unresolved_body_refs:
                item["unresolved_textual_blockers"] = unresolved_body_refs
        else:
            item["dependency_check"] = "native"
            item["open_blocker_count"] = native_count

        if item["assignees"]:
            item["reason"] = "assigned"
            result["claimed"].append(item)
        elif item["open_blocker_count"]:
            item["reason"] = "open blocker"
            result["blocked"].append(item)
        else:
            result["ready"].append(item)
    return result


def build_frontier(
    repo: str | None,
    cwd: Path | None,
    label: str,
    limit: int,
) -> dict[str, Any]:
    resolved_repo = resolve_repo(repo, cwd)
    candidates = fetch_issues(
        resolved_repo, cwd, state="open", label=label, limit=limit
    )
    all_issues = fetch_issues(resolved_repo, cwd, state="all", limit=limit)
    states = {int(issue["number"]): str(issue["state"]).upper() for issue in all_issues}
    candidate_numbers = [int(issue["number"]) for issue in candidates]
    native_counts = native_open_blocker_counts(
        resolved_repo, candidate_numbers, cwd
    )
    classified = classify_frontier(candidates, states, native_counts)
    return {
        "repository": resolved_repo,
        "query_label": label,
        **classified,
    }


def print_human(frontier: dict[str, Any]) -> None:
    print(f"Repository: {frontier['repository']}")
    for group in ("ready", "claimed", "blocked", "invalid"):
        items = frontier[group]
        print(f"{group.capitalize()} ({len(items)}):")
        for item in items:
            suffix = f" — {item['reason']}" if item.get("reason") else ""
            print(f"  #{item['number']} {item['title']}{suffix}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", help="GitHub owner/repository; infer from cwd by default")
    parser.add_argument("--cwd", type=Path, help="Repository working directory")
    parser.add_argument("--label", default="ready-for-agent")
    parser.add_argument("--limit", type=int, default=1000)
    parser.add_argument("--json", action="store_true", dest="as_json")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        frontier = build_frontier(args.repo, args.cwd, args.label, args.limit)
    except (GhError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if args.as_json:
        print(json.dumps(frontier, ensure_ascii=False, indent=2))
    else:
        print_human(frontier)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
