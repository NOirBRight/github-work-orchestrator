#!/usr/bin/env python3
"""Run deterministic, read-only Worker permission and repository preflight."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys
from typing import Any


FILESYSTEM_FULL = {"danger-full-access", "full", "full-access", "unrestricted"}
NETWORK_FULL = {"enabled", "full", "true", "unrestricted"}


class CommandError(RuntimeError):
    """A required read-only command failed."""

    def __init__(self, name: str, returncode: int, reason: str = "failed"):
        super().__init__(f"{name} {reason} with exit code {returncode}")
        self.name = name
        self.returncode = returncode
        self.reason = reason


def run_command(
    cwd: Path,
    name: str,
    arguments: list[str],
    *,
    timeout_seconds: float,
) -> str:
    try:
        result = subprocess.run(
            arguments,
            cwd=cwd,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=timeout_seconds,
        )
    except FileNotFoundError as exc:
        raise CommandError(name, 127) from exc
    except subprocess.TimeoutExpired as exc:
        raise CommandError(name, 124, "timed-out") from exc
    if result.returncode:
        raise CommandError(name, result.returncode)
    return result.stdout.strip()


def evaluate_preflight(
    *,
    expected_base: str,
    expected_branch: str | None,
    filesystem: str,
    network: str,
    approval: str,
    observed: dict[str, Any],
    require_github: bool,
) -> dict[str, Any]:
    checks = {
        "filesystem_unrestricted": filesystem.lower() in FILESYSTEM_FULL,
        "network_enabled": network.lower() in NETWORK_FULL,
        "approval_never": approval.lower() == "never",
        "head_matches_base": observed.get("head") == expected_base,
        "integration_ref_matches_base": (
            observed.get("integration_head") == expected_base
        ),
        "worktree_clean": observed.get("status") == "",
    }
    if expected_branch:
        checks["branch_matches"] = observed.get("branch") == expected_branch
    if require_github:
        checks["github_identity_available"] = bool(observed.get("github_login"))
        checks["github_repository_available"] = bool(
            observed.get("github_repository")
        )

    failures = sorted(name for name, passed in checks.items() if not passed)
    return {
        "schema_version": 1,
        "status": "passed" if not failures else "failed",
        "checks": checks,
        "failures": failures,
        "repository": observed.get("github_repository"),
        "github_login": observed.get("github_login"),
        "head": observed.get("head"),
        "branch": observed.get("branch"),
        "integration_head": observed.get("integration_head"),
    }


def collect(args: argparse.Namespace) -> dict[str, Any]:
    cwd = args.cwd.resolve()
    if not cwd.is_dir():
        raise CommandError("cwd", 2)
    observed: dict[str, Any] = {
        "head": run_command(
            cwd,
            "git-head",
            ["git", "rev-parse", "HEAD"],
            timeout_seconds=args.command_timeout_seconds,
        ),
        "branch": run_command(
            cwd,
            "git-branch",
            ["git", "branch", "--show-current"],
            timeout_seconds=args.command_timeout_seconds,
        ),
        "integration_head": run_command(
            cwd,
            "git-integration-ref",
            ["git", "rev-parse", args.integration_ref],
            timeout_seconds=args.command_timeout_seconds,
        ),
        "status": run_command(
            cwd,
            "git-status",
            ["git", "status", "--porcelain=v1"],
            timeout_seconds=args.command_timeout_seconds,
        ),
    }
    if args.require_github:
        observed["github_login"] = run_command(
            cwd,
            "github-identity",
            ["gh", "api", "user", "--jq", ".login"],
            timeout_seconds=args.command_timeout_seconds,
        )
        observed["github_repository"] = run_command(
            cwd,
            "github-repository",
            [
                "gh",
                "repo",
                "view",
                "--json",
                "nameWithOwner",
                "--jq",
                ".nameWithOwner",
            ],
            timeout_seconds=args.command_timeout_seconds,
        )
    return observed


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cwd", type=Path, required=True)
    parser.add_argument("--expected-base", required=True)
    parser.add_argument("--integration-ref", required=True)
    parser.add_argument("--expected-branch")
    parser.add_argument("--filesystem", required=True)
    parser.add_argument("--network", required=True)
    parser.add_argument("--approval", required=True)
    parser.add_argument("--require-github", action="store_true")
    parser.add_argument(
        "--command-timeout-seconds",
        type=float,
        default=15.0,
        help="per-command read-only timeout; defaults to 15 seconds",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.command_timeout_seconds <= 0:
        print(
            json.dumps(
                {
                    "schema_version": 1,
                    "status": "error",
                    "command": "configuration",
                    "returncode": 2,
                    "reason": "invalid-timeout",
                },
                indent=2,
            )
        )
        return 2
    try:
        observed = collect(args)
    except CommandError as exc:
        print(
            json.dumps(
                {
                    "schema_version": 1,
                    "status": "error",
                    "command": exc.name,
                    "returncode": exc.returncode,
                    "reason": exc.reason,
                },
                indent=2,
            )
        )
        return 2
    report = evaluate_preflight(
        expected_base=args.expected_base,
        expected_branch=args.expected_branch,
        filesystem=args.filesystem,
        network=args.network,
        approval=args.approval,
        observed=observed,
        require_github=args.require_github,
    )
    print(json.dumps(report, indent=2))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
