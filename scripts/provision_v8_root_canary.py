from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable, Protocol, Sequence


ROOT_REPOSITORY = "NOirBRight/github-work-orchestrator"
APPROVAL = "CREATE-GWO-V8-GA-ROOT-CANARY-TICKETS"

_READBACK_PREFLIGHT_QUERY = """
query($owner: String!, $name: String!) {
  repository(owner: $owner, name: $name) {
    issues(first: 1, states: [OPEN, CLOSED]) {
      nodes {
        id
        number
        title
        body
        state
        repository { nameWithOwner url }
        labels(first: 1) {
          nodes { id name }
          pageInfo { hasNextPage endCursor }
        }
        comments(first: 1) {
          nodes { id body }
          pageInfo { hasNextPage endCursor }
        }
        blockedBy(first: 1) {
          nodes {
            id
            number
            state
            repository { nameWithOwner url }
          }
          pageInfo { hasNextPage endCursor }
        }
      }
      pageInfo { hasNextPage endCursor }
    }
  }
}
""".strip()


@dataclass(frozen=True, slots=True)
class RootCanaryTicketSpec:
    key: str
    title: str
    path: str
    expected_assurance: str
    expected_batch: str
    acceptance: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RootCanaryRepositoryIdentity:
    full_name: str
    url: str

    def canonical(self) -> dict[str, str]:
        return {"full_name": self.full_name, "url": self.url}


@dataclass(frozen=True, slots=True)
class RootCanaryUserReadback:
    login: str

    def canonical(self) -> dict[str, str]:
        return {"login": self.login}


@dataclass(frozen=True, slots=True)
class RootCanaryLabelReadback:
    id: int
    node_id: str
    url: str
    name: str
    color: str
    default: bool
    description: str | None

    def canonical(self) -> dict[str, object]:
        return {
            "id": self.id,
            "node_id": self.node_id,
            "url": self.url,
            "name": self.name,
            "color": self.color,
            "default": self.default,
            "description": self.description,
        }


@dataclass(frozen=True, slots=True)
class RootCanaryCommentReadback:
    id: int
    node_id: str
    url: str
    html_url: str
    body: str
    user: RootCanaryUserReadback
    created_at: str
    updated_at: str
    author_association: str

    def canonical(self) -> dict[str, object]:
        return {
            "id": self.id,
            "node_id": self.node_id,
            "url": self.url,
            "html_url": self.html_url,
            "body": self.body,
            "user": self.user.canonical(),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "author_association": self.author_association,
        }


@dataclass(frozen=True, slots=True)
class RootCanaryBlockerReadback:
    id: int
    node_id: str
    number: int
    repository: RootCanaryRepositoryIdentity
    state: str
    url: str
    html_url: str
    updated_at: str

    def canonical(self) -> dict[str, object]:
        return {
            "id": self.id,
            "node_id": self.node_id,
            "number": self.number,
            "repository": self.repository.canonical(),
            "state": self.state,
            "url": self.url,
            "html_url": self.html_url,
            "updated_at": self.updated_at,
        }


@dataclass(frozen=True, slots=True)
class RootCanaryTicketReadback:
    id: int
    node_id: str
    number: int
    title: str
    repository: RootCanaryRepositoryIdentity
    state: str
    body: str
    url: str
    html_url: str
    updated_at: str
    labels: tuple[RootCanaryLabelReadback, ...]
    comments: tuple[RootCanaryCommentReadback, ...]
    blockers: tuple[RootCanaryBlockerReadback, ...]
    contract_digest: str

    @property
    def blocked_by(self) -> tuple[int, ...]:
        return tuple(blocker.number for blocker in self.blockers)

    @property
    def blocker_states(self) -> tuple[tuple[int, str], ...]:
        return tuple((blocker.number, blocker.state) for blocker in self.blockers)

    def canonical_without_digest(self) -> dict[str, object]:
        return {
            "id": self.id,
            "node_id": self.node_id,
            "number": self.number,
            "title": self.title,
            "repository": self.repository.canonical(),
            "state": self.state,
            "body": self.body,
            "url": self.url,
            "html_url": self.html_url,
            "updated_at": self.updated_at,
            "labels": [label.canonical() for label in self.labels],
            "comments": [comment.canonical() for comment in self.comments],
            "blockers": [blocker.canonical() for blocker in self.blockers],
        }

    def canonical(self) -> dict[str, object]:
        return {
            **self.canonical_without_digest(),
            "contract_digest": self.contract_digest,
        }


