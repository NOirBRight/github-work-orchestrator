from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
from contextlib import nullcontext
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Callable, Protocol, Sequence
from urllib.parse import quote, urlsplit


ROOT_REPOSITORY = "NOirBRight/github-work-orchestrator"
APPROVAL = "CREATE-GWO-V8-GA-ROOT-CANARY-TICKETS"
POLICY_WITNESS_PATH = ".gwo-v8/policy-witness.json"
DEFAULT_LOCK_PATH = Path(".gwo-v8-root-canary-tickets.lock")
_CREATE_OUTPUT_RE = re.compile(r"^[1-9][0-9]*$")


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
    state_reason: str | None = None
    type: dict[str, object] | None = None

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
            "state_reason": self.state_reason,
            "type": None if self.type is None else dict(self.type),
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
        return _snapshot_ticket(self.ticket_key, self.readback)


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
    lock_path: Path | None = None,
) -> tuple[RootCanaryManifestEntry, ...]:
    if repository != ROOT_REPOSITORY or (not read_only and approval != APPROVAL):
        raise RootCanaryProvisionError("ROOT_CANARY_APPROVAL_REQUIRED")

    lock = nullcontext() if read_only else _provision_lock(lock_path)
    with lock:
        _safe_github_call(github.preflight_readback, repository)

        specs = root_ticket_specs()
        staged: list[tuple[RootCanaryTicketSpec, RootCanaryTicketReadback | None]] = []
        missing: list[str] = []

        # Resolve and validate the whole existing frontier before any create.
        # A partially existing root must never be completed after a later
        # existing contract has already proved invalid.
        for spec in specs:
            issue = _safe_github_call(
                github.find_exact_title,
                repository,
                spec.title,
            )
            if issue is None:
                missing.append(spec.key)
            else:
                issue_number = _readback_number(issue)
                _safe_validate_readback(
                    issue,
                    repository,
                    spec,
                    canonical_body(spec),
                    issue_number,
                )
            staged.append((spec, issue))

        if read_only and missing:
            raise RootCanaryProvisionError("ROOT_TICKET_MISSING")

        result: list[RootCanaryManifestEntry] = []
        for spec, issue in staged:
            body = canonical_body(spec)
            if issue is None:
                created = _safe_github_call(
                    github.create_issue,
                    repository,
                    spec.title,
                    body,
                    ("ready-for-agent",),
                )
                created_number = _readback_number(created)
                # The create response is only a transport receipt.  Always
                # use the authoritative issue readback for the contract.
                issue = _safe_github_call(
                    github.read_complete,
                    repository,
                    created_number,
                )
            issue_number = _readback_number(issue)
            _safe_validate_readback(issue, repository, spec, body, issue_number)
            result.append(
                RootCanaryManifestEntry(
                    key=spec.key,
                    ticket_key=f"issue:{issue_number}",
                    readback=issue,
                )
            )

        # Read every exact title again after all creates.  The output is
        # published only when all four numbers and complete readbacks are the
        # same at both sides of the mutation boundary.
        barrier: list[RootCanaryManifestEntry] = []
        for spec, expected in zip(specs, result):
            observed = _safe_github_call(
                github.find_exact_title,
                repository,
                spec.title,
            )
            if observed is None:
                raise RootCanaryProvisionError("ROOT_TICKET_CONCURRENT_CHANGE")
            _safe_validate_readback(
                observed,
                repository,
                spec,
                canonical_body(spec),
                expected.readback.number,
            )
            if observed != expected.readback:
                raise RootCanaryProvisionError("ROOT_TICKET_CONCURRENT_CHANGE")
            barrier.append(
                RootCanaryManifestEntry(
                    key=spec.key,
                    ticket_key=f"issue:{observed.number}",
                    readback=observed,
                )
            )

        return tuple(barrier)


def _safe_github_call(call: Callable[..., object], *args: object) -> object:
    try:
        return call(*args)
    except RootCanaryProvisionError:
        raise
    except Exception as error:
        raise RootCanaryProvisionError("ROOT_TICKET_READBACK_UNAVAILABLE") from error


def _readback_number(value: object) -> int:
    if type(value) is not RootCanaryTicketReadback:
        raise RootCanaryProvisionError("ROOT_TICKET_READBACK_INVALID")
    return value.number


