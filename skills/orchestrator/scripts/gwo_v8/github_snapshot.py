"""Production GitHub adapter for one complete ready-Ticket snapshot."""

from __future__ import annotations

import json
import re
import subprocess
from typing import Any, Mapping, Protocol

from ._canonical import CanonicalJsonError, digest_value, strict_json_loads
from .activation import GitHubContentClient
from .plan_control import PlanControlError


_REPOSITORY = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_BRANCH = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]*$")
_ISSUE_KEY = re.compile(r"^issue:([1-9][0-9]*)$")
_ISSUE_SHORT = re.compile(r"^#([1-9][0-9]*)$")


class GitHubIssueReadClient(Protocol):
    def read_issue(self, repository: str, number: int) -> Mapping[str, Any]: ...

    def read_blockers(
        self,
        repository: str,
        number: int,
    ) -> tuple[Mapping[str, Any], ...]: ...

    def read_branch_oid(self, repository: str, branch: str) -> str: ...


class GitHubCliIssueReadClient:
    """Authenticated GitHub issue/dependency reads through ``gh api``."""

    def __init__(
        self,
        executable: str = "gh",
        *,
        command_timeout_seconds: int = 30,
    ):
        if type(executable) is not str or not executable:
            raise ValueError("GitHub executable must be exact text")
        if (
            type(command_timeout_seconds) is not int
            or command_timeout_seconds < 1
        ):
            raise ValueError("GitHub command timeout must be positive")
        self.executable = executable
        self.command_timeout_seconds = command_timeout_seconds

    def _read_json(self, endpoint: str) -> Any:
        try:
            result = subprocess.run(
                [self.executable, "api", endpoint],
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=self.command_timeout_seconds,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise PlanControlError(
                "GITHUB_SNAPSHOT_UNAVAILABLE",
                "GitHub snapshot command is unavailable",
            ) from error
        if result.returncode != 0:
            raise PlanControlError(
                "GITHUB_SNAPSHOT_UNAVAILABLE",
                "GitHub snapshot API read failed",
            )
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError as error:
            raise PlanControlError(
                "GITHUB_SNAPSHOT_INVALID",
                "GitHub snapshot API returned malformed JSON",
            ) from error

    def read_issue(self, repository: str, number: int) -> Mapping[str, Any]:
        value = self._read_json(f"repos/{repository}/issues/{number}")
        if type(value) is not dict:
            raise PlanControlError(
                "GITHUB_SNAPSHOT_INVALID",
                "GitHub issue readback is not an object",
            )
        return value

    def read_blockers(
        self,
        repository: str,
        number: int,
    ) -> tuple[Mapping[str, Any], ...]:
        value = self._read_json(
            f"repos/{repository}/issues/{number}/dependencies/blocked_by"
        )
        if type(value) is not list or any(type(item) is not dict for item in value):
            raise PlanControlError(
                "GITHUB_SNAPSHOT_INVALID",
                "GitHub issue blockers readback is not a list",
            )
        return tuple(value)

    def read_branch_oid(self, repository: str, branch: str) -> str:
        value = self._read_json(f"repos/{repository}/git/ref/heads/{branch}")
        if (
            type(value) is not dict
            or type(value.get("object")) is not dict
            or type(value["object"].get("sha")) is not str
            or not value["object"]["sha"]
        ):
            raise PlanControlError(
                "GITHUB_SNAPSHOT_INVALID",
                "GitHub target branch readback omitted its commit",
            )
        return value["object"]["sha"]


def _issue_number(repository: str, ready_ref: str) -> int:
    match = _ISSUE_KEY.fullmatch(ready_ref) or _ISSUE_SHORT.fullmatch(ready_ref)
    if match is not None:
        return int(match.group(1))
    prefix = f"https://github.com/{repository}/issues/"
    if ready_ref.startswith(prefix):
        suffix = ready_ref.removeprefix(prefix)
        if suffix.isdigit() and not suffix.startswith("0") and int(suffix) > 0:
            return int(suffix)
    raise PlanControlError(
        "READY_REFS_INVALID",
        "GitHub ready reference must be issue:N, #N, or an exact repository issue URL",
    )


def _label_names(value: object) -> list[str]:
    if type(value) is not list:
        raise PlanControlError(
            "GITHUB_SNAPSHOT_INVALID",
            "GitHub issue labels are malformed",
        )
    labels = []
    for item in value:
        name = item if type(item) is str else (
            item.get("name") if type(item) is dict else None
        )
        if type(name) is not str or not name:
            raise PlanControlError(
                "GITHUB_SNAPSHOT_INVALID",
                "GitHub issue label omitted its exact name",
            )
        labels.append(name)
    if len(set(labels)) != len(labels):
        raise PlanControlError(
            "GITHUB_SNAPSHOT_INVALID",
            "GitHub issue labels repeat a name",
        )
    return sorted(labels)


class GitHubReadySnapshotSource:
    """Freeze GitHub issues, native blockers, policy, and target branch."""

    def __init__(
        self,
        *,
        content_client: GitHubContentClient,
        issue_client: GitHubIssueReadClient,
        control_branch: str,
        target_branch: str,
        policy_path: str = ".gwo-v8/policy-witness.json",
    ):
        if (
            type(control_branch) is not str
            or _BRANCH.fullmatch(control_branch) is None
            or type(target_branch) is not str
            or _BRANCH.fullmatch(target_branch) is None
            or type(policy_path) is not str
            or not policy_path.strip("/")
        ):
            raise PlanControlError(
                "PLAN_CONTROL_COMPOSITION_INVALID",
                "GitHub snapshot branch or Policy Witness path is invalid",
            )
        self.content_client = content_client
        self.issue_client = issue_client
        self.control_branch = control_branch
        self.target_branch = target_branch
        self.policy_path = policy_path.strip("/")

    def snapshot(
        self,
        repository: str,
        ready_refs: tuple[str, ...],
    ) -> Mapping[str, Any]:
        if (
            type(repository) is not str
            or _REPOSITORY.fullmatch(repository) is None
            or type(ready_refs) is not tuple
            or not ready_refs
        ):
            raise PlanControlError(
                "GITHUB_SNAPSHOT_INVALID",
                "GitHub snapshot identity is invalid",
            )
        try:
            policy_content = self.content_client.read(
                repository,
                self.control_branch,
                self.policy_path,
            )
        except Exception as error:
            raise PlanControlError(
                "GITHUB_SNAPSHOT_UNAVAILABLE",
                "GitHub Policy Witness cannot be read",
            ) from error
        if policy_content is None:
            raise PlanControlError(
                "POLICY_WITNESS_INVALID",
                "GitHub Policy Witness is missing",
            )
        try:
            policy = strict_json_loads(policy_content.content)
        except CanonicalJsonError as error:
            raise PlanControlError(
                "POLICY_WITNESS_INVALID",
                "GitHub Policy Witness is not strict JSON",
            ) from error
        if type(policy) is not dict:
            raise PlanControlError(
                "POLICY_WITNESS_INVALID",
                "GitHub Policy Witness is not an object",
            )
        target_oid = self.issue_client.read_branch_oid(
            repository,
            self.target_branch,
        )
        tickets = []
        seen_numbers: set[int] = set()
        for ready_ref in ready_refs:
            number = _issue_number(repository, ready_ref)
            if number in seen_numbers:
                raise PlanControlError(
                    "READY_REFS_INVALID",
                    "Ready references resolve to the same GitHub issue",
                )
            seen_numbers.add(number)
            issue = self.issue_client.read_issue(repository, number)
            if (
                issue.get("number") != number
                or type(issue.get("title")) is not str
                or not issue["title"]
                or type(issue.get("body")) is not str
                or not issue["body"]
                or type(issue.get("state")) is not str
            ):
                raise PlanControlError(
                    "GITHUB_SNAPSHOT_INVALID",
                    "GitHub issue contract readback is incomplete",
                )
            labels = _label_names(issue.get("labels"))
            blockers = []
            for blocker in self.issue_client.read_blockers(repository, number):
                blocker_number = blocker.get("number")
                blocker_state = blocker.get("state")
                if (
                    type(blocker_number) is not int
                    or blocker_number < 1
                    or type(blocker_state) is not str
                    or blocker_state.lower() not in {"open", "closed"}
                ):
                    raise PlanControlError(
                        "GITHUB_SNAPSHOT_INVALID",
                        "GitHub native blocker readback is incomplete",
                    )
                blockers.append(
                    {
                        "key": f"issue:{blocker_number}",
                        "state": blocker_state.lower(),
                    }
                )
            frozen_contract = {
                "number": number,
                "title": issue["title"],
                "body": issue["body"],
                "labels": labels,
                "state": issue["state"].lower(),
                "native_blockers": sorted(
                    blockers,
                    key=lambda item: item["key"],
                ),
            }
            tickets.append(
                {
                    "key": f"issue:{number}",
                    "labels": labels,
                    "source": {
                        "ref": ready_ref,
                        "digest": digest_value(frozen_contract),
                    },
                    "contract": {
                        "title": issue["title"],
                        "body": issue["body"],
                    },
                    "native_blockers": frozen_contract["native_blockers"],
                }
            )
        branch_ref = f"refs/heads/{self.target_branch}"
        return {
            "repository": repository,
            "target_branch": self.target_branch,
            "campaign_source": {
                "ref": branch_ref,
                "digest": digest_value(
                    {
                        "repository": repository,
                        "ref": branch_ref,
                        "commit_oid": target_oid,
                    }
                ),
            },
            "policy": policy,
            "tickets": tickets,
        }
