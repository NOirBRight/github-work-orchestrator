#!/usr/bin/env python3
"""Orchestrator V6.1 command seam.

The CLI owns short-lived GitHub/Git mutations. Agent creation and prompts are
returned as actions for the Skill to execute through Paseo MCP.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from typing import Any, Literal, TypedDict

import orch_core as core

_SCRIPT_DIR = str(Path(__file__).resolve().parent)
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)

from gwo_v8.activation import (
    ActivationError,
    GitHubCliContentClient,
)
from gwo_v8.transition import (
    GitHubLegacyWriterControl,
    LegacyWriterReadback,
)

frontier = core.frontier


DEFAULT_CONFIG = Path.home() / ".orch" / "config.json"
LEGACY_WRITER_CONTROL_BRANCH = "gwo-control"
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
  labels(first:100){pageInfo{hasNextPage} nodes{name}}
  milestone{title dueOn}
  assignees(first:20){nodes{login}}
  comments(first:100){pageInfo{hasNextPage} nodes{databaseId body createdAt updatedAt author{login}}}
}
""".replace("__ORCH_PR_FIELDS__", ORCH_PR_FIELDS)
FRONTIER_ISSUE_FIELDS = r"""
number title body updatedAt
labels(first:100){pageInfo{hasNextPage} nodes{name}}
milestone{title dueOn}
assignees(first:20){nodes{login}}
"""
FRONTIER_DETAIL_FIELDS = (
    FRONTIER_ISSUE_FIELDS
    + "comments(first:100){pageInfo{hasNextPage} nodes{databaseId body createdAt updatedAt author{login}}}"
)
FRONTIER_QUERY = r"""
query($owner:String!,$name:String!,$limit:Int!){
  repository(owner:$owner,name:$name){
    issues(first:$limit,states:OPEN,orderBy:{field:UPDATED_AT,direction:DESC}){
      totalCount pageInfo{hasNextPage}
      nodes{...FrontierIssue}
    }
  }
}
fragment FrontierIssue on Issue{__FRONTIER_ISSUE_FIELDS__}
"""
FRONTIER_QUERY = FRONTIER_QUERY.replace(
    "__FRONTIER_ISSUE_FIELDS__", FRONTIER_ISSUE_FIELDS
)


class CommandError(RuntimeError):
    pass


class DispatchRuntimeEvidence(TypedDict):
    state: Literal["present", "auto_archived", "invalid"]
    detail: dict[str, Any] | None
    cwd: Path | None
    branch: str | None
    blocker: str | None


def _read_json(source: str | Path) -> Any:
    if str(source) == "-":
        text = sys.stdin.read()
    else:
        text = Path(source).read_text(encoding="utf-8")
    try:
        return json.loads(text)
    except json.JSONDecodeError as error:
        raise CommandError(f"invalid JSON: {error}") from error


SUBPROCESS_TIMEOUT = 120


def _spawn(
    command: list[str], *, cwd: Path | None = None
) -> "subprocess.CompletedProcess[str]":
    executable = Path(command[0])
    if os.name == "nt" and executable.suffix.lower() in {".cmd", ".bat"}:
        command = [
            os.environ.get("ComSpec", r"C:\Windows\System32\cmd.exe"),
            "/d",
            "/s",
            "/c",
            subprocess.list2cmdline(command),
        ]
    try:
        return subprocess.run(
            command,
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=SUBPROCESS_TIMEOUT,
        )
    except subprocess.TimeoutExpired as error:
        raise CommandError(
            f"{' '.join(command)}: timed out after {SUBPROCESS_TIMEOUT}s"
        ) from error


def _run(command: list[str], *, cwd: Path | None = None) -> str:
    result = _spawn(command, cwd=cwd)
    if result.returncode:
        detail = result.stderr.strip() or result.stdout.strip()
        raise CommandError(f"{' '.join(command)}: {detail}")
    return result.stdout


def _tool(name: str, env_name: str) -> str:
    found = os.environ.get(env_name) or shutil.which(name)
    if not found:
        raise CommandError(f"{name} not found; install it or set {env_name}")
    return found


def _envelope(
    status: str,
    *,
    actions: list[dict[str, Any]] | None = None,
    warnings: list[dict[str, Any]] | None = None,
    summary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "status": status,
        "actions": actions or [],
        "warnings": warnings or [],
        "summary": summary or {},
    }


def _git_common_dir() -> Path:
    raw = _run([_tool("git", "ORCH_GIT_PATH"), "rev-parse", "--git-common-dir"])
    path = Path(raw.strip())
    if not path.is_absolute():
        path = Path.cwd() / path
    return path.resolve()


def _legacy_writer_stopped(repository: str) -> bool:
    """Read only the durable V6.1 stop bit used by mutation boundaries."""

    control = GitHubLegacyWriterControl(
        GitHubCliContentClient(executable=_tool("gh", "ORCH_GH_PATH")),
        branch=LEGACY_WRITER_CONTROL_BRANCH,
        execution_readback=lambda candidate: LegacyWriterReadback(
            repository=candidate,
            stopped=False,
            active_dispatches=(),
            integration_lease=False,
            active_workers=(),
        ),
    )
    return control.readback(repository).stopped


def _require_v61_writer_authority(repository: str) -> None:
    try:
        stopped = _legacy_writer_stopped(repository)
    except (ActivationError, ValueError) as error:
        raise core.PolicyError(
            "V61_WRITER_FENCE_UNAVAILABLE",
            f"durable V6.1 writer fence could not be trusted: {error}",
        ) from error
    if stopped:
        raise core.PolicyError(
            "V61_WRITER_STOPPED",
            "the durable V6.1 writer fence blocks repository mutation",
        )


@contextmanager
def _v61_mutation_guard(repository: str):
    with core.coordination_mutex(_git_common_dir() / "orchestrator.lock"):
        _require_v61_writer_authority(repository)
        yield