@dataclass(frozen=True, slots=True)
class RootCanaryManifestEntry:
    key: str
    ticket_key: str
    readback: RootCanaryTicketReadback

    def canonical(self) -> dict[str, object]:
        return {
            "key": self.key,
            "ticket_key": self.ticket_key,
            **self.readback.canonical(),
        }


class RootCanaryProvisionError(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class GitHubIssuePort(Protocol):
    def preflight_readback(self, repository: str) -> None: ...

    def find_exact_title(
        self, repository: str, title: str
    ) -> RootCanaryTicketReadback | None: ...

    def create_issue(
        self, repository: str, title: str, body: str, labels: tuple[str, ...]
    ) -> RootCanaryTicketReadback: ...

    def read_complete(self, repository: str, number: int) -> RootCanaryTicketReadback: ...


def root_ticket_specs() -> tuple[RootCanaryTicketSpec, ...]:
    return (
        RootCanaryTicketSpec(
            "alpha",
            "GWO V8 GA Canary A: document Candidate receipt readback",
            "docs/canary/gwo-v8-ga-alpha.md",
            "standard",
            "multi",
            ("Add the named document only.", "Repository validation passes."),
        ),
        RootCanaryTicketSpec(
            "beta",
            "GWO V8 GA Canary B: document permission binding readback",
            "docs/canary/gwo-v8-ga-beta.md",
            "standard",
            "multi",
            ("Add the named document only.", "Repository validation passes."),
        ),
        RootCanaryTicketSpec(
            "gamma",
            "GWO V8 GA Canary C: document restart reconstruction",
            "docs/canary/gwo-v8-ga-gamma.md",
            "standard",
            "multi",
            ("Add the named document only.", "Repository validation passes."),
        ),
        RootCanaryTicketSpec(
            "delta",
            "GWO V8 GA Canary D: update the protected GA marker",
            "docs/canary/protected/gwo-v8-ga-delta.md",
            "strict",
            "singleton",
            ("Add the protected marker only.", "Repository validation passes."),
        ),
    )


def canonical_body(spec: RootCanaryTicketSpec) -> str:
    acceptance = "\n".join(f"- [ ] {item}" for item in spec.acceptance)
    return (
        "## Outcome\n"
        f"Create `{spec.path}` as one GA root-Canary marker.\n\n"
        "## Acceptance criteria\n"
        f"{acceptance}\n\n"
        "## Scope exclusions\n"
        "- Do not edit another path.\n"
        "- Do not change dependencies, labels, authority, or release state.\n"
    )


def provision_root_tickets(
    github: GitHubIssuePort,
    repository: str,
    approval: str | None,
    *,
    read_only: bool = False,
) -> tuple[RootCanaryManifestEntry, ...]:
    if repository != ROOT_REPOSITORY or (not read_only and approval != APPROVAL):
        raise RootCanaryProvisionError("ROOT_CANARY_APPROVAL_REQUIRED")

    try:
        github.preflight_readback(repository)
    except RootCanaryProvisionError:
        raise
    except Exception as error:
        raise RootCanaryProvisionError("ROOT_TICKET_READBACK_UNAVAILABLE") from error

    result = []
    for spec in root_ticket_specs():
        body = canonical_body(spec)
        issue = github.find_exact_title(repository, spec.title)
        if issue is None:
            if read_only:
                raise RootCanaryProvisionError("ROOT_TICKET_MISSING")
            issue = github.create_issue(repository, spec.title, body, ("ready-for-agent",))

        readback = github.read_complete(repository, issue.number)
        _validate_readback(readback, repository, spec, body, issue.number)
        result.append(
            RootCanaryManifestEntry(
                key=spec.key,
                ticket_key=f"issue:{readback.number}",
                readback=readback,
            )
        )
    return tuple(result)


def canonical_json_bytes(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode(
        "utf-8"
    )


def digest_value(value: object) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def _flatten_paginated(value: object) -> tuple[dict[str, object], ...]:
    if (
        type(value) is not list
        or any(type(page) is not list for page in value)
        or any(
            type(item) is not dict
            for page in value
            for item in page
        )
    ):
        raise RootCanaryProvisionError("ROOT_TICKET_READBACK_INVALID")
    return tuple(item for page in value for item in page)


def _repository_identity(repository: str) -> RootCanaryRepositoryIdentity:
    return RootCanaryRepositoryIdentity(
        full_name=repository,
        url=f"https://api.github.com/repos/{repository}",
    )


def _issue_urls(repository: str, number: int) -> tuple[str, str]:
    api_url = f"https://api.github.com/repos/{repository}/issues/{number}"
    return api_url, f"https://github.com/{repository}/issues/{number}"


def _valid_text(value: object) -> bool:
    return type(value) is str and bool(value)


def _valid_digest(value: object) -> bool:
    return (
        type(value) is str
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _validate_readback(
    readback: object,
    repository: str,
    spec: RootCanaryTicketSpec,
    body: str,
    expected_number: int,
) -> None:
    if type(readback) is not RootCanaryTicketReadback:
        raise RootCanaryProvisionError("ROOT_TICKET_READBACK_INVALID")

    expected_repository = _repository_identity(repository)
    expected_url, expected_html_url = _issue_urls(repository, readback.number)
    if (
        type(readback.id) is not int
        or readback.id < 1
        or not _valid_text(readback.node_id)
        or type(readback.number) is not int
        or readback.number < 1
        or readback.number != expected_number
        or readback.title != spec.title
        or readback.repository != expected_repository
        or readback.state != "OPEN"
        or readback.body != body
        or readback.url != expected_url
        or readback.html_url != expected_html_url
        or not _valid_text(readback.updated_at)
        or tuple(label.name for label in readback.labels) != ("ready-for-agent",)
        or readback.blockers
        or not _valid_digest(readback.contract_digest)
    ):
        raise RootCanaryProvisionError("ROOT_TICKET_NOT_READY")

    try:
        complete_digest = digest_value(readback.canonical_without_digest())
    except (AttributeError, TypeError, ValueError):
        raise RootCanaryProvisionError("ROOT_TICKET_READBACK_INVALID") from None
    if complete_digest != readback.contract_digest:
        raise RootCanaryProvisionError("ROOT_TICKET_READBACK_INVALID")


@dataclass(frozen=True, slots=True)
class GhIssuePort:
    run_gh_json: Callable[[tuple[str, ...]], object]

    def _run_json(self, command: tuple[str, ...]) -> object:
        try:
            return self.run_gh_json(command)
        except RootCanaryProvisionError:
            raise
        except Exception as error:
            raise RootCanaryProvisionError("ROOT_TICKET_READBACK_UNAVAILABLE") from error

    def preflight_readback(self, repository: str) -> None:
        parts = repository.split("/")
        if len(parts) != 2 or any(not part for part in parts):
            raise RootCanaryProvisionError("ROOT_TICKET_READBACK_UNAVAILABLE")
        owner, name = parts
        raw = self._run_json(
            (
                "api",
                "graphql",
                "-f",
                f"query={_READBACK_PREFLIGHT_QUERY}",
                "-F",
                f"owner={owner}",
                "-F",
                f"name={name}",
            )
        )
        if (
            type(raw) is not dict
            or raw.get("errors")
            or type(raw.get("data")) is not dict
            or type(raw["data"].get("repository")) is not dict
            or type(raw["data"]["repository"].get("issues")) is not dict
            or type(raw["data"]["repository"]["issues"].get("nodes")) is not list
            or type(raw["data"]["repository"]["issues"].get("pageInfo")) is not dict
        ):
            raise RootCanaryProvisionError("ROOT_TICKET_READBACK_UNAVAILABLE")

    def find_exact_title(
        self, repository: str, title: str
    ) -> RootCanaryTicketReadback | None:
        rows = _flatten_paginated(
            self._run_json(
                (
                    "api",
                    "--paginate",
                    "--slurp",
                    f"repos/{repository}/issues?state=all&per_page=100",
                )
            )
        )
        matches = []
        for row in rows:
            if type(row.get("number")) is not int or type(row.get("title")) is not str:
                raise RootCanaryProvisionError("ROOT_TICKET_READBACK_INVALID")
            if "pull_request" not in row and row["title"] == title:
                matches.append(row)
        if len(matches) > 1:
            raise RootCanaryProvisionError("ROOT_TICKET_TITLE_DUPLICATE")
        return None if not matches else self.read_complete(repository, int(matches[0]["number"]))

    def create_issue(
        self, repository: str, title: str, body: str, labels: tuple[str, ...]
    ) -> RootCanaryTicketReadback:
        created = self._run_json(
            (
                "issue",
                "create",
                "--repo",
                repository,
                "--title",
                title,
                "--body",
                body,
                "--label",
                labels[0],
            )
        )
        if type(created) is not dict or type(created.get("number")) is not int:
            raise RootCanaryProvisionError("ROOT_TICKET_READBACK_INVALID")
        return self.read_complete(repository, created["number"])

    def read_complete(self, repository: str, number: int) -> RootCanaryTicketReadback:
        issue = self._run_json(("api", f"repos/{repository}/issues/{number}"))
        comments = _flatten_paginated(
            self._run_json(
                (
                    "api",
                    "--paginate",
                    "--slurp",
                    f"repos/{repository}/issues/{number}/comments?per_page=100",
                )
            )
        )
        blockers = _flatten_paginated(
            self._run_json(
                (
                    "api",
                    "--paginate",
                    "--slurp",
                    f"repos/{repository}/issues/{number}/dependencies/blocked_by?per_page=100",
                )
            )
        )
        return _readback_from_api(issue, comments, blockers, repository, number)


def _readback_from_api(
    issue: object,
    comments: tuple[dict[str, object], ...],
    blockers: tuple[dict[str, object], ...],
    repository: str,
    number: int,
) -> RootCanaryTicketReadback:
    if type(issue) is not dict:
        raise RootCanaryProvisionError("ROOT_TICKET_READBACK_INVALID")
    repository_identity = _repository_identity(repository)
    issue_url, issue_html_url = _issue_urls(repository, number)
    if (
        type(issue.get("id")) is not int
        or issue["id"] < 1
        or not _valid_text(issue.get("node_id"))
        or issue.get("number") != number
        or not _valid_text(issue.get("title"))
        or type(issue.get("body")) is not str
        or type(issue.get("state")) is not str
        or issue["state"].upper() not in {"OPEN", "CLOSED"}
        or issue.get("repository_url") != repository_identity.url
        or issue.get("url") != issue_url
        or issue.get("html_url") != issue_html_url
        or not _valid_text(issue.get("updated_at"))
        or type(issue.get("labels")) is not list
    ):
        raise RootCanaryProvisionError("ROOT_TICKET_READBACK_INVALID")

    labels = tuple(
        sorted(
            (_label_from_api(item) for item in issue["labels"]),
            key=lambda item: item.name,
        )
    )
    if len({label.id for label in labels}) != len(labels) or len({label.name for label in labels}) != len(labels):
        raise RootCanaryProvisionError("ROOT_TICKET_READBACK_INVALID")
    parsed_comments = tuple(
        sorted((_comment_from_api(item) for item in comments), key=lambda item: item.id)
    )
    if len({comment.id for comment in parsed_comments}) != len(parsed_comments):
        raise RootCanaryProvisionError("ROOT_TICKET_READBACK_INVALID")
    parsed_blockers = tuple(
        sorted(
            (_blocker_from_api(item, repository, repository_identity) for item in blockers),
            key=lambda item: item.number,
        )
    )
    if len({blocker.number for blocker in parsed_blockers}) != len(parsed_blockers):
        raise RootCanaryProvisionError("ROOT_TICKET_READBACK_INVALID")

    readback = RootCanaryTicketReadback(
        id=issue["id"],
        node_id=issue["node_id"],
        number=number,
        title=issue["title"],
        repository=repository_identity,
        state=issue["state"].upper(),
        body=issue["body"],
        url=issue_url,
        html_url=issue_html_url,
        updated_at=issue["updated_at"],
        labels=labels,
        comments=parsed_comments,
        blockers=parsed_blockers,
        contract_digest="0" * 64,
    )
    return replace(
        readback,
        contract_digest=digest_value(readback.canonical_without_digest()),
    )


def _label_from_api(value: object) -> RootCanaryLabelReadback:
    if type(value) is not dict:
        raise RootCanaryProvisionError("ROOT_TICKET_READBACK_INVALID")
    if (
        type(value.get("id")) is not int
        or value["id"] < 1
        or not _valid_text(value.get("node_id"))
        or not _valid_text(value.get("url"))
        or not _valid_text(value.get("name"))
        or type(value.get("color")) is not str
        or type(value.get("default")) is not bool
        or (
            value.get("description") is not None
            and type(value["description"]) is not str
        )
    ):
        raise RootCanaryProvisionError("ROOT_TICKET_READBACK_INVALID")
    return RootCanaryLabelReadback(
        id=value["id"],
        node_id=value["node_id"],
        url=value["url"],
        name=value["name"],
        color=value["color"],
        default=value["default"],
        description=value.get("description"),
    )


def _comment_from_api(value: dict[str, object]) -> RootCanaryCommentReadback:
    user = value.get("user")
    if (
        type(value.get("id")) is not int
        or value["id"] < 1
        or not _valid_text(value.get("node_id"))
        or not _valid_text(value.get("url"))
        or not _valid_text(value.get("html_url"))
        or type(value.get("body")) is not str
        or type(user) is not dict
        or not _valid_text(user.get("login"))
        or not _valid_text(value.get("created_at"))
        or not _valid_text(value.get("updated_at"))
        or not _valid_text(value.get("author_association"))
    ):
        raise RootCanaryProvisionError("ROOT_TICKET_READBACK_INVALID")
    return RootCanaryCommentReadback(
        id=value["id"],
        node_id=value["node_id"],
        url=value["url"],
        html_url=value["html_url"],
        body=value["body"],
        user=RootCanaryUserReadback(user["login"]),
        created_at=value["created_at"],
        updated_at=value["updated_at"],
        author_association=value["author_association"],
    )


def _blocker_from_api(
    value: dict[str, object],
    repository: str,
    repository_identity: RootCanaryRepositoryIdentity,
) -> RootCanaryBlockerReadback:
    number = value.get("number")
    expected_url, expected_html_url = (
        _issue_urls(repository, number) if type(number) is int else ("", "")
    )
    if (
        type(value.get("id")) is not int
        or value["id"] < 1
        or not _valid_text(value.get("node_id"))
        or type(number) is not int
        or number < 1
        or type(value.get("state")) is not str
        or value["state"].upper() not in {"OPEN", "CLOSED"}
        or value.get("repository_url") != repository_identity.url
        or value.get("url") != expected_url
        or value.get("html_url") != expected_html_url
        or not _valid_text(value.get("updated_at"))
        or "pull_request" in value
    ):
        raise RootCanaryProvisionError("ROOT_TICKET_READBACK_INVALID")
    return RootCanaryBlockerReadback(
        id=value["id"],
        node_id=value["node_id"],
        number=number,
        repository=repository_identity,
        state=value["state"].upper(),
        url=expected_url,
        html_url=expected_html_url,
        updated_at=value["updated_at"],
    )


def write_ticket_manifest(
    path: Path, entries: tuple[RootCanaryManifestEntry, ...]
) -> None:
    refs = [
        f"github://{ROOT_REPOSITORY}/issues/{entry.readback.number}"
        for entry in entries
    ]
    payload = {
        "schema": "gwo-v8-root-canary-tickets.v1",
        "repository": ROOT_REPOSITORY,
        "ready_refs": refs,
        "tickets": [entry.canonical() for entry in entries],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_json_bytes(payload))


def write_ticket_runbook(path: Path) -> None:
    sections = [
        "# GWO V8 Root Canary Ticket Contract",
        "",
        f"Repository: `{ROOT_REPOSITORY}`",
        "",
        f"Approval: `{APPROVAL}`",
        "",
    ]
    for spec in root_ticket_specs():
        sections.extend(
            [
                f"## {spec.key}: {spec.title}",
                "",
                f"- Path: `{spec.path}`",
                f"- Assurance: `{spec.expected_assurance}`",
                f"- Batch: `{spec.expected_batch}`",
                "",
                canonical_body(spec),
                "",
            ]
        )
    sections.append(
        "Readback command: `py -3.13 scripts/provision_v8_root_canary.py "
        "--repository NOirBRight/github-work-orchestrator --read-only "
        "--output tickets-readback.json`."
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(sections) + "\n", encoding="utf-8")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", required=True)
    parser.add_argument("--approval")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--read-only", action="store_true")
    args = parser.parse_args(argv)

    def run_gh_json(command: tuple[str, ...]) -> object:
        return json.loads(subprocess.check_output(("gh", *command), text=True))

    github = GhIssuePort(run_gh_json=run_gh_json)
    approval = None if args.read_only else args.approval
    entries = provision_root_tickets(
        github, args.repository, approval, read_only=args.read_only
    )
    write_ticket_manifest(args.output, entries)
    if not args.read_only:
        write_ticket_runbook(Path("docs/e2e/gwo-v8-root-canary-tickets.md"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
