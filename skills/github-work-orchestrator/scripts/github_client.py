#!/usr/bin/env python3
"""Small read-only GitHub CLI client shared by GWO policy commands."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import time
from typing import Any


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
    limit: int = 1000,
) -> list[dict[str, Any]]:
    fields = "number,title,url,state,body,assignees,labels"
    payload = json.loads(
        run_gh(
            [
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
            ],
            cwd,
        )
        or "[]"
    )
    if not isinstance(payload, list):
        raise GhError("GitHub Issue query did not return an array")
    return sorted(payload, key=lambda issue: int(issue["number"]))