def _production_legacy_execution_readback(
    repository: str,
    repository_config: dict[str, Any],
) -> LegacyWriterReadback:
    integration_branch = repository_config.get("integration_branch")
    if not isinstance(integration_branch, str) or not integration_branch:
        raise core.PolicyError(
            "LEGACY_READBACK_CONFIG_INVALID",
            "legacy writer readback requires an integration branch",
        )
    with core.coordination_mutex(_git_common_dir() / "orchestrator.lock"):
        snapshot = GitHub().snapshot(repository, integration_branch)
        active_dispatches = tuple(
            sorted(
                str(dispatch["id"])
                for issue in snapshot.get("issues") or []
                for dispatch in [issue.get("dispatch") or {}]
                if dispatch.get("id")
                and dispatch.get("status") not in {"merged", "retired"}
            )
        )
        worker_ids: list[str] = []
        for agent in Paseo().agents_for_labels(
            {
                "orch.repository": repository,
                "orch.role": "worker",
            }
        ):
            archived = bool(agent.get("Archived") or agent.get("archivedAt"))
            status = str(agent.get("Status") or agent.get("status") or "").lower()
            if archived or status == "closed":
                continue
            agent_id = agent.get("Id") or agent.get("id")
            if not isinstance(agent_id, str) or not agent_id:
                raise core.PolicyError(
                    "LEGACY_RUNTIME_READBACK_INVALID",
                    "Paseo Worker readback lacks an Agent identity",
                )
            worker_ids.append(agent_id)
        return LegacyWriterReadback(
            repository=repository,
            stopped=False,
            active_dispatches=active_dispatches,
            integration_lease=False,
            active_workers=tuple(sorted(worker_ids)),
        )


def production_legacy_writer_control(
    repository_config: dict[str, Any],
) -> GitHubLegacyWriterControl:
    """Compose the production V6.1 fence and authoritative readback sources."""

    return GitHubLegacyWriterControl(
        GitHubCliContentClient(executable=_tool("gh", "ORCH_GH_PATH")),
        branch=LEGACY_WRITER_CONTROL_BRANCH,
        execution_readback=lambda repository: _production_legacy_execution_readback(
            repository,
            repository_config,
        ),
    )


