"""Production GitHub adapter for one complete ready-Ticket snapshot."""

from __future__ import annotations

import json
import re
import subprocess
from typing import Any, Mapping, Protocol

from ._canonical import CanonicalJsonError, digest_value, strict_json_loads
from .activation import GitHubContentClient
from .plan_control import PlanControlError, frozen_ticket_contract_digest


_REPOSITORY = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_BRANCH = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]*$")
_ISSUE_KEY = re.compile(r"^issue:([1-9][0-9]*)$")
_ISSUE_SHORT = re.compile(r"^#([1-9][0-9]*)$")
_BLOCKED_BY_HEADING = re.compile(
    r"(?im)^[ \t]*##[ \t]+Blocked[ \t]+by[ \t]*$"
)
_NEXT_LEVEL_TWO_HEADING = re.compile(r"(?m)^[ \t]*##(?:[ \t]+|$)")
_BLOCKER_BULLET = re.compile(r"^[ \t]*[-*+][ \t]+(.+?)[ \t]*$")
_OID = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
_DEPENDENCY_UNSUPPORTED = re.compile(
    r"(?is)\bHTTP (?:404|410|422)\b.*\bissue dependencies\b.*\b(?:not supported|unavailable|disabled)\b"
)


class GitHubIssueReadClient(Protocol):
    def read_issue(self, repository: str, number: int) -> Mapping[str, Any]: ...

    def read_comments(
        self,
        repository: str,
        number: int,
    ) -> tuple[Mapping[str, Any], ...]: ...

    def read_blockers(
        self,
        repository: str,
        number: int,
    ) -> tuple[Mapping[str, Any], ...] | None: ...

    def read_branch_source(
        self,
        repository: str,
        branch: str,
    ) -> Mapping[str, str]: ...


class GitHubHumanApprovalReadClient(Protocol):
    """Read-only seam for the durable human-approval workflow record."""

    def read_human_approval(
        self,
        repository: str,
        readback_ref: str,
    ) -> Mapping[str, Any] | None: ...


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

    def _run(self, arguments: list[str]) -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run(
                [self.executable, *arguments],
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

    def _read_json(self, endpoint: str) -> Any:
        result = self._run(["api", endpoint])
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

    def _read_pages(self, endpoint: str) -> tuple[Mapping[str, Any], ...]:
        result = self._run(["api", "--paginate", "--slurp", endpoint])
        if result.returncode != 0:
            raise PlanControlError(
                "GITHUB_SNAPSHOT_UNAVAILABLE",
                "GitHub paginated snapshot API read failed",
            )
        try:
            pages = json.loads(result.stdout)
        except json.JSONDecodeError as error:
            raise PlanControlError(
                "GITHUB_SNAPSHOT_INVALID",
                "GitHub paginated snapshot API returned malformed JSON",
            ) from error
        if (
            type(pages) is not list
            or any(type(page) is not list for page in pages)
            or any(
                type(item) is not dict
                for page in pages
                for item in page
            )
        ):
            raise PlanControlError(
                "GITHUB_SNAPSHOT_INVALID",
                "GitHub paginated snapshot readback is not a list of pages",
            )
        return tuple(item for page in pages for item in page)

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
    ) -> tuple[Mapping[str, Any], ...] | None:
        endpoint = (
            f"repos/{repository}/issues/{number}/dependencies/blocked_by"
        )
        result = self._run(["api", "--paginate", "--slurp", endpoint])
        if result.returncode != 0:
            detail = "\n".join((result.stderr, result.stdout))
            if _DEPENDENCY_UNSUPPORTED.search(detail):
                return None
            raise PlanControlError(
                "GITHUB_SNAPSHOT_UNAVAILABLE",
                "GitHub native dependency API read failed",
            )
        return self._decode_pages(result.stdout)

    def read_comments(
        self,
        repository: str,
        number: int,
    ) -> tuple[Mapping[str, Any], ...]:
        return self._read_pages(
            f"repos/{repository}/issues/{number}/comments?per_page=100"
        )

    def _decode_pages(self, payload: str) -> tuple[Mapping[str, Any], ...]:
        try:
            pages = json.loads(payload)
        except json.JSONDecodeError as error:
            raise PlanControlError(
                "GITHUB_SNAPSHOT_INVALID",
                "GitHub paginated snapshot API returned malformed JSON",
            ) from error
        if (
            type(pages) is not list
            or any(type(page) is not list for page in pages)
            or any(type(item) is not dict for page in pages for item in page)
        ):
            raise PlanControlError(
                "GITHUB_SNAPSHOT_INVALID",
                "GitHub paginated snapshot readback is not a list of pages",
            )
        return tuple(item for page in pages for item in page)

    def read_branch_source(
        self,
        repository: str,
        branch: str,
    ) -> Mapping[str, str]:
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
        commit_oid = value["object"]["sha"]
        commit = self._read_json(
            f"repos/{repository}/git/commits/{commit_oid}"
        )
        if (
            type(commit) is not dict
            or type(commit.get("tree")) is not dict
            or type(commit["tree"].get("sha")) is not str
            or _OID.fullmatch(commit_oid) is None
            or _OID.fullmatch(commit["tree"]["sha"]) is None
        ):
            raise PlanControlError(
                "GITHUB_SNAPSHOT_INVALID",
                "GitHub target branch source omitted its immutable tree identity",
            )
        return {
            "input_ref": f"refs/heads/{branch}",
            "resolved_commit_oid": commit_oid,
            "tree_oid": commit["tree"]["sha"],
        }

    def read_branch_oid(self, repository: str, branch: str) -> str:
        """Compatibility read for unrelated callers; snapshots use full source."""

        return self.read_branch_source(repository, branch)["resolved_commit_oid"]


