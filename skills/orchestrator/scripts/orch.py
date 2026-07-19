#!/usr/bin/env python3
"""Orchestrator V6 command seam.

The CLI owns short-lived GitHub/Git mutations. Agent creation and prompts are
returned as actions for the Skill to execute through Paseo MCP.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from typing import Any

import orch_core as core


DEFAULT_CONFIG = Path.home() / ".orch" / "config.json"
ORCH_PR_FIELDS = r"""
number state title body headRefName headRefOid baseRefName isDraft updatedAt mergedAt
mergeStateStatus reviewDecision url
reviews(first:100){pageInfo{hasNextPage} nodes{state body submittedAt author{login} commit{oid}}}
files(first:100){pageInfo{hasNextPage} nodes{path}}
commits(last:1){nodes{commit{statusCheckRollup{contexts(first:100){pageInfo{hasNextPage} nodes{
  ... on CheckRun{status conclusion}
  ... on StatusContext{state}
}}}}}}
"""
SNAPSHOT_QUERY = r"""
query($owner:String!,$name:String!,$branch:String!){
  repository(owner:$owner,name:$name){
    ref(qualifiedName:$branch){target{... on Commit{oid}}}
    readyIssues:issues(first:100,states:OPEN,labels:["orch:ready"],orderBy:{field:UPDATED_AT,direction:DESC}){
      totalCount pageInfo{hasNextPage}
      nodes{...OrchIssue}
    }
    activeIssues:issues(first:100,states:OPEN,labels:["orch:active"],orderBy:{field:UPDATED_AT,direction:DESC}){
      totalCount pageInfo{hasNextPage}
      nodes{...OrchIssue}
    }
    blockedIssues:issues(first:100,states:OPEN,labels:["orch:blocked"],orderBy:{field:UPDATED_AT,direction:DESC}){
      totalCount pageInfo{hasNextPage}
      nodes{...OrchIssue}
    }
    pullRequests(first:100,states:OPEN,orderBy:{field:UPDATED_AT,direction:DESC}){
      totalCount pageInfo{hasNextPage}
      nodes{__ORCH_PR_FIELDS__}
    }
  }
}
fragment OrchIssue on Issue{
  number title body updatedAt
  labels(first:30){nodes{name}}
  milestone{title dueOn}
  assignees(first:20){nodes{login}}
  comments(first:100){pageInfo{hasNextPage} nodes{databaseId body createdAt updatedAt author{login}}}
}
""".replace("__ORCH_PR_FIELDS__", ORCH_PR_FIELDS)


class CommandError(RuntimeError):
    pass


def _read_json(source: str | Path) -> Any:
    if str(source) == "-":
        text = sys.stdin.read()
    else:
        text = Path(source).read_text(encoding="utf-8")
    try:
        return json.loads(text)
    except json.JSONDecodeError as error:
        raise CommandError(f"invalid JSON: {error}") from error


def _run(command: list[str], *, cwd: Path | None = None) -> str:
    executable = Path(command[0])
    if os.name == "nt" and executable.suffix.lower() in {".cmd", ".bat"}:
        command = [
            os.environ.get("ComSpec", r"C:\Windows\System32\cmd.exe"),
            "/d",
            "/s",
            "/c",
            subprocess.list2cmdline(command),
        ]
    result = subprocess.run(
        command,
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if result.returncode:
        detail = result.stderr.strip() or result.stdout.strip()
        raise CommandError(f"{' '.join(command)}: {detail}")
    return result.stdout


def _tool(name: str, env_name: str) -> str:
    found = os.environ.get(env_name) or shutil.which(name)
    if not found:
        raise CommandError(f"{name} not found; install it or set {env_name}")
    return found


def _git_common_dir() -> Path:
    raw = _run([_tool("git", "ORCH_GIT_PATH"), "rev-parse", "--git-common-dir"])
    path = Path(raw.strip())
    if not path.is_absolute():
        path = Path.cwd() / path
    return path.resolve()


class GitHub:
    """Small public-CLI adapter; policy remains in orch_core."""

    def __init__(self) -> None:
        self.executable = _tool("gh", "ORCH_GH_PATH")

    def run(self, args: list[str]) -> str:
        return _run([self.executable, *args])

    def snapshot(self, repo: str, integration_branch: str = "dev") -> dict[str, Any]:
        owner, name = repo.split("/", 1)
        payload = json.loads(
            self.run(
                [
                    "api",
                    "graphql",
                    "-f",
                    f"query={SNAPSHOT_QUERY}",
                    "-F",
                    f"owner={owner}",
                    "-F",
                    f"name={name}",
                    "-F",
                    f"branch=refs/heads/{integration_branch}",
                ]
            )
        )
        repository = (payload.get("data") or {}).get("repository")
        if not isinstance(repository, dict):
            raise CommandError("GitHub repository snapshot missing")
        issue_connections = [
            repository.get(name) or {}
            for name in ("readyIssues", "activeIssues", "blockedIssues")
            if name in repository
        ]
        if not issue_connections:
            issue_connections = [repository.get("issues") or {}]
        pr_connection = repository.get("pullRequests") or {}
        if any(
            (connection.get("pageInfo") or {}).get("hasNextPage")
            for connection in issue_connections
        ) or (pr_connection.get("pageInfo") or {}).get("hasNextPage"):
            raise core.PolicyError(
                "SNAPSHOT_PAGINATION_REQUIRED",
                "more than 100 open Issues or PRs require a narrower frontier",
            )
        issue_nodes: dict[int, dict[str, Any]] = {}
        for connection in issue_connections:
            for node in connection.get("nodes") or []:
                issue_nodes[int(node["number"])] = node
        issues = [self._issue(issue_nodes[number]) for number in sorted(issue_nodes)]
        prs = [self._pr(node) for node in pr_connection.get("nodes") or []]
        normalized = core.normalize_github_snapshot(repo, issues, prs)
        open_pr_numbers = {int(pr["number"]) for pr in prs}
        durable_pr_numbers = {
            int((issue.get("dispatch") or {}).get("pr_number"))
            for issue in normalized["issues"]
            if (issue.get("dispatch") or {}).get("pr_number")
        }
        missing_pr_numbers = sorted(durable_pr_numbers - open_pr_numbers)
        if missing_pr_numbers:
            prs.extend(self.pull_requests_by_number(repo, missing_pr_numbers))
            normalized = core.normalize_github_snapshot(repo, issues, prs)
        dependencies = sorted(
            {
                int(dependency)
                for issue in normalized["issues"]
                for dependency in issue.get("dependencies") or []
            }
        )
        if dependencies:
            states = self.dependency_states(repo, dependencies)
            normalized["closed_issues"] = sorted(
                number for number, state in states.items() if state == "CLOSED"
            )
        normalized["base_sha"] = (
            (repository.get("ref") or {}).get("target") or {}
        ).get("oid")
        return normalized

    def pull_requests_by_number(
        self, repo: str, numbers: list[int]
    ) -> list[dict[str, Any]]:
        unique = sorted({int(number) for number in numbers})
        if not unique:
            return []
        owner, name = repo.split("/", 1)
        aliases = " ".join(
            f"p{number}:pullRequest(number:{number}){{{ORCH_PR_FIELDS}}}"
            for number in unique
        )
        query = (
            "query($owner:String!,$name:String!){"
            f"repository(owner:$owner,name:$name){{{aliases}}}"
            "}"
        )
        payload = json.loads(
            self.run(
                [
                    "api",
                    "graphql",
                    "-f",
                    f"query={query}",
                    "-F",
                    f"owner={owner}",
                    "-F",
                    f"name={name}",
                ]
            )
        )
        repository = (payload.get("data") or {}).get("repository") or {}
        return [
            self._pr(repository[f"p{number}"])
            for number in unique
            if repository.get(f"p{number}")
        ]

    def dependency_states(self, repo: str, numbers: list[int]) -> dict[int, str | None]:
        unique = sorted({int(number) for number in numbers})
        if not unique:
            return {}
        owner, name = repo.split("/", 1)
        fields = " ".join(
            f"d{number}:issue(number:{number}){{number state}}" for number in unique
        )
        query = f"query($owner:String!,$name:String!){{repository(owner:$owner,name:$name){{{fields}}}}}"
        payload = json.loads(
            self.run(
                [
                    "api",
                    "graphql",
                    "-f",
                    f"query={query}",
                    "-F",
                    f"owner={owner}",
                    "-F",
                    f"name={name}",
                ]
            )
        )
        repository = (payload.get("data") or {}).get("repository") or {}
        return {
            number: (repository.get(f"d{number}") or {}).get("state")
            for number in unique
        }

    @staticmethod
    def _issue(node: dict[str, Any]) -> dict[str, Any]:
        if ((node.get("comments") or {}).get("pageInfo") or {}).get("hasNextPage"):
            raise core.PolicyError(
                "SNAPSHOT_PAGINATION_REQUIRED",
                f"Issue #{node.get('number')} has more than 100 comments",
            )
        return {
            **node,
            "labels": (node.get("labels") or {}).get("nodes") or [],
            "assignees": (node.get("assignees") or {}).get("nodes") or [],
            "comments": [
                {**comment, "id": comment.get("databaseId")}
                for comment in (node.get("comments") or {}).get("nodes") or []
            ],
        }

    @staticmethod
    def _pr(node: dict[str, Any]) -> dict[str, Any]:
        commits = (node.get("commits") or {}).get("nodes") or []
        contexts = (
            ((commits[-1] if commits else {}).get("commit") or {}).get(
                "statusCheckRollup"
            )
            or {}
        ).get("contexts", {})
        if any(
            ((node.get(connection) or {}).get("pageInfo") or {}).get("hasNextPage")
            for connection in ("reviews", "files")
        ) or (contexts.get("pageInfo") or {}).get("hasNextPage"):
            raise core.PolicyError(
                "SNAPSHOT_PAGINATION_REQUIRED",
                f"PR #{node.get('number')} evidence exceeds one batch",
            )
        rollup = contexts.get("nodes", [])
        checks = []
        for context in rollup:
            if context.get("state"):
                state = str(context["state"]).upper()
                checks.append(
                    {
                        "status": "COMPLETED"
                        if state in {"SUCCESS", "FAILURE", "ERROR"}
                        else "PENDING",
                        "conclusion": state,
                    }
                )
            else:
                checks.append(context)
        return {
            **node,
            "statusCheckRollup": checks,
            "reviews": (node.get("reviews") or {}).get("nodes") or [],
            "changedPaths": [
                item.get("path")
                for item in (node.get("files") or {}).get("nodes") or []
            ],
            "filesTruncated": bool(
                ((node.get("files") or {}).get("pageInfo") or {}).get("hasNextPage")
            ),
        }

    def claim(self, repo: str, issue: dict[str, Any], action: dict[str, Any]) -> None:
        record = dict(issue["managed_record"])
        dispatch = dict(record.get("dispatch") or {})
        if dispatch.get("id") not in {None, action["dispatch_id"]}:
            raise core.PolicyError(
                "ISSUE_ALREADY_CLAIMED", "Issue has another dispatch"
            )
        dispatch.update(
            {
                "id": action["dispatch_id"],
                "attempt": action["attempt"],
                "generation": action["wave_generation"],
                "creator_agent_id": os.environ.get("PASEO_AGENT_ID"),
                "worker_agent_id": None,
                "workspace_id": None,
                "branch": action["branch"],
                "base_sha": action.get("base_sha"),
                "status": "claiming",
                "claimed_at": core.utc_now(),
            }
        )
        record["dispatch"] = dispatch
        rendered = core.render_issue_record(record)
        comment_id = issue.get("managed_comment_id")
        owner, name = repo.split("/", 1)
        if comment_id:
            response = json.loads(
                self.run(
                    [
                        "api",
                        "--method",
                        "PATCH",
                        f"repos/{owner}/{name}/issues/comments/{comment_id}",
                        "-f",
                        f"body={rendered}",
                    ]
                )
            )
        else:
            response = json.loads(
                self.run(
                    [
                        "api",
                        "--method",
                        "POST",
                        f"repos/{owner}/{name}/issues/{action['issue']}/comments",
                        "-f",
                        f"body={rendered}",
                    ]
                )
            )
        if response.get("body") != rendered:
            raise core.PolicyError(
                "ISSUE_RECORD_READBACK_FAILED", "claim record readback failed"
            )
        self.set_issue_state(repo, int(action["issue"]), "active")

    def update_record(self, repo: str, issue: dict[str, Any]) -> None:
        record = dict(issue.get("managed_record") or {})
        record["dispatch"] = issue.get("dispatch")
        comment_id = issue.get("managed_comment_id")
        if not comment_id:
            raise core.PolicyError(
                "ISSUE_RECORD_MISSING", "cannot update missing managed record"
            )
        owner, name = repo.split("/", 1)
        rendered = core.render_issue_record(record)
        response = json.loads(
            self.run(
                [
                    "api",
                    "--method",
                    "PATCH",
                    f"repos/{owner}/{name}/issues/comments/{comment_id}",
                    "-f",
                    f"body={rendered}",
                ]
            )
        )
        if response.get("body") != rendered:
            raise core.PolicyError(
                "ISSUE_RECORD_READBACK_FAILED", "managed record readback failed"
            )

    def block_issue(self, repo: str, issue: dict[str, Any]) -> None:
        self.update_record(repo, issue)
        self.set_issue_state(repo, int(issue["number"]), "blocked")

    def retire_issue(self, repo: str, issue: dict[str, Any]) -> None:
        dispatch = dict(issue.get("dispatch") or {})
        dispatch.update(
            {
                "status": "retired",
                "parked": True,
                "retired_at": core.utc_now(),
            }
        )
        issue["dispatch"] = dispatch
        self.update_record(repo, issue)
        self.set_issue_state(repo, int(issue["number"]), "blocked")
        issue["state"] = "blocked"

    def mark_integrating(
        self,
        repo: str,
        issue: dict[str, Any],
        pr: int,
        candidate_sha: str,
        pr_state: str,
    ) -> None:
        dispatch = dict(issue.get("dispatch") or {})
        if dispatch.get("pr_number") not in {None, int(pr)}:
            raise core.PolicyError(
                "INTEGRATION_IDENTITY_CONFLICT",
                "Dispatch PR conflicts with the accepted PR",
            )
        old_candidate = dispatch.get("candidate_sha")
        if old_candidate not in {None, candidate_sha} and not (
            dispatch.get("status") == "integrating" and str(pr_state).upper() == "OPEN"
        ):
            raise core.PolicyError(
                "INTEGRATION_IDENTITY_CONFLICT",
                "Dispatch candidate conflicts with the accepted PR",
            )
        dispatch["pr_number"] = int(pr)
        dispatch["candidate_sha"] = candidate_sha
        dispatch["status"] = "integrating"
        dispatch["integrating_at"] = core.utc_now()
        issue["dispatch"] = dispatch
        self.update_record(repo, issue)

    def mark_merged(
        self,
        repo: str,
        issue: dict[str, Any],
        pr: int,
        candidate_sha: str,
        merged_at: str,
    ) -> None:
        dispatch = dict(issue.get("dispatch") or {})
        if dispatch.get("pr_number") not in {None, int(pr)} or dispatch.get(
            "candidate_sha"
        ) not in {None, candidate_sha}:
            raise core.PolicyError(
                "INTEGRATION_IDENTITY_CONFLICT",
                "merged PR identity conflicts with the Dispatch",
            )
        dispatch.update(
            {
                "pr_number": int(pr),
                "candidate_sha": candidate_sha,
                "status": "merged",
                "merged_at": merged_at,
            }
        )
        issue["dispatch"] = dispatch
        self.update_record(repo, issue)

    def set_issue_state(self, repo: str, issue: int, state: str) -> None:
        if state not in {"ready", "active", "blocked"}:
            raise core.PolicyError("ISSUE_STATE_INVALID", f"invalid state: {state}")
        desired = f"orch:{state}"
        others = sorted({"orch:ready", "orch:active", "orch:blocked"} - {desired})
        command = [
            "issue",
            "edit",
            str(issue),
            "--repo",
            repo,
            "--add-label",
            desired,
        ]
        for label in others:
            command.extend(["--remove-label", label])
        self.run(command)
        readback = json.loads(
            self.run(
                [
                    "issue",
                    "view",
                    str(issue),
                    "--repo",
                    repo,
                    "--json",
                    "labels",
                ]
            )
        )
        labels = {label.get("name") for label in readback.get("labels") or []}
        if desired not in labels or labels & set(others):
            raise core.PolicyError(
                "ISSUE_STATE_READBACK_FAILED", "Issue state was not read back"
            )

    def project_labels(self, repo: str) -> list[str]:
        created: list[str] = []
        colors = {
            "orch:ready": "1D76DB",
            "orch:active": "FBCA04",
            "orch:blocked": "D93F0B",
        }
        for label, color in colors.items():
            result = subprocess.run(
                [
                    self.executable,
                    "label",
                    "create",
                    label,
                    "--repo",
                    repo,
                    "--color",
                    color,
                    "--force",
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
            if result.returncode:
                raise CommandError(result.stderr.strip())
            created.append(label)
        return created

    def ensure_project_fields(
        self, number: int, owner: str
    ) -> tuple[str, dict[str, dict[str, Any]]]:
        project = json.loads(
            self.run(
                ["project", "view", str(number), "--owner", owner, "--format", "json"]
            )
        )
        project_id = project.get("id")
        if not project_id:
            raise CommandError("Project id missing")
        specification = {
            "Status": (
                "SINGLE_SELECT",
                "Backlog,Ready,Active,Blocked,Review,Ready to merge,Done",
            ),
            "Priority": ("SINGLE_SELECT", "P0,P1,P2,P3"),
            "Wave": ("TEXT", None),
            "Risk": ("SINGLE_SELECT", "low,standard,strict"),
        }
        listed = json.loads(
            self.run(
                [
                    "project",
                    "field-list",
                    str(number),
                    "--owner",
                    owner,
                    "--format",
                    "json",
                    "--limit",
                    "100",
                ]
            )
        )
        fields = {field.get("name"): field for field in listed.get("fields") or []}
        for name, (data_type, options) in specification.items():
            existing = fields.get(name)
            if existing:
                actual_type = self._project_field_kind(existing)
                if actual_type != data_type:
                    raise core.PolicyError(
                        "PROJECT_FIELD_DRIFT",
                        f"Project field {name} has type {actual_type}",
                    )
                continue
            command = [
                "project",
                "field-create",
                str(number),
                "--owner",
                owner,
                "--name",
                name,
                "--data-type",
                data_type,
                "--format",
                "json",
            ]
            if options:
                command.extend(["--single-select-options", options])
            fields[name] = json.loads(self.run(command))
        refreshed = json.loads(
            self.run(
                [
                    "project",
                    "field-list",
                    str(number),
                    "--owner",
                    owner,
                    "--format",
                    "json",
                    "--limit",
                    "100",
                ]
            )
        )
        fields = {field.get("name"): field for field in refreshed.get("fields") or []}
        if not set(specification).issubset(fields):
            raise core.PolicyError(
                "PROJECT_FIELD_READBACK_FAILED", "Project fields missing after init"
            )
        return project_id, fields

    @staticmethod
    def _project_field_kind(field: dict[str, Any]) -> str:
        raw = (
            str(field.get("dataType") or field.get("type") or "")
            .upper()
            .replace(" ", "_")
        )
        if "SINGLESELECT" in raw or "SINGLE_SELECT" in raw:
            return "SINGLE_SELECT"
        if raw in {"TEXT", "PROJECTV2FIELD"}:
            return "TEXT"
        return raw

    def sync_project_issue(
        self,
        *,
        number: int,
        owner: str,
        project_id: str,
        fields: dict[str, dict[str, Any]],
        issue_url: str,
        projection: dict[str, str],
    ) -> None:
        item = json.loads(
            self.run(
                [
                    "project",
                    "item-add",
                    str(number),
                    "--owner",
                    owner,
                    "--url",
                    issue_url,
                    "--format",
                    "json",
                ]
            )
        )
        item_id = item.get("id")
        if not item_id:
            raise CommandError(f"Project item id missing for {issue_url}")
        for name, value in projection.items():
            field = fields[name]
            command = [
                "project",
                "item-edit",
                "--id",
                item_id,
                "--project-id",
                project_id,
                "--field-id",
                field["id"],
                "--format",
                "json",
            ]
            if self._project_field_kind(field) == "TEXT":
                command.extend(["--text", value])
            else:
                option = next(
                    (
                        option
                        for option in field.get("options") or []
                        if str(option.get("name")) == value
                    ),
                    None,
                )
                if not option:
                    raise core.PolicyError(
                        "PROJECT_OPTION_DRIFT",
                        f"Project field {name} lacks option {value}",
                    )
                command.extend(["--single-select-option-id", option["id"]])
            self.run(command)

    def update_branch(self, repo: str, pr: int) -> None:
        self.run(["pr", "update-branch", str(pr), "--repo", repo])

    def merge(
        self, repo: str, pr: int, method: str, expected_head_sha: str
    ) -> dict[str, Any]:
        if not re.fullmatch(r"[0-9a-fA-F]{40}", expected_head_sha or ""):
            raise core.PolicyError("CANDIDATE_SHA_INVALID", "merge head SHA invalid")
        self.run(
            [
                "pr",
                "merge",
                str(pr),
                "--repo",
                repo,
                f"--{method}",
                "--match-head-commit",
                expected_head_sha,
            ]
        )
        readback = json.loads(
            self.run(
                [
                    "pr",
                    "view",
                    str(pr),
                    "--repo",
                    repo,
                    "--json",
                    "state,mergedAt,mergeCommit,headRefName,headRefOid",
                ]
            )
        )
        if (
            readback.get("state") != "MERGED"
            or not readback.get("mergedAt")
            or readback.get("headRefOid") != expected_head_sha
        ):
            raise core.PolicyError(
                "MERGE_READBACK_FAILED", "PR merge was not read back"
            )
        return readback

    def close_issue(self, repo: str, issue: int, pr: int) -> None:
        current = json.loads(
            self.run(["issue", "view", str(issue), "--repo", repo, "--json", "state"])
        )
        if current.get("state") != "CLOSED":
            self.run(
                [
                    "issue",
                    "close",
                    str(issue),
                    "--repo",
                    repo,
                    "--comment",
                    f"Delivered by merged PR #{pr}.",
                ]
            )
        readback = json.loads(
            self.run(["issue", "view", str(issue), "--repo", repo, "--json", "state"])
        )
        if readback.get("state") != "CLOSED":
            raise core.PolicyError("ISSUE_CLOSE_READBACK_FAILED", "Issue did not close")

    def remote_branch_sha(self, repo: str, branch: str) -> str | None:
        owner, name = repo.split("/", 1)
        encoded = branch.replace("/", "%2F")
        result = subprocess.run(
            [
                self.executable,
                "api",
                f"repos/{owner}/{name}/git/ref/heads/{encoded}",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        if result.returncode:
            if "Not Found" in (result.stderr + result.stdout):
                return None
            raise CommandError(result.stderr.strip() or result.stdout.strip())
        payload = json.loads(result.stdout)
        sha = (payload.get("object") or {}).get("sha")
        if not re.fullmatch(r"[0-9a-fA-F]{40}", str(sha or "")):
            raise core.PolicyError(
                "REMOTE_BRANCH_READBACK_FAILED", "remote branch SHA missing"
            )
        return sha

    def delete_remote_branch(self, repo: str, branch: str, expected_sha: str) -> None:
        current = self.remote_branch_sha(repo, branch)
        if current is None:
            return
        if current != expected_sha:
            raise core.PolicyError(
                "REMOTE_BRANCH_ADVANCED", "remote branch has unmerged WIP"
            )
        result = subprocess.run(
            [
                _tool("git", "ORCH_GIT_PATH"),
                "push",
                f"--force-with-lease=refs/heads/{branch}:{expected_sha}",
                "origin",
                f":refs/heads/{branch}",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        if result.returncode:
            after_failure = self.remote_branch_sha(repo, branch)
            if after_failure is None:
                return
            if after_failure != expected_sha:
                raise core.PolicyError(
                    "REMOTE_BRANCH_ADVANCED", "remote branch advanced during cleanup"
                )
            raise CommandError(result.stderr.strip() or result.stdout.strip())
        if self.remote_branch_sha(repo, branch) is not None:
            raise core.PolicyError(
                "BRANCH_DELETE_READBACK_FAILED", "remote branch still exists"
            )


class Paseo:
    def __init__(self) -> None:
        self.executable = _tool("paseo", "ORCH_PASEO_PATH")

    def run(self, args: list[str]) -> str:
        return _run([self.executable, *args])

    def agents_for_dispatch(self, dispatch: str) -> list[dict[str, Any]]:
        return json.loads(
            self.run(
                [
                    "ls",
                    "--global",
                    "--all",
                    "--label",
                    f"orch.dispatch={dispatch}",
                    "--json",
                ]
            )
        )

    def agents_for_labels(self, labels: dict[str, str]) -> list[dict[str, Any]]:
        command = ["ls", "--global", "--all"]
        for key, value in labels.items():
            command.extend(["--label", f"{key}={value}"])
        command.append("--json")
        return json.loads(self.run(command))

    def all_agents(self) -> list[dict[str, Any]]:
        return json.loads(self.run(["ls", "--global", "--json"]))

    def inspect(self, agent_id: str) -> dict[str, Any]:
        return json.loads(self.run(["inspect", agent_id, "--json"]))

    def archive_agent(self, agent_id: str) -> dict[str, Any]:
        self.run(["archive", agent_id, "--json"])
        readback = self.inspect(agent_id)
        if not readback.get("Archived"):
            raise core.PolicyError(
                "AGENT_ARCHIVE_READBACK_FAILED", "Agent not archived"
            )
        return readback

    def archive_worktree(self, branch: str, path: Path | None = None) -> None:
        self.run(["worktree", "archive", branch, "--json"])
        if path is not None and path.exists():
            raise core.PolicyError(
                "WORKTREE_ARCHIVE_READBACK_FAILED", "worktree path still exists"
            )

    def runtime_agents(self, issues: list[dict[str, Any]]) -> list[dict[str, Any]]:
        runtime = []
        for issue in issues:
            dispatch = issue.get("dispatch") or {}
            dispatch_id = dispatch.get("id")
            if not dispatch_id:
                continue
            matches = self.agents_for_dispatch(dispatch_id)
            for match in matches:
                detail = self.inspect(match["id"])
                cwd = _expand_cwd(detail.get("Cwd"))
                branch = dispatch.get("branch")
                if cwd and cwd.is_dir():
                    branch = _git_at(cwd, "branch", "--show-current").strip() or branch
                worktree = detail.get("Worktree")
                workspace_id = (
                    worktree.get("Id")
                    if isinstance(worktree, dict)
                    else dispatch.get("workspace_id")
                )
                runtime.append(
                    {
                        "id": detail.get("Id"),
                        "labels": {"orch.dispatch": dispatch_id},
                        "workspace_id": workspace_id,
                        "branch": branch,
                        "state": "archived"
                        if detail.get("Archived")
                        else str(detail.get("Status") or "").lower(),
                        "parent_id": detail.get("ParentAgentId"),
                    }
                )
        return runtime


def _expand_cwd(raw: str | None) -> Path | None:
    if not raw:
        return None
    value = raw.replace("~", str(Path.home()), 1)
    return Path(value).resolve()


def _git_at(path: Path, *args: str) -> str:
    return _run([_tool("git", "ORCH_GIT_PATH"), "-C", str(path), *args])


def _ensure_local_base(base_sha: str, integration_branch: str) -> bool:
    """Fetch the integration ref only when its read-backed commit is unavailable."""

    command = [
        _tool("git", "ORCH_GIT_PATH"),
        "cat-file",
        "-e",
        f"{base_sha}^{{commit}}",
    ]

    def available() -> bool:
        return (
            subprocess.run(
                command,
                capture_output=True,
                text=True,
                encoding="utf-8",
            ).returncode
            == 0
        )

    if available():
        return False
    _run(
        [
            _tool("git", "ORCH_GIT_PATH"),
            "fetch",
            "--no-tags",
            "origin",
            integration_branch,
        ]
    )
    if not available():
        raise core.PolicyError(
            "INTEGRATION_BASE_FETCH_FAILED",
            "integration base is still unavailable after fetch",
        )
    return True


def _parse_git_worktrees(output: str) -> list[dict[str, Any]]:
    worktrees: list[dict[str, Any]] = []
    current: dict[str, Any] = {}
    for line in [*output.splitlines(), ""]:
        if not line:
            if current.get("path") and current.get("branch"):
                worktrees.append(current)
            current = {}
            continue
        key, _, value = line.partition(" ")
        if key == "worktree":
            current["path"] = value
        elif key == "branch" and value.startswith("refs/heads/"):
            current["branch"] = value.removeprefix("refs/heads/")
    return worktrees


def _runtime_worktrees() -> list[dict[str, Any]]:
    return _parse_git_worktrees(
        _run([_tool("git", "ORCH_GIT_PATH"), "worktree", "list", "--porcelain"])
    )


def _verified_dispatch_runtime(
    dispatch: dict[str, Any], paseo: Paseo, integration_branch: str
) -> tuple[dict[str, Any] | None, Path | None, str | None, str | None]:
    """Read back the exact Agent, Workspace, and branch bound to a Dispatch."""

    dispatch_id = dispatch.get("id")
    expected_agent = dispatch.get("worker_agent_id")
    expected_workspace = dispatch.get("workspace_id")
    expected_branch = dispatch.get("branch")
    try:
        issue_number = core.dispatch_issue(dispatch_id)
    except core.PolicyError:
        return None, None, None, "dispatch-identity-mismatch"
    if (
        not expected_agent
        or not expected_workspace
        or expected_branch != f"work/issue-{issue_number}"
        or expected_branch == integration_branch
    ):
        return None, None, None, "dispatch-identity-mismatch"
    matches = paseo.agents_for_dispatch(dispatch_id)
    if len(matches) != 1:
        return (
            None,
            None,
            None,
            "agent-identity-unknown" if not matches else "duplicate-dispatch-agent",
        )
    detail = paseo.inspect(matches[0]["id"])
    cwd = _expand_cwd(detail.get("Cwd"))
    actual_worktree = detail.get("Worktree")
    actual_workspace = (
        actual_worktree.get("Id") if isinstance(actual_worktree, dict) else None
    )
    if detail.get("Id") != expected_agent or not cwd or not cwd.is_dir():
        return None, None, None, "dispatch-identity-mismatch"
    actual_branch = _git_at(cwd, "branch", "--show-current").strip()
    if actual_branch != expected_branch:
        return None, None, None, "dispatch-identity-mismatch"
    if actual_workspace is not None and actual_workspace != expected_workspace:
        return None, None, None, "dispatch-identity-mismatch"
    if actual_workspace is None:
        registered = [
            worktree
            for worktree in _runtime_worktrees()
            if worktree.get("branch") == expected_branch
            and _expand_cwd(worktree.get("path")) == cwd
        ]
        if len(registered) != 1:
            return None, None, None, "dispatch-identity-mismatch"
    return detail, cwd, actual_branch, None


def _cleanup_after_merge(
    repo: str,
    issue: dict[str, Any],
    actor_id: str,
    github: GitHub,
    integration_branch: str,
) -> dict[str, Any]:
    dispatch = issue.get("dispatch") or {}
    dispatch_id = dispatch.get("id")
    if not dispatch_id:
        return {"actions": [], "manual_cleanup": [], "blockers": ["dispatch-missing"]}
    paseo = Paseo()
    detail, cwd, branch, identity_blocker = _verified_dispatch_runtime(
        dispatch, paseo, integration_branch
    )
    if identity_blocker:
        return {
            "actions": [],
            "manual_cleanup": [],
            "blockers": [identity_blocker],
        }
    assert detail is not None and cwd is not None and branch is not None
    candidate_sha = (issue.get("pr") or {}).get("head_sha")
    if not re.fullmatch(r"[0-9a-fA-F]{40}", str(candidate_sha or "")):
        return {
            "actions": [],
            "manual_cleanup": [],
            "blockers": ["cleanup-candidate-unknown"],
        }
    if _git_at(cwd, "rev-parse", "HEAD").strip() != candidate_sha:
        return {
            "actions": [],
            "manual_cleanup": [],
            "blockers": ["local-head-not-merged-candidate"],
        }
    remote_sha = github.remote_branch_sha(repo, branch)
    if remote_sha not in {None, candidate_sha}:
        return {
            "actions": [],
            "manual_cleanup": [],
            "blockers": ["remote-branch-has-unmerged-wip"],
        }
    worker = {
        "agent_id": detail.get("Id"),
        "relationship": "root" if not detail.get("ParentAgentId") else "subagent",
        "parent_id": detail.get("ParentAgentId"),
        "state": "archived"
        if detail.get("Archived")
        else str(detail.get("Status") or "").lower(),
        "archived": bool(detail.get("Archived")),
    }
    all_agents = paseo.all_agents()
    bound = [
        agent["id"]
        for agent in all_agents
        if agent.get("status") not in {"archived", "closed"}
        and _expand_cwd(agent.get("cwd")) == cwd
    ]
    worktree = {
        "workspace_id": dispatch["workspace_id"],
        "branch": branch,
        "path": cwd,
        "dirty": bool(_git_at(cwd, "status", "--porcelain").strip()),
        "bound_agent_ids": bound,
        "stable": False,
        "shared": len(bound) > 1,
    }
    cleanup = core.plan_cleanup(
        {
            "dispatch": dispatch_id,
            "merged": True,
            "actor_agent_id": actor_id,
            "worker": worker,
            "worktree": worktree,
            "integration_branch": integration_branch,
            "identity_verified": True,
        }
    )
    if cleanup["blockers"] or cleanup["manual_cleanup"]:
        return cleanup

    reviewer_manual: list[dict[str, Any]] = []
    reviewer_blockers: list[str] = []
    for reviewer_match in paseo.agents_for_labels(
        {
            "orch.repository": repo,
            "orch.issue": str(issue["number"]),
            "orch.role": "reviewer",
        }
    ):
        reviewer_detail = paseo.inspect(reviewer_match["id"])
        reviewer = {
            "agent_id": reviewer_detail.get("Id"),
            "relationship": "root"
            if not reviewer_detail.get("ParentAgentId")
            else "subagent",
            "parent_id": reviewer_detail.get("ParentAgentId"),
            "state": "archived"
            if reviewer_detail.get("Archived")
            else str(reviewer_detail.get("Status") or "").lower(),
            "archived": bool(reviewer_detail.get("Archived")),
        }
        reviewer_plan = core.plan_cleanup(
            {
                "merged": True,
                "actor_agent_id": actor_id,
                "worker": reviewer,
                "worktree": {},
                "integration_branch": integration_branch,
                "identity_verified": True,
            }
        )
        reviewer_manual.extend(reviewer_plan["manual_cleanup"])
        reviewer_blockers.extend(reviewer_plan["blockers"])
        for action in reviewer_plan["actions"]:
            if action["type"] == "archive_agent":
                paseo.archive_agent(action["agent_id"])
    completed = []
    if cleanup["actions"] and cleanup["actions"][0]["type"] == "archive_agent":
        paseo.archive_agent(cleanup["actions"][0]["agent_id"])
        completed.append(cleanup["actions"][0])
        worker["archived"] = True
        worker["state"] = "archived"
        worktree["bound_agent_ids"] = [
            agent["id"]
            for agent in paseo.all_agents()
            if agent.get("status") not in {"archived", "closed"}
            and _expand_cwd(agent.get("cwd")) == cwd
        ]
        cleanup = core.plan_cleanup(
            {
                "dispatch": dispatch_id,
                "merged": True,
                "actor_agent_id": actor_id,
                "worker": worker,
                "worktree": worktree,
                "integration_branch": integration_branch,
                "identity_verified": True,
            }
        )
    for action in cleanup["actions"]:
        if action["type"] == "archive_worktree":
            paseo.archive_worktree(worktree["branch"], worktree.get("path"))
            completed.append(action)
        elif action["type"] == "delete_branch":
            github.delete_remote_branch(repo, action["branch"], candidate_sha)
            completed.append(action)
    return {
        **cleanup,
        "actions": completed,
        "manual_cleanup": [*reviewer_manual, *cleanup["manual_cleanup"]],
        "blockers": [*reviewer_blockers, *cleanup["blockers"]],
    }


def _retire_stopped_dispatch(
    issue: dict[str, Any],
    actor_id: str,
    integration_branch: str,
    *,
    execute: bool = True,
) -> dict[str, Any]:
    dispatch = issue.get("dispatch") or {}
    paseo = Paseo()
    detail, cwd, branch, identity_blocker = _verified_dispatch_runtime(
        dispatch, paseo, integration_branch
    )
    if identity_blocker:
        return {
            "actions": [],
            "manual_cleanup": [],
            "blockers": [identity_blocker],
            "retirement_verified": False,
        }
    assert detail is not None and cwd is not None and branch is not None
    agent_id = detail.get("Id")
    parent_id = detail.get("ParentAgentId")
    status = str(detail.get("Status") or "").lower()
    if agent_id == actor_id or not parent_id:
        return {
            "actions": [],
            "manual_cleanup": [],
            "blockers": ["self-or-root-protected"],
            "retirement_verified": False,
        }
    if status not in {"idle", "closed", "error", "stopped"} and not detail.get(
        "Archived"
    ):
        return {
            "actions": [],
            "manual_cleanup": [],
            "blockers": ["worker-not-idle"],
            "retirement_verified": False,
        }
    if parent_id != actor_id:
        return {
            "actions": [],
            "manual_cleanup": [
                {
                    "type": "archive_agent",
                    "agent_id": agent_id,
                    "reason": "foreign-parent",
                }
            ],
            "blockers": [],
            "retirement_verified": True,
        }
    if _git_at(cwd, "status", "--porcelain").strip():
        return {
            "actions": [],
            "manual_cleanup": [],
            "blockers": ["dirty-worktree-protected"],
            "retirement_verified": True,
        }
    try:
        ahead = int(_git_at(cwd, "rev-list", "--count", "@{upstream}..HEAD").strip())
    except CommandError:
        return {
            "actions": [],
            "manual_cleanup": [],
            "blockers": ["unpushed-wip"],
            "retirement_verified": True,
        }
    if ahead:
        return {
            "actions": [],
            "manual_cleanup": [],
            "blockers": ["unpushed-wip"],
            "retirement_verified": True,
        }
    all_agents = paseo.all_agents()
    bound = [
        agent["id"]
        for agent in all_agents
        if agent.get("status") not in {"archived", "closed"}
        and _expand_cwd(agent.get("cwd")) == cwd
    ]
    if set(bound) - {agent_id}:
        return {
            "actions": [],
            "manual_cleanup": [],
            "blockers": ["shared-worktree-protected"],
            "retirement_verified": False,
        }
    if not execute:
        return {
            "actions": [],
            "manual_cleanup": [],
            "blockers": [],
            "retirement_verified": True,
        }
    completed = []
    if not detail.get("Archived"):
        paseo.archive_agent(agent_id)
        completed.append({"type": "archive_agent", "agent_id": agent_id})
    paseo.archive_worktree(branch, cwd)
    completed.append({"type": "archive_worktree", "branch": branch})
    return {
        "actions": completed,
        "manual_cleanup": [],
        "blockers": [],
        "preserve_remote_branch": True,
        "retirement_verified": True,
    }


def _observations(path: str | None) -> list[dict[str, Any]]:
    if not path:
        return []
    value = _read_json(path)
    if not isinstance(value, list):
        raise CommandError("observations must be a JSON array")
    return value


def _dedupe_actions(actions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Collapse identical deterministic retries and reject conflicting IDs."""

    result: list[dict[str, Any]] = []
    by_id: dict[str, dict[str, Any]] = {}
    for action in actions:
        action_id = action.get("action_id")
        if not action_id:
            raise core.PolicyError("ACTION_ID_MISSING", "action id is required")
        previous = by_id.get(action_id)
        if previous is None:
            by_id[action_id] = action
            result.append(action)
        elif previous != action:
            raise core.PolicyError(
                "ACTION_ID_CONFLICT", f"conflicting action payload: {action_id}"
            )
    return result


