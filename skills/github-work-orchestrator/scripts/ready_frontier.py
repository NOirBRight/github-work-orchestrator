#!/usr/bin/env python3
"""Compute a read-only GitHub Issue frontier for Paseo orchestration."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

from github_client import GhError, resolve_repo, run_gh
from hotset_policy import normalize_hotset_entry


CANONICAL_LABELS = {
    "needs-triage",
    "needs-info",
    "ready-for-agent",
    "ready-for-human",
    "wontfix",
}
HOTSET_HEADING_RE = re.compile(r"^\s*#{2,6}\s+expected\s+hotset\s*$", re.IGNORECASE)
HOTSET_BULLET_RE = re.compile(r"^\s*[-*]\s+`([^`]+)`\s*$")
ISSUE_SNAPSHOT_QUERY = """
query($owner: String!, $name: String!, $cursor: String, $pageSize: Int!) {
  repository(owner: $owner, name: $name) {
    issues(first: $pageSize, after: $cursor, orderBy: {field: CREATED_AT, direction: ASC}) {
      pageInfo { hasNextPage endCursor }
      nodes {
        number
        title
        url
        state
        body
        assignees(first: 100) { nodes { login } }
        labels(first: 100) { nodes { name } }
        blockedBy(first: 100) {
          pageInfo { hasNextPage }
          nodes { number state }
        }
      }
    }
  }
}
""".strip()


def label_names(issue: dict[str, Any]) -> set[str]:
    return {
        label["name"]
        for label in issue.get("labels", [])
        if isinstance(label, dict) and isinstance(label.get("name"), str)
    }


def expected_hotset(body: str) -> list[str] | None:
    """Parse strict backticked bullets from the Expected hotset section."""
    lines = (body or "").splitlines()
    start = next(
        (
            index + 1
            for index, line in enumerate(lines)
            if HOTSET_HEADING_RE.fullmatch(line)
        ),
        None,
    )
    if start is None:
        return None
    entries: list[str] = []
    for line in lines[start:]:
        if re.match(r"^\s*#{1,6}\s+", line):
            break
        if not line.strip():
            continue
        match = HOTSET_BULLET_RE.fullmatch(line)
        if not match:
            return None
        try:
            entries.append(normalize_hotset_entry(match.group(1)))
        except ValueError:
            return None
    return sorted(set(entries)) or None


def fetch_issue_snapshot(
    repo: str,
    cwd: Path | None,
    *,
    limit: int,
    runner=run_gh,
) -> list[dict[str, Any]]:
    """Read Issue state and dependency edges through paginated GraphQL."""
    if limit <= 0:
        raise ValueError("limit must be positive")
    try:
        owner, name = repo.split("/", 1)
    except ValueError as error:
        raise GhError("repository must be owner/name") from error
    if not owner or not name:
        raise GhError("repository must be owner/name")
    cursor: str | None = None
    issues: list[dict[str, Any]] = []
    while len(issues) < limit:
        page_size = min(100, limit - len(issues))
        arguments = [
            "api",
            "graphql",
            "-f",
            f"query={ISSUE_SNAPSHOT_QUERY}",
            "-F",
            f"owner={owner}",
            "-F",
            f"name={name}",
            "-F",
            f"pageSize={page_size}",
        ]
        if cursor is not None:
            arguments.extend(["-F", f"cursor={cursor}"])
        payload = json.loads(runner(arguments, cwd) or "{}")
        repository = payload.get("data", {}).get("repository")
        connection = repository.get("issues") if isinstance(repository, dict) else None
        if not isinstance(connection, dict) or not isinstance(
            connection.get("nodes"), list
        ):
            raise GhError("GitHub GraphQL response omitted repository Issues")
        for node in connection["nodes"]:
            if not isinstance(node, dict):
                raise GhError("GitHub GraphQL returned an invalid Issue node")
            blocked_by = node.get("blockedBy")
            blocked_nodes = (
                blocked_by.get("nodes", []) if isinstance(blocked_by, dict) else []
            )
            blocked_page = (
                blocked_by.get("pageInfo", {}) if isinstance(blocked_by, dict) else {}
            )
            issues.append(
                {
                    "number": node.get("number"),
                    "title": node.get("title"),
                    "url": node.get("url"),
                    "state": node.get("state"),
                    "body": node.get("body") or "",
                    "assignees": node.get("assignees", {}).get("nodes", []),
                    "labels": node.get("labels", {}).get("nodes", []),
                    "open_dependencies": sorted(
                        int(dependency["number"])
                        for dependency in blocked_nodes
                        if isinstance(dependency, dict)
                        and dependency.get("state") == "OPEN"
                        and isinstance(dependency.get("number"), int)
                    ),
                    "dependencies_complete": blocked_page.get("hasNextPage") is False,
                }
            )
        page_info = connection.get("pageInfo")
        if not isinstance(page_info, dict) or page_info.get("hasNextPage") is not True:
            break
        next_cursor = page_info.get("endCursor")
        if not isinstance(next_cursor, str) or not next_cursor:
            raise GhError("GitHub GraphQL pagination cursor is missing")
        cursor = next_cursor
    return sorted(issues, key=lambda issue: int(issue["number"]))


def classify_frontier(
    issues: list[dict[str, Any]],
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
            "expected_hotset": expected_hotset(issue.get("body") or "") or [],
        }
        item["hotset_evidence"] = (
            "strict" if item["expected_hotset"] else "missing-or-invalid"
        )
        if statuses != ["ready-for-agent"]:
            item["reason"] = "ready-for-agent is combined with another status label"
            result["invalid"].append(item)
            continue

        dependencies = issue.get("open_dependencies")
        if not isinstance(dependencies, list) or any(
            not isinstance(value, int) or isinstance(value, bool) or value <= 0
            for value in dependencies
        ):
            item["reason"] = "native dependency evidence is invalid"
            result["invalid"].append(item)
            continue
        item["dependency_check"] = "native-graphql"
        item["open_dependencies"] = sorted(set(dependencies))
        item["open_blocker_count"] = len(item["open_dependencies"])
        if issue.get("dependencies_complete") is not True:
            item["dependency_check"] = "native-graphql-incomplete"
            item["open_blocker_count"] = max(1, item["open_blocker_count"])

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
    all_issues = fetch_issue_snapshot(resolved_repo, cwd, limit=limit)
    candidates = [
        issue
        for issue in all_issues
        if str(issue.get("state")).upper() == "OPEN" and label in label_names(issue)
    ]
    classified = classify_frontier(candidates)
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
    parser.add_argument(
        "--repo", help="GitHub owner/repository; infer from cwd by default"
    )
    parser.add_argument("--cwd", type=Path, help="Repository working directory")
    parser.add_argument("--label", default="ready-for-agent")
    parser.add_argument("--limit", type=int, default=1000)
    parser.add_argument("--json", action="store_true", dest="as_json")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        frontier = build_frontier(args.repo, args.cwd, args.label, args.limit)
    except (GhError, json.JSONDecodeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if args.as_json:
        print(json.dumps(frontier, ensure_ascii=False, indent=2))
    else:
        print_human(frontier)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