class _ProvisionLock:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._descriptor: int | None = None

    def __enter__(self) -> "_ProvisionLock":
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._descriptor = os.open(
                self.path,
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
            )
            os.write(
                self._descriptor,
                f"gwo-v8-root-canary-lock\npid={os.getpid()}\n".encode("ascii"),
            )
        except FileExistsError as error:
            raise RootCanaryProvisionError("ROOT_CANARY_LOCK_UNAVAILABLE") from error
        except OSError as error:
            descriptor = self._descriptor
            self._descriptor = None
            if descriptor is not None:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
            raise RootCanaryProvisionError("ROOT_CANARY_LOCK_UNAVAILABLE") from error
        return self

    def __exit__(self, *_args: object) -> None:
        descriptor = self._descriptor
        self._descriptor = None
        if descriptor is not None:
            try:
                os.close(descriptor)
            finally:
                try:
                    self.path.unlink()
                except FileNotFoundError:
                    pass
                except OSError:
                    # A lock that cannot be removed is deliberately left as a
                    # durable fail-closed marker for the next invocation.
                    pass


def _provision_lock(path: Path | None) -> _ProvisionLock:
    return _ProvisionLock(DEFAULT_LOCK_PATH if path is None else path)


def canonical_json_bytes(value: object) -> bytes:
    return _canonical_json_value_bytes(value) + b"\n"


def _canonical_json_value_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def digest_value(value: object) -> str:
    return hashlib.sha256(_canonical_json_value_bytes(value)).hexdigest()


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
        or (
            readback.state_reason is not None
            and type(readback.state_reason) is not str
        )
        or (
            readback.type is not None
            and type(readback.type) is not dict
        )
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


def _safe_validate_readback(
    readback: object,
    repository: str,
    spec: RootCanaryTicketSpec,
    body: str,
    expected_number: int,
) -> None:
    try:
        _validate_readback(readback, repository, spec, body, expected_number)
    except RootCanaryProvisionError:
        raise
    except Exception as error:
        raise RootCanaryProvisionError("ROOT_TICKET_READBACK_INVALID") from error