def _materialize_worker_wave(
    actions: list[dict[str, Any]],
    *,
    planned_action_ids: set[str],
    issues_by_number: dict[int, dict[str, Any]],
    repository: str,
    base_sha: str,
    config: dict[str, Any],
    runtime: dict[str, Any],
    github: GitHub,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Materialize and claim independent Worker actions without wave-wide failure."""

    materialized: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    for raw_action in actions:
        action = {**raw_action, "base_sha": base_sha}
        issue = issues_by_number[action["issue"]]
        try:
            ready = core.materialize_worker_action(
                action,
                issue,
                repository=repository,
                base_sha=base_sha,
                config=config,
                coordinator_runtime=runtime,
            )
        except core.PolicyError as error:
            warnings.append(
                {
                    "code": error.code,
                    "issue": action["issue"],
                    "detail": str(error),
                }
            )
            continue
        if action["action_id"] in planned_action_ids:
            try:
                github.claim(repository, issue, action)
            except (CommandError, core.PolicyError, OSError) as error:
                warnings.append(
                    {
                        "code": getattr(error, "code", "COMMAND_FAILED"),
                        "issue": action["issue"],
                        "detail": str(error),
                    }
                )
                continue
        materialized.append(ready)
    return materialized, warnings


def _repository_config(config: dict[str, Any], repo: str) -> dict[str, Any]:
    global_config = config.get("global") or {}
    configured = dict((config.get("repositories") or {}).get(repo) or {})
    return {
        **configured,
        "repository": repo,
        "integration_branch": configured.get("integration_branch", "dev"),
        "merge_method": configured.get("merge_method", "squash"),
        "worker_slots": configured.get(
            "worker_slots", global_config.get("worker_slots", 3)
        ),
        "max_attempts": configured.get(
            "max_attempts", global_config.get("max_attempts", 2)
        ),
    }


def _load_config(path: Path, *, write_migration: bool = True) -> dict[str, Any]:
    return core.load_or_migrate_config(
        path,
        path.with_name("providers.json"),
        write_migration=write_migration,
    )


def _paseo_current() -> tuple[dict[str, Any], dict[str, Any]]:
    agent_id = os.environ.get("PASEO_AGENT_ID")
    if not agent_id:
        raise core.PolicyError(
            "COORDINATOR_IDENTITY_MISSING", "PASEO_AGENT_ID is required"
        )
    executable = _tool("paseo", "ORCH_PASEO_PATH")
    payload = json.loads(_run([executable, "inspect", agent_id, "--json"]))
    parent = payload.get("ParentAgentId")
    settings = {
        "model": payload.get("Model"),
        "thinkingOptionId": payload.get("Thinking"),
        "modeId": payload.get("Mode"),
    }
    runtime_settings = payload.get("RuntimeSettings") or payload.get("Settings") or {}
    if "features" in runtime_settings:
        settings["features"] = dict(runtime_settings.get("features") or {})
    elif "Features" in payload:
        settings["features"] = dict(payload.get("Features") or {})
    runtime = {
        "agent_id": agent_id,
        "provider": payload.get("Provider"),
        "settings": settings,
    }
    identity = {
        "relationship": "root" if not parent else "subagent",
        "archived": bool(payload.get("Archived")),
        "workspace_id": (payload.get("Worktree") or {}).get("Id")
        if isinstance(payload.get("Worktree"), dict)
        else None,
        "cwd": payload.get("Cwd"),
    }
    return runtime, identity


def _remote_repository() -> str:
    url = _run([_tool("git", "ORCH_GIT_PATH"), "remote", "get-url", "origin"]).strip()
    match = re.search(r"github\.com[/:]([^/]+/[^/]+?)(?:\.git)?$", url)
    if not match:
        raise core.PolicyError(
            "REPOSITORY_REMOTE_INVALID", "origin is not a GitHub repository"
        )
    return match.group(1)


def _workspace(
    repo: str, identity: dict[str, Any], snapshot: dict[str, Any]
) -> dict[str, Any]:
    branch = _run([_tool("git", "ORCH_GIT_PATH"), "branch", "--show-current"]).strip()
    top_level = Path(
        _run([_tool("git", "ORCH_GIT_PATH"), "rev-parse", "--show-toplevel"]).strip()
    ).resolve()
    agent_cwd = _expand_cwd(identity.get("cwd"))
    dirty = bool(_run([_tool("git", "ORCH_GIT_PATH"), "status", "--porcelain"]).strip())
    return {
        "id": identity.get("workspace_id"),
        "repository": repo,
        "branch": branch,
        "relationship": identity["relationship"],
        "dirty": dirty,
        "pr_head": branch in set(snapshot.get("pr_heads") or []),
        "ephemeral": bool(identity.get("archived")),
        "worker": branch.startswith("work/issue-"),
        "agent_cwd_matches": agent_cwd == top_level,
    }


def _prepare_snapshot(
    github: GitHub,
    repo: str,
    repo_config: dict[str, Any],
    observations: list[dict[str, Any]],
    *,
    mutate: bool,
) -> tuple[dict[str, Any], dict[str, Any]]:
    snapshot = github.snapshot(repo, repo_config["integration_branch"])
    if mutate and not re.fullmatch(
        r"[0-9a-fA-F]{40}", str(snapshot.get("base_sha") or "")
    ):
        raise core.PolicyError(
            "INTEGRATION_BASE_MISSING", "integration branch base SHA was not read back"
        )
    snapshot["worker_slots"] = repo_config["worker_slots"]
    snapshot["wave_generation"] = max(
        (
            int((issue.get("dispatch") or {}).get("generation", 0))
            for issue in snapshot["issues"]
        ),
        default=0,
    )
    repairs = core.plan_issue_state_repairs(snapshot["issues"])
    snapshot["pending_state_repairs"] = repairs
    snapshot["runtime_agents"] = Paseo().runtime_agents(snapshot["issues"])
    snapshot["runtime_worktrees"] = _runtime_worktrees()
    runtime, identity = _paseo_current()
    workspace = _workspace(repo, identity, snapshot)
    snapshot["coordinator_workspace"] = workspace
    if mutate:
        core.qualify_workspace(workspace, repo_config, operation="reconcile-write")
    if _remote_repository().lower() != repo.lower():
        raise core.PolicyError(
            "WORKSPACE_REPOSITORY_MISMATCH", "origin repository mismatch"
        )
    if mutate:
        snapshot["base_fetched"] = _ensure_local_base(
            snapshot["base_sha"], repo_config["integration_branch"]
        )
    if mutate:
        by_number = {issue["number"]: issue for issue in snapshot["issues"]}
        for repair in repairs:
            github.set_issue_state(repo, repair["issue"], repair["state"])
            by_number[repair["issue"]]["state"] = repair["state"]
    merged_pending = [
        issue for issue in snapshot["issues"] if issue.get("state") == "merged"
    ]
    snapshot["merged_finalization_pending"] = [
        int(issue["number"]) for issue in merged_pending
    ]
    finalized: list[dict[str, Any]] = []
    if mutate:
        for issue in merged_pending:
            dispatch = issue.get("dispatch") or {}
            pr = issue.get("pr") or {}
            if dispatch.get("status") != "merged":
                github.mark_merged(
                    repo,
                    issue,
                    int(pr["number"]),
                    pr["head_sha"],
                    pr.get("merged_at") or core.utc_now(),
                )
            github.close_issue(repo, int(issue["number"]), int(pr["number"]))
            try:
                cleanup = _cleanup_after_merge(
                    repo,
                    issue,
                    runtime["agent_id"],
                    github,
                    repo_config["integration_branch"],
                )
            except (CommandError, core.PolicyError, OSError) as error:
                cleanup = {
                    "actions": [],
                    "manual_cleanup": [],
                    "blockers": [f"cleanup-failed:{error}"],
                }
            finalized.append({"issue": int(issue["number"]), "cleanup": cleanup})
    snapshot["post_merge_finalized"] = finalized
    observed = core.apply_observations(snapshot, observations)
    if mutate and observations:
        original = {issue["number"]: issue for issue in snapshot["issues"]}
        for issue in observed["issues"]:
            if issue.get("dispatch") != original[issue["number"]].get("dispatch"):
                github.update_record(repo, issue)
    return observed, runtime


def _plan_recoveries(
    snapshot: dict[str, Any],
    repo_config: dict[str, Any],
    runtime: dict[str, Any],
    github: GitHub,
    *,
    mutate: bool,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    actions: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    by_id = {agent["id"]: agent for agent in snapshot.get("runtime_agents") or []}
    for issue in snapshot.get("issues") or []:
        if issue.get("state") != "active":
            continue
        dispatch = issue.get("dispatch") or {}
        if dispatch.get("status") == "claiming":
            continue
        worker_id = dispatch.get("worker_agent_id")
        agent = by_id.get(worker_id)
        if not agent:
            warnings.append(
                {
                    "code": "WORKER_IDENTITY_UNKNOWN",
                    "dispatch": dispatch.get("id"),
                }
            )
            continue
        recovery = core.plan_worker_recovery(
            {
                "dispatch": dispatch,
                "agent": agent,
                "max_attempts": repo_config["max_attempts"],
            }
        )
        updated = recovery["dispatch_update"]
        if recovery["next_issue_state"] == "blocked":
            updated["status"] = "blocked"
            issue["dispatch"] = updated
            if mutate:
                github.block_issue(snapshot["repository"], issue)
            warnings.append(
                {"code": "WORKER_ATTEMPTS_EXHAUSTED", "dispatch": dispatch.get("id")}
            )
            continue
        if not recovery["actions"]:
            continue
        action = recovery["actions"][0]
        if action["type"] == "send_prompt":
            action["message"] = (
                f"No delivered PR is visible for Issue #{issue['number']}. "
                "Resume the existing contract, preserve WIP, and finish or report the concrete blocker."
            )
            issue["dispatch"] = updated
            if mutate:
                github.update_record(snapshot["repository"], issue)
            actions.append(action)
            continue
        updated["claimed_at"] = core.utc_now()
        updated["creator_agent_id"] = runtime["agent_id"]
        issue["dispatch"] = updated
        if mutate:
            github.update_record(snapshot["repository"], issue)
        actions.append(action)
    return actions, warnings


def _reconcile(args: argparse.Namespace) -> dict[str, Any]:
    observations = _observations(args.observations)
    if args.snapshot:
        snapshot = _read_json(args.snapshot)
        if snapshot.get("repository") not in {None, args.repo}:
            raise core.PolicyError(
                "REPOSITORY_MISMATCH", "snapshot repository mismatch"
            )
        snapshot["repository"] = args.repo
        snapshot = core.apply_observations(snapshot, observations)
        result = core.plan_reconcile(snapshot)
        partial = core.plan_partial_dispatch(snapshot)
        result["actions"] = _dedupe_actions([*result["actions"], *partial["actions"]])
        result["warnings"].extend(partial["warnings"])
        result["status"] = "actions" if result["actions"] else result["status"]
        if partial["record_updates"]:
            result["summary"]["record_updates"] = partial["record_updates"]
        return result

    config = _load_config(args.config, write_migration=False)
    repo_config = _repository_config(config, args.repo)
    github = GitHub()
    if args.read_only:
        snapshot, runtime = _prepare_snapshot(
            github, args.repo, repo_config, observations, mutate=False
        )
        planned = core.plan_reconcile(snapshot)
        partial = core.plan_partial_dispatch(snapshot)
        recovery_actions, recovery_warnings = _plan_recoveries(
            snapshot, repo_config, runtime, github, mutate=False
        )
        planned["actions"] = _dedupe_actions(
            [*planned["actions"], *partial["actions"], *recovery_actions]
        )
        planned["warnings"].extend(recovery_warnings)
        planned["warnings"].extend(partial["warnings"])
        if snapshot.get("pending_state_repairs"):
            planned["warnings"].append(
                {
                    "code": "ISSUE_STATE_REPAIR_PENDING",
                    "repairs": snapshot["pending_state_repairs"],
                }
            )
        if snapshot.get("merged_finalization_pending"):
            planned["warnings"].append(
                {
                    "code": "MERGED_FINALIZATION_PENDING",
                    "issues": snapshot["merged_finalization_pending"],
                }
            )
        if partial["record_updates"]:
            planned["summary"]["pending_record_updates"] = len(
                partial["record_updates"]
            )
        planned["status"] = "actions" if planned["actions"] else planned["status"]
        return planned

    with core.coordination_mutex(_git_common_dir() / "orchestrator.lock"):
        snapshot, runtime = _prepare_snapshot(
            github, args.repo, repo_config, observations, mutate=True
        )
        config = _load_config(args.config, write_migration=True)
        repo_config = _repository_config(config, args.repo)
        recovery_actions, recovery_warnings = _plan_recoveries(
            snapshot, repo_config, runtime, github, mutate=True
        )
        planned = core.plan_reconcile(snapshot)
        partial = core.plan_partial_dispatch(snapshot)
        for update in partial["record_updates"]:
            issue = next(
                item for item in snapshot["issues"] if item["number"] == update["issue"]
            )
            issue["dispatch"] = update["dispatch"]
            github.update_record(args.repo, issue)
        raw_actions = _dedupe_actions([*planned["actions"], *partial["actions"]])
        issues_by_number = {issue["number"]: issue for issue in snapshot["issues"]}
        materialized, materialization_warnings = _materialize_worker_wave(
            raw_actions,
            planned_action_ids={action["action_id"] for action in planned["actions"]},
            issues_by_number=issues_by_number,
            repository=args.repo,
            base_sha=snapshot["base_sha"],
            config=config,
            runtime=runtime,
            github=github,
        )
        recovery_creates = [
            action for action in recovery_actions if action["type"] == "create_worker"
        ]
        recovered, recovery_materialization_warnings = _materialize_worker_wave(
            recovery_creates,
            planned_action_ids=set(),
            issues_by_number=issues_by_number,
            repository=args.repo,
            base_sha=snapshot["base_sha"],
            config=config,
            runtime=runtime,
            github=github,
        )
        materialized.extend(recovered)
        materialized.extend(
            action for action in recovery_actions if action["type"] != "create_worker"
        )
        review = core.plan_review_actions(snapshot)
        for action in review["actions"]:
            issue = issues_by_number[action["issue"]]
            try:
                reviewer = core.materialize_reviewer_action(
                    action,
                    issue,
                    repository=args.repo,
                    config=config,
                    coordinator_runtime=runtime,
                )
            except core.PolicyError as error:
                materialization_warnings.append(
                    {
                        "code": error.code,
                        "issue": action["issue"],
                        "detail": str(error),
                    }
                )
                continue
            materialized.append(reviewer)
        planned["actions"] = materialized
        planned["warnings"].extend(recovery_warnings)
        planned["warnings"].extend(partial["warnings"])
        planned["warnings"].extend(materialization_warnings)
        planned["warnings"].extend(recovery_materialization_warnings)
        if snapshot.get("pending_state_repairs"):
            planned["summary"]["state_repairs"] = snapshot["pending_state_repairs"]
        if snapshot.get("post_merge_finalized"):
            planned["summary"]["post_merge_finalized"] = snapshot[
                "post_merge_finalized"
            ]
        planned["status"] = "actions" if materialized else planned["status"]
        return planned


def _integrate(args: argparse.Namespace) -> dict[str, Any]:
    if args.snapshot:
        return core.plan_integration(_read_json(args.snapshot))
    config = _load_config(args.config, write_migration=False)
    repo_config = _repository_config(config, args.repo)
    if repo_config["integration_branch"] == "main":
        raise core.PolicyError(
            "MAIN_RELEASE_REQUIRES_EXPLICIT_REQUEST",
            "Orchestrator does not automatically release to main",
        )
    github = GitHub()
    with core.coordination_mutex(_git_common_dir() / "orchestrator.lock"):
        snapshot, runtime = _prepare_snapshot(
            github, args.repo, repo_config, [], mutate=True
        )
        workspace = snapshot["coordinator_workspace"]
        core.qualify_workspace(workspace, repo_config, operation="integrate")
        config = _load_config(args.config, write_migration=True)
        repo_config = _repository_config(config, args.repo)
        matching = [
            issue
            for issue in snapshot["issues"]
            if (issue.get("pr") or {}).get("number") == args.pr
        ]
        if len(matching) != 1:
            raise core.PolicyError(
                "INTEGRATION_PR_NOT_MANAGED", "PR is not one managed candidate"
            )
        issue = matching[0]
        ordered = core.integration_order(snapshot["issues"], snapshot["closed_issues"])
        if not ordered or ordered[0]["number"] != issue["number"]:
            return {
                "schema_version": 1,
                "status": "waiting",
                "actions": [],
                "warnings": [
                    {
                        "code": "INTEGRATION_ORDER_WAIT",
                        "next_issue": ordered[0]["number"] if ordered else None,
                    }
                ],
                "summary": {"pr": args.pr, "issue": issue["number"]},
            }
        pr = issue["pr"]
        merge_state = str(pr.get("merge_state") or "").upper()
        checks = pr.get("checks")
        plan = core.plan_integration(
            {
                "pr": args.pr,
                "head_sha": pr.get("head_sha"),
                "base": pr.get("base"),
                "integration_branch": repo_config["integration_branch"],
                "workspace": workspace,
                "checks": "none-allowed" if checks == "none" else checks,
                "review": "accepted"
                if issue.get("state") == "ready-to-merge"
                else "waiting",
                "contract_valid": bool(
                    issue.get("contract_valid") and pr.get("delivery_valid")
                ),
                "behind": merge_state == "BEHIND",
                "required_approval": str(pr.get("review_decision") or "").upper()
                == "REVIEW_REQUIRED",
                "merge_queue": merge_state == "QUEUED",
                "deployment_gate": merge_state == "BLOCKED",
            }
        )
        if plan["status"] != "actions":
            if plan["actions"] and plan["actions"][0]["type"] == "update_branch":
                github.update_branch(args.repo, args.pr)
            return {
                "schema_version": 1,
                "status": "waiting",
                "actions": [],
                "warnings": [],
                "summary": {
                    "pr": args.pr,
                    "issue": issue["number"],
                    "updated_branch": bool(plan["actions"]),
                },
            }
        github.mark_integrating(
            args.repo,
            issue,
            args.pr,
            pr["head_sha"],
            pr.get("state"),
        )
        readback = github.merge(
            args.repo,
            args.pr,
            repo_config["merge_method"],
            pr["head_sha"],
        )
        github.mark_merged(
            args.repo,
            issue,
            args.pr,
            pr["head_sha"],
            readback["mergedAt"],
        )
        github.close_issue(args.repo, issue["number"], args.pr)
        try:
            cleanup = _cleanup_after_merge(
                args.repo,
                issue,
                runtime["agent_id"],
                github,
                repo_config["integration_branch"],
            )
        except (CommandError, core.PolicyError, OSError) as error:
            cleanup = {
                "actions": [],
                "manual_cleanup": [],
                "blockers": [f"cleanup-failed:{error}"],
            }
        return {
            "schema_version": 1,
            "status": "idle",
            "actions": [],
            "warnings": [{"code": "cleanup-deferred", "blockers": cleanup["blockers"]}]
            if cleanup["blockers"] or cleanup["manual_cleanup"]
            else [],
            "summary": {
                "pr": args.pr,
                "issue": issue["number"],
                "merged_at": readback["mergedAt"],
                "cleanup": cleanup,
            },
        }


def _retire(args: argparse.Namespace) -> dict[str, Any]:
    if args.snapshot:
        return core.plan_retirement(_read_json(args.snapshot))
    issue_number = core.dispatch_issue(args.dispatch)
    github = GitHub()
    config = _load_config(args.config, write_migration=False)
    repo_config = _repository_config(config, args.repo)
    with core.coordination_mutex(_git_common_dir() / "orchestrator.lock"):
        _prepare_snapshot(github, args.repo, repo_config, [], mutate=True)
        view = json.loads(
            github.run(
                [
                    "issue",
                    "view",
                    str(issue_number),
                    "--repo",
                    args.repo,
                    "--comments",
                    "--json",
                    "number,state,labels,comments,title",
                ]
            )
        )
        issue_snapshot = core.normalize_github_snapshot(args.repo, [view], [])
        if not issue_snapshot["issues"]:
            raise core.PolicyError("DISPATCH_NOT_MANAGED", "dispatch Issue not managed")
        issue = issue_snapshot["issues"][0]
        dispatch = issue.get("dispatch") or {}
        if dispatch.get("id") != args.dispatch:
            raise core.PolicyError(
                "DISPATCH_IDENTITY_MISMATCH", "dispatch record mismatch"
            )
        core.plan_retirement(
            {
                "status": dispatch.get("status"),
                "parked": dispatch.get("parked", False),
                "merged": False,
                "remote_branch": True,
            }
        )
        config = _load_config(args.config, write_migration=True)
        repo_config = _repository_config(config, args.repo)
        runtime, _ = _paseo_current()
        preflight = _retire_stopped_dispatch(
            issue,
            runtime["agent_id"],
            repo_config["integration_branch"],
            execute=False,
        )
        if not preflight.get("retirement_verified"):
            cleanup = preflight
        else:
            if dispatch.get("status") != "retired":
                github.retire_issue(args.repo, issue)
            cleanup = (
                preflight
                if preflight["blockers"] or preflight["manual_cleanup"]
                else _retire_stopped_dispatch(
                    issue,
                    runtime["agent_id"],
                    repo_config["integration_branch"],
                    execute=True,
                )
            )
        if cleanup.get("retirement_verified") and issue.get("state") != "blocked":
            # retire_issue already persisted the terminal before any destructive step.
            issue["state"] = "blocked"
        return {
            "schema_version": 1,
            "status": "blocked" if cleanup["blockers"] else "idle",
            "actions": [],
            "warnings": [],
            "summary": {"dispatch": args.dispatch, "cleanup": cleanup},
        }


def _project(args: argparse.Namespace) -> dict[str, Any]:
    config = _load_config(args.config, write_migration=False)
    repo_config = _repository_config(config, args.repo)
    number = repo_config.get("project_number")
    owner = repo_config.get("project_owner") or args.repo.split("/", 1)[0]
    try:
        github = GitHub()
        with core.coordination_mutex(_git_common_dir() / "orchestrator.lock"):
            _prepare_snapshot(github, args.repo, repo_config, [], mutate=True)
            config = _load_config(args.config, write_migration=True)
            repo_config = _repository_config(config, args.repo)
            labels = github.project_labels(args.repo)
            if not number:
                return {
                    "schema_version": 1,
                    "status": "idle",
                    "actions": [],
                    "warnings": [],
                    "summary": {"labels": labels, "project": "not-configured"},
                }
            project_id, fields = github.ensure_project_fields(int(number), owner)
            synced = 0
            if args.operation == "sync":
                snapshot = github.snapshot(args.repo, repo_config["integration_branch"])
                for issue in snapshot["issues"]:
                    github.sync_project_issue(
                        number=int(number),
                        owner=owner,
                        project_id=project_id,
                        fields=fields,
                        issue_url=f"https://github.com/{args.repo}/issues/{issue['number']}",
                        projection=core.project_projection(issue),
                    )
                    synced += 1
    except (CommandError, core.PolicyError, OSError) as error:
        return {
            "schema_version": 1,
            "status": "waiting",
            "actions": [],
            "warnings": [{"code": "project-sync-degraded", "detail": str(error)}],
            "summary": {"project_optional": True},
        }
    return {
        "schema_version": 1,
        "status": "idle",
        "actions": [],
        "warnings": [],
        "summary": {
            "labels": labels,
            "project_optional": True,
            "project_number": number,
            "synced_items": synced,
            "manual_views": [
                "Backlog Table: group/filter by Status, Priority, Wave, Risk",
                "Current Wave Board: filter Active/Review/Ready to merge and group by Wave",
            ],
        },
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    reconcile = commands.add_parser("reconcile")
    reconcile.add_argument("--repo", required=True)
    reconcile.add_argument("--read-only", action="store_true")
    reconcile.add_argument("--observations")
    reconcile.add_argument("--snapshot", help=argparse.SUPPRESS)
    reconcile.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    integrate = commands.add_parser("integrate")
    integrate.add_argument("--repo", required=True)
    integrate.add_argument("--pr", required=True, type=int)
    integrate.add_argument("--snapshot", help=argparse.SUPPRESS)
    integrate.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    retire = commands.add_parser("retire")
    retire.add_argument("--repo", required=True)
    retire.add_argument("--dispatch", required=True)
    retire.add_argument("--snapshot", help=argparse.SUPPRESS)
    retire.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    project = commands.add_parser("project")
    project.add_argument("operation", choices=("init", "sync"))
    project.add_argument("--repo", required=True)
    project.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        if args.command == "reconcile":
            result = _reconcile(args)
        elif args.command == "integrate":
            result = _integrate(args)
        elif args.command == "retire":
            result = _retire(args)
        else:
            result = _project(args)
        print(json.dumps(result, indent=2, sort_keys=True, ensure_ascii=False))
        return 0
    except (CommandError, core.PolicyError, OSError) as error:
        code = getattr(error, "code", "COMMAND_FAILED")
        print(
            json.dumps(
                {
                    "schema_version": 1,
                    "status": "blocked",
                    "actions": [],
                    "warnings": [{"code": code, "detail": str(error)}],
                    "summary": {},
                },
                ensure_ascii=False,
            )
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