class GitHubCliHumanApprovalReadClient:
    """Authenticated, read-only reads of a durable approval API record."""

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

    def _run(self, endpoint: str) -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run(
                [self.executable, "api", endpoint],
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=self.command_timeout_seconds,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise PlanControlError(
                "GITHUB_HUMAN_APPROVAL_UNAVAILABLE",
                "GitHub human-approval read is unavailable",
            ) from error

    @staticmethod
    def _endpoint(repository: str, readback_ref: str) -> str:
        if (
            type(repository) is not str
            or _REPOSITORY.fullmatch(repository) is None
            or type(readback_ref) is not str
            or not readback_ref
        ):
            raise PlanControlError(
                "GITHUB_HUMAN_APPROVAL_INVALID",
                "GitHub human-approval readback identity is invalid",
            )
        api_prefix = "https://api.github.com/"
        if readback_ref.startswith(api_prefix):
            endpoint = readback_ref.removeprefix(api_prefix)
        elif readback_ref.startswith(f"repos/{repository}/"):
            endpoint = readback_ref
        else:
            raise PlanControlError(
                "GITHUB_HUMAN_APPROVAL_INVALID",
                "Human approval readback must name an exact GitHub API record",
            )
        if "://" in endpoint or ".." in endpoint or "\\" in endpoint:
            raise PlanControlError(
                "GITHUB_HUMAN_APPROVAL_INVALID",
                "Human approval readback contains an unsafe reference",
            )
        return endpoint

    def read_human_approval(
        self,
        repository: str,
        readback_ref: str,
    ) -> Mapping[str, Any] | None:
        endpoint = self._endpoint(repository, readback_ref)
        result = self._run(endpoint)
        if result.returncode != 0:
            detail = f"{result.stdout}\n{result.stderr}".casefold()
            if "404" in detail or "not found" in detail:
                return None
            raise PlanControlError(
                "GITHUB_HUMAN_APPROVAL_UNAVAILABLE",
                "GitHub human-approval API read failed",
            )
        try:
            value = strict_json_loads(result.stdout)
        except CanonicalJsonError as error:
            raise PlanControlError(
                "GITHUB_HUMAN_APPROVAL_INVALID",
                "GitHub human-approval API returned malformed JSON",
            ) from error
        if type(value) is not dict:
            raise PlanControlError(
                "GITHUB_HUMAN_APPROVAL_INVALID",
                "GitHub human-approval API returned a non-object",
            )
        return value


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


def _exact_repository_urls(repository: str, number: int) -> tuple[str, str, str]:
    api_repository = f"https://api.github.com/repos/{repository}"
    return (
        api_repository,
        f"{api_repository}/issues/{number}",
        f"https://github.com/{repository}/issues/{number}",
    )


def _canonical_issue(
    issue: Mapping[str, Any],
    *,
    repository: str,
    number: int,
    require_ready: bool,
) -> tuple[list[str], list[dict[str, Any]], dict[str, Any]]:
    repository_url, issue_url, html_url = _exact_repository_urls(
        repository,
        number,
    )
    if (
        type(issue) is not dict
        or type(issue.get("id")) is not int
        or issue["id"] < 1
        or type(issue.get("node_id")) is not str
        or not issue["node_id"]
        or issue.get("number") != number
        or issue.get("repository_url") != repository_url
        or issue.get("url") != issue_url
        or issue.get("html_url") != html_url
        or "pull_request" in issue
        or type(issue.get("title")) is not str
        or not issue["title"]
        or type(issue.get("body")) is not str
        or not issue["body"]
        or type(issue.get("state")) is not str
        or issue["state"].lower() not in {"open", "closed"}
        or type(issue.get("updated_at")) is not str
        or not issue["updated_at"]
    ):
        raise PlanControlError(
            "GITHUB_SNAPSHOT_INVALID",
            "GitHub issue contract or exact repository identity is incomplete",
        )
    labels_value = issue.get("labels")
    labels = _label_names(labels_value)
    if (
        type(labels_value) is not list
        or any(type(label) is not dict for label in labels_value)
    ):
        raise PlanControlError(
            "GITHUB_SNAPSHOT_INVALID",
            "GitHub issue labels must be complete API records",
        )
    if require_ready and (
        issue["state"].lower() != "open"
        or "ready-for-agent" not in labels
        or set(labels).intersection(
            {
                "needs-triage",
                "needs-info",
                "ready-for-human",
                "wontfix",
            }
        )
    ):
        raise PlanControlError(
            "TICKET_LABEL_INVALID",
            "GitHub issue is not an open ready-for-agent Ticket",
        )
    issue_type = issue.get("type")
    if issue_type is not None and type(issue_type) is not dict:
        raise PlanControlError(
            "GITHUB_SNAPSHOT_INVALID",
            "GitHub issue type is malformed",
        )
    state_reason = issue.get("state_reason")
    if state_reason is not None and type(state_reason) is not str:
        raise PlanControlError(
            "GITHUB_SNAPSHOT_INVALID",
            "GitHub issue state reason is malformed",
        )
    return (
        labels,
        sorted(
            (
                {
                    "id": label.get("id"),
                    "node_id": label.get("node_id"),
                    "url": label.get("url"),
                    "name": label.get("name"),
                    "color": label.get("color"),
                    "default": label.get("default"),
                    "description": label.get("description"),
                }
                for label in labels_value
            ),
            key=lambda label: str(label["name"]),
        ),
        {
            "id": issue["id"],
            "node_id": issue["node_id"],
            "number": number,
            "title": issue["title"],
            "body": issue["body"],
            "state": issue["state"].lower(),
            "state_reason": state_reason,
            "type": None if issue_type is None else dict(issue_type),
            "repository": {
                "full_name": repository,
                "url": repository_url,
            },
            "url": issue_url,
            "html_url": html_url,
            "updated_at": issue["updated_at"],
        },
    )


def _body_blocker_numbers(
    body: str,
    repository: str,
) -> tuple[int, ...] | None:
    heading = _BLOCKED_BY_HEADING.search(body)
    if heading is None:
        return None
    remainder = body[heading.end():]
    next_heading = _NEXT_LEVEL_TWO_HEADING.search(remainder)
    section = remainder[:next_heading.start()] if next_heading else remainder
    numbers: list[int] = []
    for raw_line in section.splitlines():
        if not raw_line.strip():
            continue
        bullet = _BLOCKER_BULLET.fullmatch(raw_line)
        if bullet is None:
            raise PlanControlError(
                "GITHUB_BLOCKERS_CONFLICT",
                "Blocked-by body section contains a non-reference entry",
            )
        reference = bullet.group(1).strip()
        try:
            number = _issue_number(repository, reference)
        except PlanControlError as error:
            raise PlanControlError(
                "GITHUB_BLOCKERS_CONFLICT",
                "Blocked-by body section contains a non-local Issue reference",
            ) from error
        if number in numbers:
            raise PlanControlError(
                "GITHUB_BLOCKERS_CONFLICT",
                "Blocked-by body section repeats an Issue",
            )
        numbers.append(number)
    return tuple(numbers)


def _canonical_comments(
    comments: object,
    *,
    repository: str,
    issue_number: int,
) -> list[dict[str, Any]]:
    if (
        type(comments) not in {tuple, list}
        or any(type(comment) is not dict for comment in comments)
    ):
        raise PlanControlError(
            "GITHUB_SNAPSHOT_INVALID",
            "GitHub issue comments readback is incomplete",
        )
    api_prefix = (
        f"https://api.github.com/repos/{repository}/issues/comments/"
    )
    html_prefix = (
        f"https://github.com/{repository}/issues/{issue_number}"
        "#issuecomment-"
    )
    frozen: list[dict[str, Any]] = []
    seen: set[int] = set()
    for comment in comments:
        comment_id = comment.get("id")
        if (
            type(comment_id) is not int
            or comment_id < 1
            or comment_id in seen
            or comment.get("url") != f"{api_prefix}{comment_id}"
            or comment.get("html_url") != f"{html_prefix}{comment_id}"
            or type(comment.get("body")) is not str
            or type(comment.get("node_id")) is not str
            or not comment["node_id"]
            or type(comment.get("user")) is not dict
            or type(comment["user"].get("login")) is not str
            or not comment["user"]["login"]
            or type(comment.get("created_at")) is not str
            or not comment["created_at"]
            or type(comment.get("updated_at")) is not str
            or not comment["updated_at"]
            or type(comment.get("author_association")) is not str
            or not comment["author_association"]
        ):
            raise PlanControlError(
                "GITHUB_SNAPSHOT_INVALID",
                "GitHub issue comment identity or contract is incomplete",
            )
        seen.add(comment_id)
        frozen.append(
            {
                "id": comment_id,
                "node_id": comment.get("node_id"),
                "url": comment["url"],
                "html_url": comment["html_url"],
                "body": comment["body"],
                "user": {"login": (comment.get("user") or {}).get("login")},
                "created_at": comment.get("created_at"),
                "updated_at": comment.get("updated_at"),
                "author_association": comment.get("author_association"),
            }
        )
    return sorted(frozen, key=lambda comment: comment["id"])


def _canonical_blocker(
    blocker: Mapping[str, Any],
    *,
    repository: str,
) -> dict[str, Any]:
    number = blocker.get("number")
    if type(number) is not int or number < 1:
        raise PlanControlError(
            "GITHUB_SNAPSHOT_INVALID",
            "GitHub blocker omitted its Issue number",
        )
    repository_url, issue_url, html_url = _exact_repository_urls(
        repository,
        number,
    )
    state = blocker.get("state")
    if (
        type(blocker.get("id")) is not int
        or blocker["id"] < 1
        or type(blocker.get("node_id")) is not str
        or not blocker["node_id"]
        or type(blocker.get("updated_at")) is not str
        or not blocker["updated_at"]
        or
        blocker.get("repository_url") != repository_url
        or blocker.get("url") != issue_url
        or blocker.get("html_url") != html_url
        or type(state) is not str
        or state.lower() not in {"open", "closed"}
        or "pull_request" in blocker
    ):
        raise PlanControlError(
            "GITHUB_SNAPSHOT_INVALID",
            "GitHub blocker has a foreign or incomplete Issue identity",
        )
    blocker_contract = {
        "key": f"issue:{number}",
        "state": state.lower(),
        "repository": {
            "full_name": repository,
            "url": repository_url,
        },
    }
    return {
        **blocker_contract,
        "source": {
            "ref": f"issue:{number}",
            "digest": digest_value(blocker_contract),
        },
    }


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
            or not policy_path
            or policy_path != policy_path.strip("/")
            or "\\" in policy_path
            or not policy_path.startswith(".gwo-v8/")
            or any(part in {"", ".", ".."} for part in policy_path.split("/"))
        ):
            raise PlanControlError(
                "PLAN_CONTROL_COMPOSITION_INVALID",
                "GitHub snapshot branch or Policy Witness path is invalid",
            )
        self.content_client = content_client
        self.issue_client = issue_client
        self.control_branch = control_branch
        self.target_branch = target_branch
        self.policy_path = policy_path

    def canonical_ready_refs(
        self,
        repository: str,
        ready_refs: tuple[str, ...],
    ) -> tuple[str, ...]:
        """Resolve transport spellings to stable local Ticket identities."""

        if (
            type(repository) is not str
            or _REPOSITORY.fullmatch(repository) is None
            or type(ready_refs) is not tuple
            or not ready_refs
        ):
            raise PlanControlError(
                "READY_REFS_INVALID",
                "GitHub ready reference identity is invalid",
            )
        canonical = tuple(
            sorted(
                f"issue:{_issue_number(repository, ready_ref)}"
                for ready_ref in ready_refs
            )
        )
        if len(set(canonical)) != len(canonical):
            raise PlanControlError(
                "READY_REFS_INVALID",
                "Ready references resolve to the same GitHub issue",
            )
        return canonical

    def _policy(self, repository: str) -> tuple[dict[str, Any], bytes]:
        try:
            content = self.content_client.read(
                repository,
                self.control_branch,
                self.policy_path,
            )
        except Exception as error:
            raise PlanControlError(
                "GITHUB_SNAPSHOT_UNAVAILABLE",
                "GitHub Policy Witness cannot be read",
            ) from error
        if content is None or type(content.content) is not bytes:
            raise PlanControlError(
                "POLICY_WITNESS_INVALID",
                "GitHub Policy Witness is missing",
            )
        try:
            policy = strict_json_loads(content.content)
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
        return policy, content.content

    def _campaign_source(self, repository: str) -> dict[str, str]:
        raw = self.issue_client.read_branch_source(
            repository,
            self.target_branch,
        )
        if (
            type(raw) is not dict
            or set(raw) != {"input_ref", "resolved_commit_oid", "tree_oid"}
            or raw["input_ref"] != f"refs/heads/{self.target_branch}"
            or any(
                type(raw[name]) is not str
                or _OID.fullmatch(raw[name]) is None
                for name in ("resolved_commit_oid", "tree_oid")
            )
        ):
            raise PlanControlError(
                "GITHUB_SNAPSHOT_INVALID",
                "GitHub Campaign source did not read back one immutable commit and tree",
            )
        source = {
            "repository": repository,
            **raw,
        }
        return {**source, "digest": digest_value(source)}

    def _ticket(self, repository: str, ready_ref: str) -> dict[str, Any]:
        number = _issue_number(repository, ready_ref)
        issue = self.issue_client.read_issue(repository, number)
        labels, label_records, issue_contract = _canonical_issue(
            issue,
            repository=repository,
            number=number,
            require_ready=True,
        )
        comments = _canonical_comments(
            self.issue_client.read_comments(repository, number),
            repository=repository,
            issue_number=number,
        )
        body_numbers = _body_blocker_numbers(issue_contract["body"], repository)
        native_values = self.issue_client.read_blockers(repository, number)
        blockers: list[dict[str, Any]] = []
        if native_values is None:
            if body_numbers is None:
                raise PlanControlError(
                    "GITHUB_BLOCKERS_OMITTED",
                    "Native dependencies are unavailable and the Ticket omits its Blocked-by fallback section",
                )
            for blocker_number in body_numbers:
                blocker_issue = self.issue_client.read_issue(repository, blocker_number)
                _canonical_issue(
                    blocker_issue,
                    repository=repository,
                    number=blocker_number,
                    require_ready=False,
                )
                blockers.append(_canonical_blocker(blocker_issue, repository=repository))
        else:
            if type(native_values) not in {tuple, list}:
                raise PlanControlError(
                    "GITHUB_SNAPSHOT_INVALID",
                    "GitHub native blocker readback is not a complete page set",
                )
            blockers = [
                _canonical_blocker(blocker, repository=repository)
                for blocker in native_values
            ]
            native_numbers = tuple(
                int(blocker["key"].removeprefix("issue:"))
                for blocker in blockers
            )
            if len(set(native_numbers)) != len(native_numbers):
                raise PlanControlError(
                    "GITHUB_SNAPSHOT_INVALID",
                    "GitHub native blockers repeat an Issue",
                )
            if body_numbers is None and native_numbers:
                raise PlanControlError(
                    "GITHUB_BLOCKERS_OMITTED",
                    "Ticket body omits native dependency references",
                )
            if body_numbers is not None and set(body_numbers) != set(native_numbers):
                raise PlanControlError(
                    "GITHUB_BLOCKERS_CONFLICT",
                    "Ticket body and native dependencies disagree",
                )
        complete_contract = {
            "id": issue_contract["id"],
            "node_id": issue_contract["node_id"],
            "number": issue_contract["number"],
            "title": issue_contract["title"],
            "body": issue_contract["body"],
            "state": issue_contract["state"],
            "state_reason": issue_contract["state_reason"],
            "type": issue_contract["type"],
            "repository": issue_contract["repository"],
            "labels": label_records,
            "comments": comments,
            "updated_at": issue_contract["updated_at"],
        }
        canonical_blockers = sorted(blockers, key=lambda item: item["key"])
        key = f"issue:{number}"
        return {
            "key": key,
            "labels": labels,
            "source": {
                "ref": key,
                "digest": frozen_ticket_contract_digest(
                    key=key,
                    contract=complete_contract,
                    labels=labels,
                    native_blockers=canonical_blockers,
                ),
            },
            "contract": complete_contract,
            "native_blockers": canonical_blockers,
        }

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
            raise PlanControlError("GITHUB_SNAPSHOT_INVALID", "GitHub snapshot identity is invalid")
        ready_refs = self.canonical_ready_refs(repository, ready_refs)
        policy, policy_bytes = self._policy(repository)
        source = self._campaign_source(repository)
        tickets = [self._ticket(repository, ready_ref) for ready_ref in ready_refs]
        if len({ticket["key"] for ticket in tickets}) != len(tickets):
            raise PlanControlError("READY_REFS_INVALID", "Ready references resolve to the same GitHub issue")
        # GitHub serves these facts through independent endpoints.  Re-read the
        # complete selected frontier at a bounded final barrier; publishing a
        # mixed issue/comment/blocker/ref view is forbidden.
        if (
            self._campaign_source(repository) != source
            or self._policy(repository)[1] != policy_bytes
            or [self._ticket(repository, ready_ref) for ready_ref in ready_refs]
            != tickets
        ):
            raise PlanControlError(
                "GITHUB_SNAPSHOT_CONCURRENT_CHANGE",
                "GitHub selected Ticket frontier changed during snapshot capture",
            )
        return {
            "repository": repository,
            "target_branch": self.target_branch,
            "campaign_source": source,
            "policy": policy,
            "tickets": tickets,
        }