@dataclass(frozen=True, slots=True)
class GhIssuePort:
    run_gh_json: Callable[[tuple[str, ...]], object]
    run_gh_text: Callable[[tuple[str, ...]], str] | None = None
    _preflight_cache: dict[str, RootCanaryTicketReadback | None] = field(
        default_factory=dict,
        init=False,
        repr=False,
        compare=False,
    )

    def _run_json(self, command: tuple[str, ...]) -> object:
        try:
            return self.run_gh_json(command)
        except RootCanaryProvisionError:
            raise
        except Exception as error:
            raise RootCanaryProvisionError("ROOT_TICKET_READBACK_UNAVAILABLE") from error

    def _run_text(self, command: tuple[str, ...]) -> str:
        if self.run_gh_text is None:
            value = self._run_json(command)
            if type(value) is not str:
                raise RootCanaryProvisionError("ROOT_TICKET_READBACK_INVALID")
            return value
        try:
            value = self.run_gh_text(command)
        except RootCanaryProvisionError:
            raise
        except Exception as error:
            raise RootCanaryProvisionError("ROOT_TICKET_READBACK_UNAVAILABLE") from error
        if type(value) is not str:
            raise RootCanaryProvisionError("ROOT_TICKET_READBACK_INVALID")
        return value

    def _issue_rows(self, repository: str) -> tuple[dict[str, object], ...]:
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
        seen_numbers: set[int] = set()
        for row in rows:
            number = row.get("number")
            title = row.get("title")
            if (
                type(number) is not int
                or number < 1
                or type(title) is not str
                or not title
                or number in seen_numbers
            ):
                raise RootCanaryProvisionError("ROOT_TICKET_READBACK_INVALID")
            seen_numbers.add(number)
        return rows

    def preflight_readback(self, repository: str) -> None:
        rows = self._issue_rows(repository)
        specs_by_title = {spec.title: spec for spec in root_ticket_specs()}
        matches: dict[str, dict[str, object]] = {}
        for row in rows:
            if "pull_request" in row or row["title"] not in specs_by_title:
                continue
            title = str(row["title"])
            if title in matches:
                raise RootCanaryProvisionError("ROOT_TICKET_TITLE_DUPLICATE")
            matches[title] = row

        self._preflight_cache.clear()
        for spec in root_ticket_specs():
            row = matches.get(spec.title)
            if row is None:
                self._preflight_cache[spec.title] = None
                continue
            readback = self.read_complete(repository, int(row["number"]))
            _safe_validate_readback(
                readback,
                repository,
                spec,
                canonical_body(spec),
                int(row["number"]),
            )
            self._preflight_cache[spec.title] = readback

    def find_exact_title(
        self, repository: str, title: str
    ) -> RootCanaryTicketReadback | None:
        if title in self._preflight_cache:
            return self._preflight_cache.pop(title)
        rows = self._issue_rows(repository)
        matches = []
        for row in rows:
            if "pull_request" not in row and row["title"] == title:
                matches.append(row)
        if len(matches) > 1:
            raise RootCanaryProvisionError("ROOT_TICKET_TITLE_DUPLICATE")
        return None if not matches else self.read_complete(repository, int(matches[0]["number"]))

    def create_issue(
        self, repository: str, title: str, body: str, labels: tuple[str, ...]
    ) -> RootCanaryTicketReadback:
        output = self._run_text(
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
        number = _parse_issue_create_output(output, repository)
        return self.read_complete(repository, number)

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


def _parse_issue_create_output(value: object, repository: str) -> int:
    if type(value) is not str:
        raise RootCanaryProvisionError("ROOT_TICKET_READBACK_INVALID")
    output = value.strip()
    parsed = urlsplit(output)
    expected_prefix = f"/{repository}/issues/"
    if (
        parsed.scheme != "https"
        or parsed.netloc != "github.com"
        or parsed.query
        or parsed.fragment
        or not parsed.path.startswith(expected_prefix)
    ):
        raise RootCanaryProvisionError("ROOT_TICKET_READBACK_INVALID")
    suffix = parsed.path.removeprefix(expected_prefix)
    if _CREATE_OUTPUT_RE.fullmatch(suffix) is None:
        raise RootCanaryProvisionError("ROOT_TICKET_READBACK_INVALID")
    return int(suffix)


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
    state_reason = issue.get("state_reason")
    issue_type = issue.get("type")
    if (
        type(issue.get("id")) is not int
        or issue["id"] < 1
        or not _valid_text(issue.get("node_id"))
        or issue.get("number") != number
        or not _valid_text(issue.get("title"))
        or type(issue.get("body")) is not str
        or type(issue.get("state")) is not str
        or issue["state"].upper() not in {"OPEN", "CLOSED"}
        or (state_reason is not None and type(state_reason) is not str)
        or (issue_type is not None and type(issue_type) is not dict)
        or issue.get("repository_url") != repository_identity.url
        or issue.get("url") != issue_url
        or issue.get("html_url") != issue_html_url
        or not _valid_text(issue.get("updated_at"))
        or type(issue.get("labels")) is not list
    ):
        raise RootCanaryProvisionError("ROOT_TICKET_READBACK_INVALID")

    labels = tuple(
        sorted(
            (_label_from_api(item, repository_identity) for item in issue["labels"]),
            key=lambda item: item.name,
        )
    )
    if len({label.id for label in labels}) != len(labels) or len({label.name for label in labels}) != len(labels):
        raise RootCanaryProvisionError("ROOT_TICKET_READBACK_INVALID")
    parsed_comments = tuple(
        sorted(
            (
                _comment_from_api(item, repository, number)
                for item in comments
            ),
            key=lambda item: item.id,
        )
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
        state_reason=state_reason,
        type=None if issue_type is None else dict(issue_type),
    )
    try:
        contract_digest = digest_value(readback.canonical_without_digest())
    except (TypeError, ValueError, UnicodeError):
        raise RootCanaryProvisionError("ROOT_TICKET_READBACK_INVALID") from None
    return replace(readback, contract_digest=contract_digest)


def _label_from_api(
    value: object,
    repository_identity: RootCanaryRepositoryIdentity,
) -> RootCanaryLabelReadback:
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
        or value.get("url")
        != f"{repository_identity.url}/labels/{quote(value.get('name', ''), safe='')}"
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


def _comment_from_api(
    value: dict[str, object],
    repository: str,
    issue_number: int,
) -> RootCanaryCommentReadback:
    user = value.get("user")
    comment_id = value.get("id")
    expected_url = (
        f"https://api.github.com/repos/{repository}/issues/comments/{comment_id}"
        if type(comment_id) is int
        else ""
    )
    expected_html_url = (
        f"https://github.com/{repository}/issues/{issue_number}"
        f"#issuecomment-{comment_id}"
        if type(comment_id) is int
        else ""
    )
    if (
        type(comment_id) is not int
        or comment_id < 1
        or not _valid_text(value.get("node_id"))
        or value.get("url") != expected_url
        or value.get("html_url") != expected_html_url
        or type(value.get("body")) is not str
        or type(user) is not dict
        or not _valid_text(user.get("login"))
        or not _valid_text(value.get("created_at"))
        or not _valid_text(value.get("updated_at"))
        or not _valid_text(value.get("author_association"))
    ):
        raise RootCanaryProvisionError("ROOT_TICKET_READBACK_INVALID")
    return RootCanaryCommentReadback(
        id=comment_id,
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


def _snapshot_blocker(blocker: RootCanaryBlockerReadback) -> dict[str, object]:
    key = f"issue:{blocker.number}"
    contract = {
        "key": key,
        "state": blocker.state.lower(),
        "repository": blocker.repository.canonical(),
    }
    return {
        **contract,
        "source": {
            "ref": key,
            "digest": digest_value(contract),
        },
    }


def _snapshot_ticket(
    ticket_key: str,
    readback: RootCanaryTicketReadback,
) -> dict[str, object]:
    if ticket_key != f"issue:{readback.number}":
        raise RootCanaryProvisionError("ROOT_TICKET_MANIFEST_INVALID")
    labels = [label.name for label in readback.labels]
    contract = {
        "id": readback.id,
        "node_id": readback.node_id,
        "number": readback.number,
        "title": readback.title,
        "body": readback.body,
        "state": readback.state.lower(),
        "state_reason": readback.state_reason,
        "type": None if readback.type is None else dict(readback.type),
        "repository": readback.repository.canonical(),
        "labels": [label.canonical() for label in readback.labels],
        "comments": [comment.canonical() for comment in readback.comments],
        "updated_at": readback.updated_at,
    }
    blockers = sorted(
        (_snapshot_blocker(blocker) for blocker in readback.blockers),
        key=lambda item: str(item["key"]),
    )
    projection = {
        "number": readback.number,
        "contract": contract,
        "labels": labels,
        "source_ref": ticket_key,
        "native_blockers": blockers,
    }
    return {
        "key": ticket_key,
        "labels": labels,
        "source": {
            "ref": ticket_key,
            "digest": digest_value(projection),
        },
        "contract": contract,
        "native_blockers": blockers,
    }


def write_ticket_manifest(
    path: Path, entries: tuple[RootCanaryManifestEntry, ...]
) -> None:
    specs = root_ticket_specs()
    if type(entries) is not tuple or len(entries) != len(specs):
        raise RootCanaryProvisionError("ROOT_TICKET_MANIFEST_INVALID")
    numbers: set[int] = set()
    for spec, entry in zip(specs, entries):
        if (
            type(entry) is not RootCanaryManifestEntry
            or entry.key != spec.key
            or type(entry.ticket_key) is not str
            or not entry.ticket_key.startswith("issue:")
            or type(entry.readback) is not RootCanaryTicketReadback
        ):
            raise RootCanaryProvisionError("ROOT_TICKET_MANIFEST_INVALID")
        number = _readback_number(entry.readback)
        if entry.ticket_key != f"issue:{number}" or number in numbers:
            raise RootCanaryProvisionError("ROOT_TICKET_MANIFEST_INVALID")
        numbers.add(number)
        try:
            _safe_validate_readback(
                entry.readback,
                ROOT_REPOSITORY,
                spec,
                canonical_body(spec),
                number,
            )
            _snapshot_ticket(entry.ticket_key, entry.readback)
        except RootCanaryProvisionError as error:
            if error.code == "ROOT_TICKET_MANIFEST_INVALID":
                raise
            raise RootCanaryProvisionError("ROOT_TICKET_MANIFEST_INVALID") from error
        except (TypeError, ValueError, UnicodeError) as error:
            raise RootCanaryProvisionError("ROOT_TICKET_MANIFEST_INVALID") from error

    refs = [entry.ticket_key for entry in entries]
    payload = {
        "schema": "gwo-v8-root-canary-tickets.v2",
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
        f"Policy Witness: `{POLICY_WITNESS_PATH}`",
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
    parser.add_argument("--lock", type=Path)
    parser.add_argument("--read-only", action="store_true")
    args = parser.parse_args(argv)

    def run_gh_json(command: tuple[str, ...]) -> object:
        return json.loads(subprocess.check_output(("gh", *command), text=True))

    def run_gh_text(command: tuple[str, ...]) -> str:
        return subprocess.check_output(("gh", *command), text=True)

    github = GhIssuePort(run_gh_json=run_gh_json, run_gh_text=run_gh_text)
    approval = None if args.read_only else args.approval
    entries = provision_root_tickets(
        github,
        args.repository,
        approval,
        read_only=args.read_only,
        lock_path=args.lock,
    )
    write_ticket_manifest(args.output, entries)
    if not args.read_only:
        write_ticket_runbook(Path("docs/e2e/gwo-v8-root-canary-tickets.md"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