class GitHub:
    """Small public-CLI adapter; policy remains in orch_core."""

    def __init__(self) -> None:
        self.executable = _tool("gh", "ORCH_GH_PATH")

    def run(self, args: list[str]) -> str:
        return _run([self.executable, *args])

    def snapshot(self, repo: str, integration_branch: str) -> dict[str, Any]:
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
            raise CommandError("GitHub issue snapshot missing")
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
                for dependency in [
                    *(issue.get("dispatch_after") or []),
                    *(issue.get("merge_after") or []),
                ]
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

    def frontier_candidates(
        self, repo: str, limit: int, labels: list[str] | None = None
    ) -> list[dict[str, Any]]:
        """Read the unfiltered open-Issue pool used by Frontier admission."""

        if not 1 <= int(limit) <= 100:
            raise core.PolicyError(
                "FRONTIER_LIMIT_INVALID", "candidate limit must be between 1 and 100"
            )
        owner, name = repo.split("/", 1)
        scoped_labels = list(dict.fromkeys(labels or []))
        query = FRONTIER_QUERY
        variables = []
        connection_names = ["issues"]
        if scoped_labels:
            declarations = "".join(
                f",$label{index}:String!" for index in range(len(scoped_labels))
            )
            connections = "\n".join(
                f"l{index}:issues(first:$limit,states:OPEN,labels:[$label{index}],"
                "orderBy:{field:UPDATED_AT,direction:DESC}){"
                "totalCount pageInfo{hasNextPage} nodes{...FrontierIssue}}"
                for index in range(len(scoped_labels))
            )
            query = (
                f"query($owner:String!,$name:String!,$limit:Int!{declarations}){{"
                f"repository(owner:$owner,name:$name){{{connections}}}}}"
                f"fragment FrontierIssue on Issue{{{FRONTIER_ISSUE_FIELDS}}}"
            )
            variables = [
                value
                for index, label in enumerate(scoped_labels)
                for value in ("-F", f"label{index}={label}")
            ]
            connection_names = [f"l{index}" for index in range(len(scoped_labels))]
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
                    "-F",
                    f"limit={int(limit)}",
                    *variables,
                ]
            )
        )
        repository = (payload.get("data") or {}).get("repository")
        if not isinstance(repository, dict):
            raise CommandError("GitHub Frontier snapshot missing")
        connections = [repository.get(name) or {} for name in connection_names]
        if any(
            (connection.get("pageInfo") or {}).get("hasNextPage")
            for connection in connections
        ):
            raise core.PolicyError(
                "FRONTIER_PAGINATION_REQUIRED",
                "candidate pool exceeds configured scan limit",
            )
        nodes = {
            int(node["number"]): node
            for connection in connections
            for node in connection.get("nodes") or []
        }
        if len(nodes) > limit:
            raise core.PolicyError(
                "FRONTIER_LIMIT_REQUIRED",
                "combined intake labels exceed configured candidate limit",
            )
        return [self._issue(nodes[number]) for number in sorted(nodes)]

    def _query_by_number(
        self,
        repo: str,
        prefix: str,
        node_kind: str,
        fields: str,
        numbers: list[int],
    ) -> dict[int, dict[str, Any]]:
        unique = sorted({int(number) for number in numbers})
        if not unique:
            return {}
        owner, name = repo.split("/", 1)
        aliases = " ".join(
            f"{prefix}{number}:{node_kind}(number:{number}){{{fields}}}"
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
        return {
            number: repository[f"{prefix}{number}"]
            for number in unique
            if repository.get(f"{prefix}{number}")
        }

    def issues_by_number(self, repo: str, numbers: list[int]) -> list[dict[str, Any]]:
        """Read full admission identity only for the proposed target Issues."""

        found = self._query_by_number(
            repo, "i", "issue", FRONTIER_DETAIL_FIELDS, numbers
        )
        return [self._issue(found[number]) for number in sorted(found)]

    def pull_requests_by_number(
        self, repo: str, numbers: list[int]
    ) -> list[dict[str, Any]]:
        found = self._query_by_number(repo, "p", "pullRequest", ORCH_PR_FIELDS, numbers)
        return [self._pr(found[number]) for number in sorted(found)]

    def dependency_states(self, repo: str, numbers: list[int]) -> dict[int, str | None]:
        unique = sorted({int(number) for number in numbers})
        found = self._query_by_number(repo, "d", "issue", "number state", unique)
        return {number: (found.get(number) or {}).get("state") for number in unique}

    @staticmethod
    def _issue(node: dict[str, Any]) -> dict[str, Any]:
        if ((node.get("labels") or {}).get("pageInfo") or {}).get("hasNextPage"):
            raise core.PolicyError(
                "SNAPSHOT_PAGINATION_REQUIRED",
                f"Issue #{node.get('number')} has more than 100 labels",
            )
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
                "contract_sha256": (record.get("contract") or {}).get("sha256"),
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

    def admit(
        self, repo: str, candidate: dict[str, Any], contract: dict[str, Any]
    ) -> dict[str, Any]:
        """Create or confirm one idempotent V2 managed record, then mark it Ready."""

        core.validate_contract(contract)
        if core.contract_version(contract) != 2:
            raise core.PolicyError(
                "ADMISSION_CONTRACT_VERSION_INVALID",
                "new admissions require Contract V2",
            )
        marker_comments = [
            comment
            for comment in candidate.get("comments") or []
            if any(
                marker in str(comment.get("body") or "")
                for marker in (core.ISSUE_MARKER_V1, core.ISSUE_MARKER_V2)
            )
        ]
        if len(marker_comments) > 1:
            raise core.PolicyError(
                "ISSUE_RECORD_DUPLICATE",
                f"Issue #{candidate.get('number')} has duplicate managed records",
            )
        record = {"contract": contract, "dispatch": None}
        rendered = core.render_issue_record(record)
        owner, name = repo.split("/", 1)
        comment_id = None
        if marker_comments:
            existing = core.parse_issue_record(
                str(marker_comments[0].get("body") or "")
            )
            existing_contract = existing.get("contract") or {}
            try:
                core.validate_contract(existing_contract)
            except core.PolicyError as error:
                raise core.PolicyError(
                    "ISSUE_ALREADY_MANAGED",
                    f"Issue #{candidate.get('number')} has an invalid existing contract",
                ) from error
            if existing_contract != contract or existing.get("dispatch") not in (
                None,
                {},
            ):
                raise core.PolicyError(
                    "ISSUE_ALREADY_MANAGED",
                    f"Issue #{candidate.get('number')} already has another contract",
                )
            comment_id = marker_comments[0].get("id")
            if str(marker_comments[0].get("body") or "") != rendered:
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
                        "ISSUE_RECORD_READBACK_FAILED",
                        "admission record readback failed",
                    )
        else:
            response = json.loads(
                self.run(
                    [
                        "api",
                        "--method",
                        "POST",
                        f"repos/{owner}/{name}/issues/{int(candidate['number'])}/comments",
                        "-f",
                        f"body={rendered}",
                    ]
                )
            )
            if response.get("body") != rendered:
                raise core.PolicyError(
                    "ISSUE_RECORD_READBACK_FAILED", "admission record readback failed"
                )
            comment_id = response.get("id")
        self.set_issue_state(repo, int(candidate["number"]), "ready")
        return {
            "issue": int(candidate["number"]),
            "comment_id": comment_id,
            "state": "ready",
        }

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
            result = _spawn(
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
                ]
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
        result = _spawn(
            [
                self.executable,
                "api",
                f"repos/{owner}/{name}/git/ref/heads/{encoded}",
            ]
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
        result = _spawn(
            [
                _tool("git", "ORCH_GIT_PATH"),
                "push",
                f"--force-with-lease=refs/heads/{branch}:{expected_sha}",
                "origin",
                f":refs/heads/{branch}",
            ]
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
        matches = json.loads(
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
        for match in matches:
            match["_matched_dispatch_label"] = dispatch
        return matches

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
        return _spawn(command).returncode == 0

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


def _runtime_evidence(
    state: Literal["present", "auto_archived", "invalid"],
    *,
    detail: dict[str, Any] | None = None,
    cwd: Path | None = None,
    branch: str | None = None,
    blocker: str | None = None,
) -> DispatchRuntimeEvidence:
    return {
        "state": state,
        "detail": detail,
        "cwd": cwd,
        "branch": branch,
        "blocker": blocker,
    }


def _agent_label(item: dict[str, Any], key: str) -> str | None:
    labels = item.get("labels") or item.get("Labels") or {}
    if isinstance(labels, dict):
        value = labels.get(key)
        return str(value) if value is not None else None
    for label in labels if isinstance(labels, list) else []:
        if isinstance(label, str) and label.startswith(f"{key}="):
            return label.split("=", 1)[1]
        if isinstance(label, dict) and label.get("key") == key:
            return str(label.get("value"))
    return None


def _verified_dispatch_runtime(
    dispatch: dict[str, Any],
    paseo: Paseo,
    integration_branch: str,
    *,
    candidate_sha: str | None = None,
) -> DispatchRuntimeEvidence:
    """Classify exact runtime evidence, including host-first auto-archive."""

    dispatch_id = dispatch.get("id")
    expected_agent = dispatch.get("worker_agent_id")
    expected_workspace = dispatch.get("workspace_id")
    expected_branch = dispatch.get("branch")
    try:
        issue_number = core.dispatch_issue(dispatch_id)
    except core.PolicyError:
        return _runtime_evidence("invalid", blocker="dispatch-identity-mismatch")
    if (
        not expected_agent
        or not expected_workspace
        or expected_branch != f"work/issue-{issue_number}"
        or expected_branch == integration_branch
    ):
        return _runtime_evidence("invalid", blocker="dispatch-identity-mismatch")
    matches = paseo.agents_for_dispatch(dispatch_id)
    if len(matches) != 1:
        return _runtime_evidence(
            "invalid",
            blocker="agent-identity-unknown"
            if not matches
            else "duplicate-dispatch-agent",
        )
    match = matches[0]
    detail = paseo.inspect(match["id"])
    cwd = _expand_cwd(detail.get("Cwd"))
    actual_worktree = detail.get("Worktree")
    actual_workspace = (
        actual_worktree.get("Id") if isinstance(actual_worktree, dict) else None
    )
    if detail.get("Id") != expected_agent:
        return _runtime_evidence("invalid", blocker="dispatch-identity-mismatch")
    if actual_workspace is not None and actual_workspace != expected_workspace:
        return _runtime_evidence("invalid", blocker="dispatch-identity-mismatch")

    if cwd and cwd.is_dir():
        actual_branch = _git_at(cwd, "branch", "--show-current").strip()
        if actual_branch != expected_branch:
            return _runtime_evidence("invalid", blocker="dispatch-identity-mismatch")
        if actual_workspace is None:
            registered = [
                worktree
                for worktree in _runtime_worktrees()
                if worktree.get("branch") == expected_branch
                and _expand_cwd(worktree.get("path")) == cwd
            ]
            if len(registered) != 1:
                return _runtime_evidence(
                    "invalid", blocker="dispatch-identity-mismatch"
                )
        return _runtime_evidence(
            "present", detail=detail, cwd=cwd, branch=actual_branch
        )

    dispatch_label = (
        _agent_label(match, "orch.dispatch")
        or _agent_label(detail, "orch.dispatch")
        or match.get("_matched_dispatch_label")
    )
    auto_archive_proven = bool(
        detail.get("Archived")
        and dispatch_label == dispatch_id
        and dispatch.get("status") == "merged"
        and re.fullmatch(r"[0-9a-fA-F]{40}", str(candidate_sha or ""))
        and dispatch.get("candidate_sha") == candidate_sha
    )
    if auto_archive_proven:
        return _runtime_evidence("auto_archived", detail=detail, branch=expected_branch)
    return _runtime_evidence("invalid", blocker="dispatch-identity-mismatch")


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
    candidate_sha = (issue.get("pr") or {}).get("head_sha")
    if not re.fullmatch(r"[0-9a-fA-F]{40}", str(candidate_sha or "")):
        return {
            "actions": [],
            "manual_cleanup": [],
            "blockers": ["cleanup-candidate-unknown"],
        }
    recorded_candidate = dispatch.get("candidate_sha")
    if recorded_candidate is not None and recorded_candidate != candidate_sha:
        return {
            "actions": [],
            "manual_cleanup": [],
            "blockers": ["cleanup-candidate-mismatch"],
        }
    paseo = Paseo()
    evidence = _verified_dispatch_runtime(
        dispatch,
        paseo,
        integration_branch,
        candidate_sha=candidate_sha,
    )
    if evidence["state"] == "invalid":
        return {
            "actions": [],
            "manual_cleanup": [],
            "blockers": [evidence["blocker"]],
        }
    branch = evidence["branch"]
    assert branch is not None
    remote_sha = github.remote_branch_sha(repo, branch)
    if remote_sha not in {None, candidate_sha}:
        return {
            "actions": [],
            "manual_cleanup": [],
            "blockers": ["remote-branch-has-unmerged-wip"],
        }
    if evidence["state"] == "auto_archived":
        completed: list[dict[str, Any]] = []
        if remote_sha == candidate_sha:
            github.delete_remote_branch(repo, branch, candidate_sha)
            completed.append({"type": "delete_branch", "branch": branch})
        return {
            "actions": completed,
            "manual_cleanup": [],
            "blockers": [],
            "runtime_evidence": "auto_archived",
        }

    detail = evidence["detail"]
    cwd = evidence["cwd"]
    assert detail is not None and cwd is not None
    if _git_at(cwd, "rev-parse", "HEAD").strip() != candidate_sha:
        return {
            "actions": [],
            "manual_cleanup": [],
            "blockers": ["local-head-not-merged-candidate"],
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
    evidence = _verified_dispatch_runtime(dispatch, paseo, integration_branch)
    if evidence["state"] == "invalid":
        return {
            "actions": [],
            "manual_cleanup": [],
            "blockers": [evidence["blocker"]],
            "retirement_verified": False,
        }
    if evidence["state"] == "auto_archived":
        return {
            "actions": [],
            "manual_cleanup": [],
            "blockers": [],
            "retirement_verified": True,
        }
    detail = evidence["detail"]
    cwd = evidence["cwd"]
    branch = evidence["branch"]
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


def _persist_record_updates(
    github: GitHub,
    repository: str,
    snapshot: dict[str, Any],
    updates: list[dict[str, Any]],
) -> None:
    by_number = {int(issue["number"]): issue for issue in snapshot.get("issues") or []}
    for update in updates:
        issue = by_number[int(update["issue"])]
        issue["dispatch"] = update["dispatch"]
        github.update_record(repository, issue)
        state = update.get("state")
        if state and issue.get("state") != state:
            github.set_issue_state(repository, int(issue["number"]), state)
            issue["state"] = state


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
    execution_slots = configured.get(
        "execution_slots",
        configured.get(
            "worker_slots",
            global_config.get("execution_slots", global_config.get("worker_slots", 3)),
        ),
    )
    integration_wip_limit = configured.get(
        "integration_wip_limit",
        global_config.get("integration_wip_limit", max(6, int(execution_slots) * 2)),
    )
    intake = {
        **dict(global_config.get("intake") or {}),
        **dict(configured.get("intake") or {}),
    }
    resolved = {
        **configured,
        "repository": repo,
        "merge_method": configured.get("merge_method", "squash"),
        "execution_slots": execution_slots,
        "integration_wip_limit": integration_wip_limit,
        # Compatibility alias consumed by installed V6.0.x snapshots.
        "worker_slots": execution_slots,
        "max_attempts": configured.get(
            "max_attempts", global_config.get("max_attempts", 2)
        ),
        "intake": intake,
    }
    if configured.get("integration_branch"):
        resolved["integration_branch"] = configured["integration_branch"]
    return resolved


def _load_config(path: Path, *, write_migration: bool = True) -> dict[str, Any]:
    return core.load_or_migrate_config(
        path,
        path.with_name("providers.json"),
        write_migration=write_migration,
    )


def _reload_repo_config(
    args: argparse.Namespace, repo_config: dict[str, Any]
) -> dict[str, Any]:
    """Re-read config inside the mutex, preserving the resolved integration branch."""

    config = _load_config(args.config, write_migration=True)
    refreshed = _repository_config(config, args.repo)
    refreshed.setdefault("integration_branch", repo_config["integration_branch"])
    return refreshed


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


def _same_path(left: Any, right: Any) -> bool:
    if not isinstance(left, str) or not isinstance(right, (str, Path)):
        return False
    return os.path.normcase(
        os.path.abspath(os.path.expanduser(left))
    ) == os.path.normcase(os.path.abspath(os.path.expanduser(str(right))))


def _coordinator_preflight(
    args: argparse.Namespace, config: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Read back and validate Coordinator authority before constructing GitHub."""

    source = getattr(args, "coordinator_context", None)
    if not source:
        raise core.PolicyError(
            "COORDINATOR_CONTEXT_REQUIRED",
            "--coordinator-context is required for state-changing commands",
        )
    context = _read_json(source)
    if not isinstance(context, dict):
        raise core.PolicyError(
            "COORDINATOR_CONTEXT_INVALID", "Coordinator context must be an object"
        )
    runtime, identity = _paseo_current()
    actor = context.get("actor") or {}
    current_workspace = context.get("current_workspace") or {}
    if not actor.get("workspace_id") or actor.get(
        "workspace_id"
    ) != current_workspace.get("id"):
        raise core.PolicyError(
            "COORDINATOR_WORKSPACE_MISMATCH",
            "Paseo MCP Actor and current Workspace disagree",
        )
    live_relationship = identity.get("relationship")
    context_relationship = current_workspace.get("relationship")
    relationship_matches = live_relationship == context_relationship or (
        live_relationship == "root" and context_relationship == "detached"
    )
    if not relationship_matches:
        raise core.PolicyError(
            "COORDINATOR_RELATIONSHIP_MISMATCH",
            "Paseo relationship and current Workspace disagree",
        )
    if identity.get("archived") is not False or current_workspace.get("ephemeral"):
        raise core.PolicyError(
            "COORDINATOR_ARCHIVED", "archived/ephemeral Actor cannot coordinate"
        )
    if identity.get("workspace_id") is not None and identity.get(
        "workspace_id"
    ) != actor.get("workspace_id"):
        raise core.PolicyError(
            "COORDINATOR_WORKSPACE_MISMATCH",
            "Paseo Actor and supplied Workspace disagree",
        )
    top_level = Path(
        _run([_tool("git", "ORCH_GIT_PATH"), "rev-parse", "--show-toplevel"]).strip()
    ).resolve()
    if not _same_path(identity.get("cwd"), top_level):
        raise core.PolicyError(
            "COORDINATOR_CWD_MISMATCH", "Paseo cwd and Git worktree disagree"
        )
    actual_branch = _run(
        [_tool("git", "ORCH_GIT_PATH"), "branch", "--show-current"]
    ).strip()
    if (
        current_workspace.get("branch") != actual_branch
        or current_workspace.get("repository", "").casefold() != args.repo.casefold()
        or current_workspace.get("agent_cwd_matches") is not True
    ):
        raise core.PolicyError(
            "COORDINATOR_GIT_MISMATCH",
            "supplied Workspace does not match live Git branch/repository",
        )
    if _remote_repository().casefold() != args.repo.casefold():
        raise core.PolicyError(
            "WORKSPACE_REPOSITORY_MISMATCH", "origin repository mismatch"
        )
    entry = core.plan_coordinator_entry(
        context,
        _repository_config(config, args.repo),
        expected_actor_id=runtime["agent_id"],
        expected_cwd=str(top_level),
    )
    branch = entry["repository_config"]["integration_branch"]
    try:
        _run(
            [
                _tool("git", "ORCH_GIT_PATH"),
                "ls-remote",
                "--exit-code",
                "--heads",
                "origin",
                f"refs/heads/{branch}",
            ]
        )
    except CommandError as error:
        raise core.PolicyError(
            "INTEGRATION_BRANCH_REQUIRED",
            f"remote integration branch was not read back: {branch}",
        ) from error
    return entry["repository_config"], entry


def _resolved_read_only_config(
    args: argparse.Namespace, config: dict[str, Any]
) -> dict[str, Any]:
    repo_config = _repository_config(config, args.repo)
    if repo_config.get("integration_branch"):
        return repo_config
    source = getattr(args, "coordinator_context", None)
    if not source:
        raise core.PolicyError(
            "INTEGRATION_BRANCH_REQUIRED",
            "configure integration_branch or provide Coordinator readback",
        )
    context = _read_json(source)
    if not isinstance(context, dict):
        raise core.PolicyError(
            "COORDINATOR_CONTEXT_INVALID", "Coordinator context must be an object"
        )
    repo_config["integration_branch"] = core.resolve_integration_branch(
        repo_config, context
    )
    return repo_config


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
    execution_slots = int(repo_config["execution_slots"])
    snapshot["execution_slots"] = execution_slots
    snapshot["integration_wip_limit"] = int(repo_config["integration_wip_limit"])
    snapshot["worker_slots"] = execution_slots
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
                dispatch = issue.get("dispatch") or {}
                desired_state = None
                if (
                    dispatch.get("parked") is True
                    or dispatch.get("status") == "blocked"
                ):
                    desired_state = "blocked"
                elif dispatch.get("status") in {"running", "resuming"}:
                    desired_state = "active"
                _persist_record_updates(
                    github,
                    repo,
                    observed,
                    [
                        {
                            "issue": int(issue["number"]),
                            "dispatch": dispatch,
                            "state": desired_state,
                        }
                    ],
                )
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
        if dispatch.get("status") in {
            "claiming",
            "parking",
            "resuming",
            "blocked",
            "retired",
            "merged",
        }:
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
                "base_sha": snapshot.get("base_sha"),
                "contract_sha256": (issue.get("contract") or {}).get("sha256"),
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


def _candidate_label_names(candidate: dict[str, Any]) -> set[str]:
    return {
        str(label.get("name") if isinstance(label, dict) else label).casefold()
        for label in candidate.get("labels") or []
    }


def _frontier_policy(repo_config: dict[str, Any]) -> dict[str, Any]:
    intake = dict(repo_config.get("intake") or {})
    execution_slots = int(repo_config["execution_slots"])
    return {
        "include_labels": list(intake.get("include_labels") or []),
        "human_labels": list(intake.get("human_labels") or []),
        "clarify_labels": list(intake.get("clarify_labels") or []),
        "reserve_target": int(
            intake.get(
                "ready_reserve_target",
                max(6, execution_slots * 2),
            )
        ),
    }


def _frontier_snapshot(
    github: GitHub, repo: str, repo_config: dict[str, Any]
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    intake = dict(repo_config.get("intake") or {})
    limit = int(intake.get("candidate_limit", 100))
    labels = [
        label
        for field in ("include_labels", "human_labels", "clarify_labels")
        for label in intake.get(field) or []
    ]
    if labels:
        labels.extend(["orch:ready", "orch:active", "orch:blocked"])
    candidates = github.frontier_candidates(repo, limit, labels)
    snapshot = github.snapshot(repo, repo_config["integration_branch"])
    execution_slots = int(repo_config["execution_slots"])
    snapshot["execution_slots"] = execution_slots
    snapshot["integration_wip_limit"] = int(repo_config["integration_wip_limit"])
    snapshot["worker_slots"] = execution_slots
    return candidates, snapshot


def _admission_target_numbers(value: Any, *, repository: str) -> list[int]:
    """Validate plan target identity before fetching full Issue comments."""

    if not isinstance(value, dict) or value.get("schema_version") != 1:
        raise core.PolicyError(
            "ADMISSION_PLAN_INVALID", "admission plan schema_version must be 1"
        )
    if str(value.get("repository") or "").casefold() != repository.casefold():
        raise core.PolicyError(
            "REPOSITORY_MISMATCH", "admission plan repository mismatch"
        )
    admissions = value.get("admissions")
    if not isinstance(admissions, list) or not admissions:
        raise core.PolicyError(
            "ADMISSION_PLAN_INVALID", "admissions must be a non-empty list"
        )
    numbers: list[int] = []
    for admission in admissions:
        if not isinstance(admission, dict):
            raise core.PolicyError(
                "ADMISSION_PLAN_INVALID", "each admission must be an object"
            )
        number = admission.get("issue")
        if (
            not isinstance(number, int)
            or isinstance(number, bool)
            or number <= 0
            or number in numbers
        ):
            raise core.PolicyError(
                "ADMISSION_ISSUE_INVALID", "admission Issue numbers must be unique"
            )
        numbers.append(number)
    return numbers


def _validate_admission_plan(
    value: Any,
    *,
    repository: str,
    candidates: list[dict[str, Any]],
    managed_issues: list[dict[str, Any]] | None = None,
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    """Validate a complete admission batch before the first GitHub mutation."""

    if not isinstance(value, dict) or value.get("schema_version") != 1:
        raise core.PolicyError(
            "ADMISSION_PLAN_INVALID", "admission plan schema_version must be 1"
        )
    if str(value.get("repository") or "").casefold() != repository.casefold():
        raise core.PolicyError(
            "REPOSITORY_MISMATCH", "admission plan repository mismatch"
        )
    admissions = value.get("admissions")
    if not isinstance(admissions, list) or not admissions:
        raise core.PolicyError(
            "ADMISSION_PLAN_INVALID", "admissions must be a non-empty list"
        )
    by_number = {int(candidate["number"]): candidate for candidate in candidates}
    resolved: list[tuple[dict[str, Any], dict[str, Any]]] = []
    seen: set[int] = set()
    dependency_graph: dict[int, set[int]] = {}
    core_labels = {"orch:ready", "orch:active", "orch:blocked"}
    canonical_triage_labels = {
        "needs-triage",
        "needs-info",
        "ready-for-agent",
        "ready-for-human",
        "wontfix",
    }
    for admission in admissions:
        if not isinstance(admission, dict):
            raise core.PolicyError(
                "ADMISSION_PLAN_INVALID", "each admission must be an object"
            )
        number = admission.get("issue")
        if (
            not isinstance(number, int)
            or isinstance(number, bool)
            or number <= 0
            or number in seen
        ):
            raise core.PolicyError(
                "ADMISSION_ISSUE_INVALID", "admission Issue numbers must be unique"
            )
        seen.add(number)
        candidate = by_number.get(number)
        if candidate is None:
            raise core.PolicyError(
                "ADMISSION_ISSUE_MISSING",
                f"Issue #{number} is outside the Candidate Pool",
            )
        candidate_labels = _candidate_label_names(candidate)
        if candidate_labels & canonical_triage_labels != {"ready-for-agent"}:
            raise core.PolicyError(
                "ADMISSION_ISSUE_NOT_READY",
                f"Issue #{number} must be unambiguously ready-for-agent",
            )
        contract = admission.get("contract")
        core.validate_contract(contract)
        if core.contract_version(contract) != 2:
            raise core.PolicyError(
                "ADMISSION_CONTRACT_VERSION_INVALID",
                "new admissions require Contract V2",
            )
        dependencies = set(core.contract_dispatch_after(contract)) | set(
            core.contract_merge_after(contract)
        )
        if number in dependencies:
            raise core.PolicyError(
                "CONTRACT_DEPENDENCY_INVALID",
                f"Issue #{number} cannot depend on itself",
            )
        dependency_graph[number] = dependencies
        core_states = candidate_labels & core_labels
        marker_comments = [
            comment
            for comment in candidate.get("comments") or []
            if any(
                marker in str(comment.get("body") or "")
                for marker in (core.ISSUE_MARKER_V1, core.ISSUE_MARKER_V2)
            )
        ]
        if len(marker_comments) > 1 or (core_states and len(marker_comments) != 1):
            raise core.PolicyError(
                "ISSUE_ALREADY_MANAGED", f"Issue #{number} is already managed"
            )
        if marker_comments:
            existing = core.parse_issue_record(
                str(marker_comments[0].get("body") or "")
            )
            existing_contract = existing.get("contract") or {}
            try:
                core.validate_contract(existing_contract)
            except core.PolicyError as error:
                raise core.PolicyError(
                    "ISSUE_ALREADY_MANAGED",
                    f"Issue #{number} has an invalid existing contract",
                ) from error
            if (
                existing_contract != contract
                or existing.get("dispatch") not in (None, {})
                or (core_states and core_states != {"orch:ready"})
            ):
                raise core.PolicyError(
                    "ISSUE_ALREADY_MANAGED",
                    f"Issue #{number} already has another orchestration record",
                )
        resolved.append((candidate, contract))

    admitted = set(dependency_graph)
    all_dependencies = {
        int(issue["number"]): set(
            issue.get("dispatch_after")
            if issue.get("dispatch_after") is not None
            else issue.get("dependencies") or []
        )
        | set(
            issue.get("merge_after")
            if issue.get("merge_after") is not None
            else issue.get("dependencies") or []
        )
        for issue in managed_issues or []
    }
    all_dependencies.update(dependency_graph)
    managed_numbers = set(all_dependencies)
    graph = {
        issue: dependencies & managed_numbers
        for issue, dependencies in all_dependencies.items()
    }
    visiting: set[int] = set()
    visited: set[int] = set()

    def visit(issue: int) -> None:
        if issue in visiting:
            raise core.PolicyError(
                "CONTRACT_DEPENDENCY_CYCLE", "admission dependency cycle"
            )
        if issue in visited:
            return
        visiting.add(issue)
        for dependency in graph[issue]:
            visit(dependency)
        visiting.remove(issue)
        visited.add(issue)

    for issue in sorted(admitted):
        visit(issue)
    return resolved


def _frontier(args: argparse.Namespace) -> dict[str, Any]:
    config = _load_config(args.config, write_migration=False)
    if args.operation == "admit":
        if not args.plan:
            raise core.PolicyError(
                "ADMISSION_PLAN_REQUIRED", "frontier admit requires --plan"
            )
        repo_config, entry = _coordinator_preflight(args, config)
        if entry["status"] == "forwarded":
            return entry
    else:
        repo_config = _resolved_read_only_config(args, config)

    github = GitHub()

    def read_and_analyze() -> tuple[
        list[dict[str, Any]], dict[str, Any], dict[str, Any]
    ]:
        candidates, snapshot = _frontier_snapshot(github, args.repo, repo_config)
        analysis = frontier.analyze_frontier(
            candidates, snapshot, _frontier_policy(repo_config)
        )
        analysis["candidates"] = [
            {
                key: candidate.get(key)
                for key in (
                    "number",
                    "title",
                    "body",
                    "updatedAt",
                    "labels",
                    "milestone",
                    "assignees",
                )
            }
            for candidate in candidates
        ]
        return candidates, snapshot, analysis

    if args.operation == "scan":
        _candidates, _snapshot, analysis = read_and_analyze()
        design_count = sum(
            item["disposition"] == "design"
            for item in analysis["candidate_assessments"]
        )
        return _envelope(
            "needs-admission" if design_count and analysis["reserve_gap"] else "idle",
            summary=analysis,
        )

    plan = _read_json(args.plan)
    with _v61_mutation_guard(args.repo):
        candidates, snapshot, analysis = read_and_analyze()
        target_numbers = _admission_target_numbers(plan, repository=args.repo)
        details = github.issues_by_number(args.repo, target_numbers)
        details_by_number = {int(issue["number"]): issue for issue in details}
        if set(details_by_number) != set(target_numbers):
            raise core.PolicyError(
                "ADMISSION_ISSUE_READBACK_MISSING",
                "one or more admission Issues could not be read back",
            )
        candidates = [
            details_by_number.get(int(candidate["number"]), candidate)
            for candidate in candidates
        ]
        admissions = _validate_admission_plan(
            plan,
            repository=args.repo,
            candidates=candidates,
            managed_issues=snapshot.get("issues") or [],
        )
        dependency_numbers = sorted(
            {
                dependency
                for _candidate, contract in admissions
                for dependency in [
                    *core.contract_dispatch_after(contract),
                    *core.contract_merge_after(contract),
                ]
                if dependency not in {int(item["number"]) for item in candidates}
            }
        )
        missing_dependencies = (
            [
                number
                for number, state in github.dependency_states(
                    args.repo, dependency_numbers
                ).items()
                if state is None
            ]
            if dependency_numbers
            else []
        )
        if missing_dependencies:
            raise core.PolicyError(
                "CONTRACT_DEPENDENCY_INVALID",
                f"dependency Issues do not exist: {missing_dependencies}",
            )
        admitted = [
            github.admit(args.repo, candidate, contract)
            for candidate, contract in admissions
        ]
    return _envelope("completed", summary={**analysis, "admitted": admitted})


def _reconcile(args: argparse.Namespace) -> dict[str, Any]:
    observations = _observations(args.observations)
    lifecycle_command = "park" if getattr(args, "park", None) else None
    if getattr(args, "resume", None):
        lifecycle_command = "resume"
    lifecycle_dispatch = (
        getattr(args, lifecycle_command, None) if lifecycle_command else None
    )
    if args.snapshot:
        snapshot = _read_json(args.snapshot)
        if snapshot.get("repository") not in {None, args.repo}:
            raise core.PolicyError(
                "REPOSITORY_MISMATCH", "snapshot repository mismatch"
            )
        snapshot["repository"] = args.repo
        snapshot = core.apply_observations(snapshot, observations)
        if lifecycle_command:
            lifecycle = core.plan_lifecycle_command(
                snapshot, lifecycle_dispatch, lifecycle_command
            )
            return _envelope(
                lifecycle["status"],
                actions=lifecycle["actions"],
                warnings=lifecycle["warnings"],
                summary={"record_updates": lifecycle["record_updates"]},
            )
        result = core.plan_reconcile(snapshot)
        partial = core.plan_partial_dispatch(snapshot)
        lifecycle = core.plan_lifecycle_transitions(snapshot)
        result["actions"] = _dedupe_actions(
            [*result["actions"], *partial["actions"], *lifecycle["actions"]]
        )
        result["warnings"].extend(partial["warnings"])
        result["status"] = "actions" if result["actions"] else result["status"]
        if partial["record_updates"]:
            result["summary"]["record_updates"] = partial["record_updates"]
        return result

    config = _load_config(args.config, write_migration=False)
    if args.read_only:
        if lifecycle_command:
            raise core.PolicyError(
                "LIFECYCLE_REQUIRES_WRITE", "Park/Resume cannot run read-only"
            )
        repo_config = _resolved_read_only_config(args, config)
        github = GitHub()
        snapshot, runtime = _prepare_snapshot(
            github, args.repo, repo_config, observations, mutate=False
        )
        planned = core.plan_reconcile(snapshot)
        partial = core.plan_partial_dispatch(snapshot)
        lifecycle = core.plan_lifecycle_transitions(snapshot)
        recovery_actions, recovery_warnings = _plan_recoveries(
            snapshot, repo_config, runtime, github, mutate=False
        )
        planned["actions"] = _dedupe_actions(
            [
                *planned["actions"],
                *partial["actions"],
                *lifecycle["actions"],
                *recovery_actions,
            ]
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
        if lifecycle["record_updates"]:
            planned["summary"]["pending_lifecycle_updates"] = len(
                lifecycle["record_updates"]
            )
        planned["status"] = "actions" if planned["actions"] else planned["status"]
        return planned

    repo_config, entry = _coordinator_preflight(args, config)
    if entry["status"] == "forwarded":
        return entry
    github = GitHub()
    with _v61_mutation_guard(args.repo):
        snapshot, runtime = _prepare_snapshot(
            github, args.repo, repo_config, observations, mutate=True
        )
        if lifecycle_command:
            lifecycle = core.plan_lifecycle_command(
                snapshot, lifecycle_dispatch, lifecycle_command
            )
            _persist_record_updates(
                github, args.repo, snapshot, lifecycle["record_updates"]
            )
            return _envelope(
                lifecycle["status"],
                actions=lifecycle["actions"],
                warnings=lifecycle["warnings"],
                summary={
                    "dispatch": lifecycle_dispatch,
                    "record_updates": len(lifecycle["record_updates"]),
                },
            )
        repo_config = _reload_repo_config(args, repo_config)
        recovery_actions, recovery_warnings = _plan_recoveries(
            snapshot, repo_config, runtime, github, mutate=True
        )
        planned = core.plan_reconcile(snapshot)
        partial = core.plan_partial_dispatch(snapshot)
        lifecycle = core.plan_lifecycle_transitions(snapshot)
        _persist_record_updates(
            github, args.repo, snapshot, lifecycle["record_updates"]
        )
        _persist_record_updates(github, args.repo, snapshot, partial["record_updates"])
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
        materialized.extend(lifecycle["actions"])
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
    repo_config, entry = _coordinator_preflight(args, config)
    if entry["status"] == "forwarded":
        return entry
    if repo_config["integration_branch"] == "main":
        raise core.PolicyError(
            "MAIN_RELEASE_REQUIRES_EXPLICIT_REQUEST",
            "Orchestrator does not automatically release to main",
        )
    github = GitHub()
    with _v61_mutation_guard(args.repo):
        snapshot, runtime = _prepare_snapshot(
            github, args.repo, repo_config, [], mutate=True
        )
        workspace = snapshot["coordinator_workspace"]
        core.qualify_workspace(workspace, repo_config, operation="integrate")
        repo_config = _reload_repo_config(args, repo_config)
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
            return _envelope(
                "waiting",
                warnings=[
                    {
                        "code": "INTEGRATION_ORDER_WAIT",
                        "next_issue": ordered[0]["number"] if ordered else None,
                    }
                ],
                summary={"pr": args.pr, "issue": issue["number"]},
            )
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
            return _envelope(
                "waiting",
                summary={
                    "pr": args.pr,
                    "issue": issue["number"],
                    "updated_branch": bool(plan["actions"]),
                },
            )
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
        return _envelope(
            "idle",
            warnings=[{"code": "cleanup-deferred", "blockers": cleanup["blockers"]}]
            if cleanup["blockers"] or cleanup["manual_cleanup"]
            else [],
            summary={
                "pr": args.pr,
                "issue": issue["number"],
                "merged_at": readback["mergedAt"],
                "cleanup": cleanup,
            },
        )


def _retire(args: argparse.Namespace) -> dict[str, Any]:
    if args.snapshot:
        return core.plan_retirement(_read_json(args.snapshot))
    issue_number = core.dispatch_issue(args.dispatch)
    config = _load_config(args.config, write_migration=False)
    repo_config, entry = _coordinator_preflight(args, config)
    if entry["status"] == "forwarded":
        return entry
    github = GitHub()
    with _v61_mutation_guard(args.repo):
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
        repo_config = _reload_repo_config(args, repo_config)
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
        # retire_issue already persisted the terminal before any destructive step.
        return _envelope(
            "blocked" if cleanup["blockers"] else "idle",
            summary={"dispatch": args.dispatch, "cleanup": cleanup},
        )


def _project(args: argparse.Namespace) -> dict[str, Any]:
    config = _load_config(args.config, write_migration=False)
    repo_config, entry = _coordinator_preflight(args, config)
    if entry["status"] == "forwarded":
        return entry
    number = repo_config.get("project_number")
    owner = repo_config.get("project_owner") or args.repo.split("/", 1)[0]
    github = GitHub()
    with _v61_mutation_guard(args.repo):
        _prepare_snapshot(github, args.repo, repo_config, [], mutate=True)
        repo_config = _reload_repo_config(args, repo_config)
        try:
            labels = github.project_labels(args.repo)
            if not number:
                return _envelope(
                    "idle",
                    summary={"labels": labels, "project": "not-configured"},
                )
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
            return _envelope(
                "waiting",
                warnings=[{"code": "project-sync-degraded", "detail": str(error)}],
                summary={"project_optional": True},
            )
    return _envelope(
        "idle",
        summary={
            "labels": labels,
            "project_optional": True,
            "project_number": number,
            "synced_items": synced,
            "manual_views": [
                "Backlog Table: group/filter by Status, Priority, Wave, Risk",
                "Current Wave Board: filter Active/Review/Ready to merge and group by Wave",
            ],
        },
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    reconcile = commands.add_parser("reconcile")
    reconcile.add_argument("--repo", required=True)
    reconcile.add_argument("--read-only", action="store_true")
    reconcile.add_argument("--observations")
    lifecycle = reconcile.add_mutually_exclusive_group()
    lifecycle.add_argument("--park")
    lifecycle.add_argument("--resume")
    reconcile.add_argument("--coordinator-context")
    reconcile.add_argument("--snapshot", help=argparse.SUPPRESS)
    reconcile.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    integrate = commands.add_parser("integrate")
    integrate.add_argument("--repo", required=True)
    integrate.add_argument("--pr", required=True, type=int)
    integrate.add_argument("--coordinator-context")
    integrate.add_argument("--snapshot", help=argparse.SUPPRESS)
    integrate.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    retire = commands.add_parser("retire")
    retire.add_argument("--repo", required=True)
    retire.add_argument("--dispatch", required=True)
    retire.add_argument("--coordinator-context")
    retire.add_argument("--snapshot", help=argparse.SUPPRESS)
    retire.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    project = commands.add_parser("project")
    project.add_argument("operation", choices=("init", "sync"))
    project.add_argument("--repo", required=True)
    project.add_argument("--coordinator-context")
    project.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    frontier_command = commands.add_parser("frontier")
    frontier_command.add_argument("operation", choices=("scan", "admit"))
    frontier_command.add_argument("--repo", required=True)
    frontier_command.add_argument("--plan", type=Path)
    frontier_command.add_argument("--coordinator-context")
    frontier_command.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(errors="replace")
    args = parse_args(argv)
    try:
        if args.command == "reconcile":
            result = _reconcile(args)
        elif args.command == "frontier":
            result = _frontier(args)
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
                _envelope("blocked", warnings=[{"code": code, "detail": str(error)}]),
                ensure_ascii=False,
            )
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
