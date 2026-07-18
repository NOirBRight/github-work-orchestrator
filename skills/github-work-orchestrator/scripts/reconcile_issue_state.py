#!/usr/bin/env python3
"""Preview or apply idempotent GitHub Issue lifecycle and dependency repairs."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from github_client import GhError, fetch_issues, resolve_repo, run_gh
from ready_frontier import CANONICAL_LABELS, label_names


BLOCKED_BY_RE = re.compile(r"(?im)^\s*blocked\s+by\s*:\s*(.+)$")
ISSUE_REF_RE = re.compile(r"#(\d+)")


def textual_blockers(body: str) -> list[int]:
    blockers: set[int] = set()
    for match in BLOCKED_BY_RE.finditer(body or ""):
        blockers.update(int(number) for number in ISSUE_REF_RE.findall(match.group(1)))
    return sorted(blockers)


def parse_assignment(value: str, name: str) -> tuple[int, str]:
    if "=" not in value:
        raise argparse.ArgumentTypeError(f"{name} must use ISSUE=VALUE")
    issue_text, assigned = value.split("=", 1)
    try:
        issue = int(issue_text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"invalid Issue number: {issue_text}") from exc
    if issue <= 0 or not assigned:
        raise argparse.ArgumentTypeError(f"{name} must use positive ISSUE=VALUE")
    return issue, assigned


def parse_edge(value: str) -> tuple[int, int]:
    issue, blocker_text = parse_assignment(value, "dependency")
    try:
        blocker = int(blocker_text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"invalid blocker Issue number: {blocker_text}"
        ) from exc
    if blocker <= 0 or issue == blocker:
        raise argparse.ArgumentTypeError("dependency must use distinct positive Issues")
    return issue, blocker


def parse_exact(value: str) -> tuple[int, set[int]]:
    if "=" not in value:
        raise argparse.ArgumentTypeError(
            "exact-dependencies must use ISSUE=B1,B2 or ISSUE="
        )
    issue_text, blocker_text = value.split("=", 1)
    try:
        issue = int(issue_text)
        blockers = {
            int(number.strip()) for number in blocker_text.split(",") if number.strip()
        }
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"invalid exact dependency expression: {value}"
        ) from exc
    if issue <= 0 or any(blocker <= 0 for blocker in blockers) or issue in blockers:
        raise argparse.ArgumentTypeError(
            "exact dependencies must use distinct positive Issues"
        )
    return issue, blockers


def fetch_native_blockers(
    repo: str, issue: int, cwd: Path | None
) -> dict[int, dict[str, Any]]:
    payload = json.loads(
        run_gh(
            [
                "api",
                f"repos/{repo}/issues/{issue}/dependencies/blocked_by",
                "--paginate",
                "--slurp",
            ],
            cwd,
        )
        or "[]"
    )
    pages = payload if payload and isinstance(payload[0], list) else [payload]
    blockers: dict[int, dict[str, Any]] = {}
    for page in pages:
        for blocker in page:
            blockers[int(blocker["number"])] = blocker
    return blockers


def fetch_blocker_sets(
    repo: str, issue_numbers: set[int], cwd: Path | None
) -> dict[int, dict[int, dict[str, Any]]]:
    if not issue_numbers:
        return {}
    ordered = sorted(issue_numbers)
    with ThreadPoolExecutor(max_workers=min(4, len(ordered))) as executor:
        values = executor.map(
            lambda issue: fetch_native_blockers(repo, issue, cwd), ordered
        )
        return dict(zip(ordered, values, strict=True))


def build_actions(
    repo: str,
    cwd: Path | None,
    *,
    repair_safe: bool,
    statuses: list[tuple[int, str]],
    add_labels: list[tuple[int, str]],
    remove_labels: list[tuple[int, str]],
    dependencies: list[tuple[int, int]],
    exact_dependencies: list[tuple[int, set[int]]],
    limit: int,
) -> tuple[list[dict[str, Any]], dict[int, dict[str, Any]]]:
    issues = fetch_issues(repo, cwd, state="all", limit=limit)
    by_number = {int(issue["number"]): issue for issue in issues}
    actions: list[dict[str, Any]] = []

    def require_issue(number: int) -> dict[str, Any]:
        if number not in by_number:
            raise GhError(f"Issue #{number} was not found within the query limit")
        return by_number[number]

    if repair_safe:
        for issue in issues:
            if str(issue["state"]).upper() != "CLOSED":
                continue
            for label in sorted(label_names(issue) & CANONICAL_LABELS):
                actions.append(
                    {
                        "action": "remove_label",
                        "issue": int(issue["number"]),
                        "label": label,
                        "reason": "closed Issue retains lifecycle label",
                    }
                )

    normalized_statuses: dict[int, str] = {}
    for issue_number, status in statuses:
        if status not in CANONICAL_LABELS:
            raise GhError(f"unsupported lifecycle label: {status}")
        issue = require_issue(issue_number)
        if str(issue["state"]).upper() != "OPEN":
            raise GhError(
                f"cannot set lifecycle status on closed Issue #{issue_number}"
            )
        previous = normalized_statuses.setdefault(issue_number, status)
        if previous != status:
            raise GhError(f"conflicting desired statuses for Issue #{issue_number}")

    for issue_number, desired in sorted(normalized_statuses.items()):
        current = label_names(require_issue(issue_number))
        for label in sorted((current & CANONICAL_LABELS) - {desired}):
            actions.append(
                {
                    "action": "remove_label",
                    "issue": issue_number,
                    "label": label,
                    "reason": f"replace lifecycle status with {desired}",
                }
            )
        if desired not in current:
            actions.append(
                {
                    "action": "add_label",
                    "issue": issue_number,
                    "label": desired,
                    "reason": "set canonical lifecycle status",
                }
            )

    for issue_number, label in add_labels:
        issue = require_issue(issue_number)
        if label in CANONICAL_LABELS:
            raise GhError("use --status for canonical lifecycle labels")
        if label not in label_names(issue):
            actions.append(
                {
                    "action": "add_label",
                    "issue": issue_number,
                    "label": label,
                    "reason": "add existing repository taxonomy",
                }
            )

    for issue_number, label in remove_labels:
        issue = require_issue(issue_number)
        if label in CANONICAL_LABELS:
            raise GhError("use --status for canonical lifecycle labels")
        if label in label_names(issue):
            actions.append(
                {
                    "action": "remove_label",
                    "issue": issue_number,
                    "label": label,
                    "reason": "remove incorrect repository taxonomy",
                }
            )

    additive: set[tuple[int, int]] = set(dependencies)
    if repair_safe:
        for issue in issues:
            if str(issue["state"]).upper() != "OPEN":
                continue
            issue_number = int(issue["number"])
            additive.update(
                (issue_number, blocker)
                for blocker in textual_blockers(issue.get("body") or "")
            )

    exact: dict[int, set[int]] = {}
    for issue_number, blockers in exact_dependencies:
        require_issue(issue_number)
        previous = exact.setdefault(issue_number, set(blockers))
        if previous != blockers:
            raise GhError(
                f"conflicting exact dependency sets for Issue #{issue_number}"
            )
    for issue_number, blocker in additive:
        require_issue(issue_number)
        require_issue(blocker)
        if issue_number == blocker:
            raise GhError(f"Issue #{issue_number} cannot block itself")

    dependency_issues = {issue for issue, _ in additive} | set(exact)
    current_sets = fetch_blocker_sets(repo, dependency_issues, cwd)
    desired_additions = set(additive)
    for issue_number, blockers in exact.items():
        desired_additions.update((issue_number, blocker) for blocker in blockers)

    for issue_number, blocker in sorted(desired_additions):
        if blocker not in current_sets.get(issue_number, {}):
            actions.append(
                {
                    "action": "add_dependency",
                    "issue": issue_number,
                    "blocker": blocker,
                    "reason": "materialize intended native dependency",
                }
            )

    for issue_number, desired in sorted(exact.items()):
        current = set(current_sets.get(issue_number, {}))
        for blocker in sorted(current - desired):
            actions.append(
                {
                    "action": "remove_dependency",
                    "issue": issue_number,
                    "blocker": blocker,
                    "reason": "remove dependency absent from explicit exact graph",
                }
            )

    deduplicated: dict[tuple[Any, ...], dict[str, Any]] = {}
    for action in actions:
        key = (
            action["action"],
            action["issue"],
            action.get("label"),
            action.get("blocker"),
        )
        deduplicated[key] = action
    actions = list(deduplicated.values())

    order = {
        "add_label": 0,
        "remove_label": 1,
        "add_dependency": 2,
        "remove_dependency": 3,
    }
    actions.sort(
        key=lambda action: (
            int(action["issue"]),
            order[action["action"]],
            str(action.get("label", action.get("blocker", ""))),
        )
    )
    return actions, by_number


def run_write(arguments: list[str], cwd: Path | None) -> None:
    command = ["gh", *arguments]
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
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise GhError(f"`{' '.join(command)}` failed: {detail}")


def issue_database_id(
    repo: str,
    issue: int,
    cwd: Path | None,
    cache: dict[int, int],
) -> int:
    if issue not in cache:
        value = run_gh(
            ["api", f"repos/{repo}/issues/{issue}", "--jq", ".id"], cwd
        ).strip()
        cache[issue] = int(value)
    return cache[issue]


def apply_actions(
    repo: str, cwd: Path | None, actions: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    applied: list[dict[str, Any]] = []
    id_cache: dict[int, int] = {}
    for action in actions:
        kind = action["action"]
        issue = int(action["issue"])
        if kind == "remove_label":
            run_write(
                [
                    "issue",
                    "edit",
                    str(issue),
                    "--repo",
                    repo,
                    "--remove-label",
                    str(action["label"]),
                ],
                cwd,
            )
        elif kind == "add_label":
            run_write(
                [
                    "issue",
                    "edit",
                    str(issue),
                    "--repo",
                    repo,
                    "--add-label",
                    str(action["label"]),
                ],
                cwd,
            )
        elif kind in {"add_dependency", "remove_dependency"}:
            blocker = int(action["blocker"])
            blocker_id = issue_database_id(repo, blocker, cwd, id_cache)
            if kind == "add_dependency":
                run_write(
                    [
                        "api",
                        "--method",
                        "POST",
                        f"repos/{repo}/issues/{issue}/dependencies/blocked_by",
                        "-F",
                        f"issue_id={blocker_id}",
                    ],
                    cwd,
                )
            else:
                run_write(
                    [
                        "api",
                        "--method",
                        "DELETE",
                        (
                            f"repos/{repo}/issues/{issue}/dependencies/"
                            f"blocked_by/{blocker_id}"
                        ),
                    ],
                    cwd,
                )
        else:
            raise GhError(f"unsupported reconciliation action: {kind}")
        applied.append(action)
    return applied


def print_human(repo: str, mode: str, actions: list[dict[str, Any]]) -> None:
    print(f"Repository: {repo}")
    print(f"Mode: {mode}")
    print(f"Actions ({len(actions)}):")
    for action in actions:
        target = action.get("label", f"#{action.get('blocker')}")
        print(f"  #{action['issue']} {action['action']} {target} — {action['reason']}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", help="GitHub owner/repository; infer from cwd")
    parser.add_argument("--cwd", type=Path, help="Repository working directory")
    parser.add_argument("--limit", type=int, default=1000)
    parser.add_argument(
        "--repair-safe",
        action="store_true",
        help="remove closed lifecycle labels and materialize textual blockers",
    )
    parser.add_argument(
        "--status",
        action="append",
        default=[],
        metavar="ISSUE=LABEL",
        help="set exactly one canonical lifecycle label",
    )
    parser.add_argument(
        "--dependency",
        action="append",
        default=[],
        metavar="ISSUE=BLOCKER",
        help="add a native blocked-by edge",
    )
    parser.add_argument(
        "--add-label",
        action="append",
        default=[],
        metavar="ISSUE=LABEL",
        help="add an existing non-lifecycle repository label",
    )
    parser.add_argument(
        "--remove-label",
        action="append",
        default=[],
        metavar="ISSUE=LABEL",
        help="remove an existing non-lifecycle repository label",
    )
    parser.add_argument(
        "--exact-dependencies",
        action="append",
        default=[],
        metavar="ISSUE=B1,B2",
        help="make the native blocker set exactly match the supplied Issues",
    )
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--json", action="store_true", dest="as_json")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        repo = resolve_repo(args.repo, args.cwd)
        statuses = [parse_assignment(value, "status") for value in args.status]
        add_labels = [parse_assignment(value, "add-label") for value in args.add_label]
        remove_labels = [
            parse_assignment(value, "remove-label") for value in args.remove_label
        ]
        dependencies = [parse_edge(value) for value in args.dependency]
        exact_dependencies = [parse_exact(value) for value in args.exact_dependencies]
        actions, _ = build_actions(
            repo,
            args.cwd,
            repair_safe=args.repair_safe,
            statuses=statuses,
            add_labels=add_labels,
            remove_labels=remove_labels,
            dependencies=dependencies,
            exact_dependencies=exact_dependencies,
            limit=args.limit,
        )
        if args.apply and actions:
            apply_actions(repo, args.cwd, actions)
            remaining, _ = build_actions(
                repo,
                args.cwd,
                repair_safe=args.repair_safe,
                statuses=statuses,
                add_labels=add_labels,
                remove_labels=remove_labels,
                dependencies=dependencies,
                exact_dependencies=exact_dependencies,
                limit=args.limit,
            )
            if remaining:
                raise GhError(
                    "reconciliation verification found remaining actions: "
                    + json.dumps(remaining, ensure_ascii=False)
                )
        mode = "apply" if args.apply else "preview"
        if args.as_json:
            print(
                json.dumps(
                    {"repository": repo, "mode": mode, "actions": actions},
                    ensure_ascii=False,
                    indent=2,
                )
            )
        else:
            print_human(repo, mode, actions)
    except (GhError, json.JSONDecodeError, argparse.ArgumentTypeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
