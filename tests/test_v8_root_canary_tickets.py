import json
from dataclasses import dataclass, field, replace
from pathlib import Path

import pytest

from scripts.provision_v8_root_canary import (
    APPROVAL,
    GhIssuePort,
    ROOT_REPOSITORY,
    RootCanaryBlockerReadback,
    RootCanaryProvisionError,
    RootCanaryLabelReadback,
    RootCanaryRepositoryIdentity,
    RootCanaryTicketReadback,
    canonical_body,
    canonical_json_bytes,
    digest_value,
    provision_root_tickets,
    root_ticket_specs,
    write_ticket_manifest,
    write_ticket_runbook,
)


ROOT = Path(__file__).parents[1]


def _repository_identity():
    return RootCanaryRepositoryIdentity(
        full_name=ROOT_REPOSITORY,
        url=f"https://api.github.com/repos/{ROOT_REPOSITORY}",
    )


def _issue_urls(number):
    return (
        f"https://api.github.com/repos/{ROOT_REPOSITORY}/issues/{number}",
        f"https://github.com/{ROOT_REPOSITORY}/issues/{number}",
    )


def _blocker(number, state):
    url, html_url = _issue_urls(number)
    return RootCanaryBlockerReadback(
        id=10000 + number,
        node_id=f"blocker-node-{number}",
        number=number,
        repository=_repository_identity(),
        state=state,
        url=url,
        html_url=html_url,
        updated_at="2026-08-17T00:00:00Z",
    )


def _readback(number, spec, *, body=None, blockers=(), contract_digest=None):
    url, html_url = _issue_urls(number)
    readback = RootCanaryTicketReadback(
        id=20000 + number,
        node_id=f"issue-node-{number}",
        number=number,
        title=spec.title,
        repository=_repository_identity(),
        state="OPEN",
        body=canonical_body(spec) if body is None else body,
        url=url,
        html_url=html_url,
        updated_at="2026-08-17T00:00:00Z",
        labels=(
            RootCanaryLabelReadback(
                id=30000 + number,
                node_id=f"label-node-{number}",
                url=f"{_repository_identity().url}/labels/ready-for-agent",
                name="ready-for-agent",
                color="1f883d",
                default=False,
                description=None,
            ),
        ),
        comments=(),
        blockers=tuple(blockers),
        contract_digest="0" * 64,
    )
    return replace(
        readback,
        contract_digest=(
            digest_value(readback.canonical_without_digest())
            if contract_digest is None
            else contract_digest
        ),
    )


@dataclass
class FakeGithub:
    next_readback: object | None = None
    existing: dict[str, object] = field(default_factory=dict)
    readbacks: dict[int, object] = field(default_factory=dict)
    create_calls: list[tuple[str, str, str, tuple[str, ...]]] = field(default_factory=list)
    preflight_error: Exception | None = None
    preflight_calls: int = 0
    _next_number: int = 101

    def preflight_readback(self, _repository):
        self.preflight_calls += 1
        if self.preflight_error is not None:
            raise self.preflight_error

    def find_exact_title(self, _repository, title):
        return self.existing.get(title)

    def create_issue(self, repository, title, body, labels):
        number = self._next_number
        self._next_number += 1
        self.create_calls.append((repository, title, body, labels))
        spec = next(spec for spec in root_ticket_specs() if spec.title == title)
        self.readbacks[number] = _readback(
            number,
            spec,
            body=body,
        )
        return self.readbacks[number]

    def read_complete(self, _repository, number):
        if self.next_readback is not None:
            return self.next_readback
        return self.readbacks[number]


@pytest.fixture
def fake_github():
    return FakeGithub()


@pytest.fixture
def approved_token():
    return APPROVAL


def fake_blocked_ticket():
    return _readback(
        101,
        root_ticket_specs()[0],
        blockers=(_blocker(999, "OPEN"),),
    )


def test_root_ticket_specs_are_disjoint_and_derive_three_standard_one_strict():
    specs = root_ticket_specs()
    assert [item.key for item in specs] == ["alpha", "beta", "gamma", "delta"]
    assert len({item.path for item in specs}) == 4
    assert [item.expected_assurance for item in specs] == [
        "standard",
        "standard",
        "standard",
        "strict",
    ]
    assert [item.expected_batch for item in specs] == [
        "multi",
        "multi",
        "multi",
        "singleton",
    ]
    policy = json.loads((ROOT / ".gwo" / "policy.json").read_text("utf-8"))
    assert policy["assurance"]["strict_path_prefixes"] == [
        "docs/canary/protected/"
    ]


def test_provision_refuses_issue_mutation_without_named_owner_approval(fake_github):
    with pytest.raises(RootCanaryProvisionError, match="ROOT_CANARY_APPROVAL_REQUIRED"):
        provision_root_tickets(
            github=fake_github,
            repository=ROOT_REPOSITORY,
            approval=None,
        )
    assert fake_github.create_calls == []


def test_provision_refuses_wrong_repository_before_issue_mutation(fake_github, approved_token):
    with pytest.raises(RootCanaryProvisionError, match="ROOT_CANARY_APPROVAL_REQUIRED"):
        provision_root_tickets(fake_github, "NOirBRight/other-repository", approved_token)
    assert fake_github.create_calls == []


def test_provision_fails_closed_before_creation_when_readback_preflight_is_unavailable(
    fake_github, approved_token
):
    fake_github.preflight_error = RootCanaryProvisionError(
        "ROOT_TICKET_READBACK_UNAVAILABLE"
    )

    with pytest.raises(
        RootCanaryProvisionError, match="ROOT_TICKET_READBACK_UNAVAILABLE"
    ):
        provision_root_tickets(fake_github, ROOT_REPOSITORY, approved_token)

    assert fake_github.preflight_calls == 1
    assert fake_github.create_calls == []


def test_provision_creates_four_exact_contracts_after_approval(fake_github, approved_token):
    entries = provision_root_tickets(fake_github, ROOT_REPOSITORY, approved_token)

    assert [entry.key for entry in entries] == ["alpha", "beta", "gamma", "delta"]
    assert [entry.readback.number for entry in entries] == [101, 102, 103, 104]
    assert [call[0] for call in fake_github.create_calls] == [ROOT_REPOSITORY] * 4
    assert [call[3] for call in fake_github.create_calls] == [
        ("ready-for-agent",)
    ] * 4
    assert [call[2] for call in fake_github.create_calls] == [
        canonical_body(spec) for spec in root_ticket_specs()
    ]


def test_readback_rejects_non_ready_or_blocked_ticket(fake_github, approved_token):
    fake_github.next_readback = fake_blocked_ticket()
    with pytest.raises(RootCanaryProvisionError, match="ROOT_TICKET_NOT_READY"):
        provision_root_tickets(fake_github, ROOT_REPOSITORY, approved_token)


def test_existing_ticket_with_wrong_contract_fails_closed_without_creation(
    fake_github, approved_token
):
    spec = root_ticket_specs()[0]
    wrong = _readback(
        701,
        spec,
        body=canonical_body(spec).replace(spec.path, "docs/canary/not-alpha.md"),
    )
    fake_github.existing[spec.title] = wrong
    fake_github.readbacks[wrong.number] = wrong

    with pytest.raises(RootCanaryProvisionError, match="ROOT_TICKET_NOT_READY"):
        provision_root_tickets(fake_github, ROOT_REPOSITORY, approved_token)
    assert fake_github.create_calls == []


def test_provision_recomputes_the_readback_digest_instead_of_trusting_caller_digest(
    fake_github, approved_token
):
    spec = root_ticket_specs()[0]
    tampered = _readback(
        701,
        spec,
        contract_digest="f" * 64,
    )
    fake_github.existing[spec.title] = tampered
    fake_github.readbacks[tampered.number] = tampered

    with pytest.raises(
        RootCanaryProvisionError, match="ROOT_TICKET_READBACK_INVALID"
    ):
        provision_root_tickets(fake_github, ROOT_REPOSITORY, approved_token)

    assert fake_github.create_calls == []


def test_read_only_provisioning_never_creates_and_requires_complete_existing_readback(
    fake_github,
):
    with pytest.raises(RootCanaryProvisionError, match="ROOT_TICKET_MISSING"):
        provision_root_tickets(fake_github, ROOT_REPOSITORY, None, read_only=True)
    assert fake_github.create_calls == []


def test_runbook_contains_all_four_fixed_contracts(tmp_path):
    path = tmp_path / "tickets.md"
    write_ticket_runbook(path)
    text = path.read_text("utf-8")
    assert "GWO V8 GA Canary A: document Candidate receipt readback" in text
    assert "docs/canary/protected/gwo-v8-ga-delta.md" in text
    assert "CREATE-GWO-V8-GA-ROOT-CANARY-TICKETS" in text
    assert "github://" not in text


def test_manifest_is_deterministic_and_contains_all_authoritative_readbacks(
    fake_github, approved_token, tmp_path
):
    entries = provision_root_tickets(fake_github, ROOT_REPOSITORY, approved_token)
    first = tmp_path / "one" / "tickets-readback.json"
    second = tmp_path / "two" / "tickets-readback.json"

    write_ticket_manifest(first, entries)
    write_ticket_manifest(second, entries)

    assert first.read_bytes() == second.read_bytes()
    payload = json.loads(first.read_text("utf-8"))
    assert payload["schema"] == "gwo-v8-root-canary-tickets.v1"
    assert payload["repository"] == ROOT_REPOSITORY
    assert payload["ready_refs"] == [
        f"github://{ROOT_REPOSITORY}/issues/{number}"
        for number in (101, 102, 103, 104)
    ]
    assert [item["ticket_key"] for item in payload["tickets"]] == [
        "issue:101",
        "issue:102",
        "issue:103",
        "issue:104",
    ]
    assert first.read_bytes() == canonical_json_bytes(payload)


def test_github_port_rejects_duplicate_exact_titles():
    port = GhIssuePort(
        run_gh_json=lambda _command: [
            [
                {"number": 1, "title": "duplicate"},
                {"number": 2, "title": "duplicate"},
            ]
        ]
    )
    with pytest.raises(RootCanaryProvisionError, match="ROOT_TICKET_TITLE_DUPLICATE"):
        port.find_exact_title(ROOT_REPOSITORY, "duplicate")


def test_github_port_paginates_title_lookup_before_deciding_no_duplicate():
    commands = []

    def run_gh_json(command):
        commands.append(command)
        return [
            [{"number": 1, "title": "other"}],
            [{"number": 2, "title": "duplicate"}],
            [{"number": 3, "title": "duplicate"}],
        ]

    port = GhIssuePort(run_gh_json=run_gh_json)
    with pytest.raises(RootCanaryProvisionError, match="ROOT_TICKET_TITLE_DUPLICATE"):
        port.find_exact_title(ROOT_REPOSITORY, "duplicate")

    assert commands == [
        (
            "api",
            "--paginate",
            "--slurp",
            f"repos/{ROOT_REPOSITORY}/issues?state=all&per_page=100",
        )
    ]


def test_github_port_preflight_uses_graphql_complete_readback_capability():
    commands = []

    def run_gh_json(command):
        commands.append(command)
        return {
            "data": {
                "repository": {
                    "issues": {
                        "nodes": [],
                        "pageInfo": {"hasNextPage": False, "endCursor": None},
                    }
                }
            }
        }

    GhIssuePort(run_gh_json=run_gh_json).preflight_readback(ROOT_REPOSITORY)

    assert len(commands) == 1
    assert commands[0][0:2] == ("api", "graphql")
    assert "blockedBy" in commands[0][3]
    assert "comments" in commands[0][3]
    assert "labels" in commands[0][3]


def test_github_port_readback_includes_complete_comments_and_blocker_pages():
    commands = []
    repository_url = f"https://api.github.com/repos/{ROOT_REPOSITORY}"

    def run_gh_json(command):
        commands.append(command)
        if command[-1] == f"repos/{ROOT_REPOSITORY}/issues?state=all&per_page=100":
            return [[{"number": 77, "title": "contract"}]]
        if command[-1] == f"repos/{ROOT_REPOSITORY}/issues/77":
            return {
                "id": 77,
                "node_id": "issue-node-77",
                "number": 77,
                "title": "contract",
                "state": "open",
                "body": "body",
                "repository_url": repository_url,
                "url": f"{repository_url}/issues/77",
                "html_url": f"https://github.com/{ROOT_REPOSITORY}/issues/77",
                "updated_at": "2026-08-17T00:00:00Z",
                "labels": [
                    {
                        "id": 1,
                        "node_id": "label-node-1",
                        "url": f"{repository_url}/labels/ready-for-agent",
                        "name": "ready-for-agent",
                        "color": "1f883d",
                        "default": False,
                        "description": None,
                    }
                ],
            }
        if command[-1].endswith("/issues/77/comments?per_page=100"):
            return [
                [
                    {
                        "id": 2,
                        "node_id": "comment-node-2",
                        "url": f"{repository_url}/issues/comments/2",
                        "html_url": f"https://github.com/{ROOT_REPOSITORY}/issues/77#issuecomment-2",
                        "body": "second",
                        "user": {"login": "reviewer"},
                        "created_at": "2026-08-17T00:00:02Z",
                        "updated_at": "2026-08-17T00:00:02Z",
                        "author_association": "MEMBER",
                    }
                ],
                [
                    {
                        "id": 1,
                        "node_id": "comment-node-1",
                        "url": f"{repository_url}/issues/comments/1",
                        "html_url": f"https://github.com/{ROOT_REPOSITORY}/issues/77#issuecomment-1",
                        "body": "first",
                        "user": {"login": "author"},
                        "created_at": "2026-08-17T00:00:01Z",
                        "updated_at": "2026-08-17T00:00:01Z",
                        "author_association": "OWNER",
                    }
                ],
            ]
        if command[-1].endswith("/issues/77/dependencies/blocked_by?per_page=100"):
            return [
                [
                    {
                        "id": 12,
                        "node_id": "blocker-node-12",
                        "number": 12,
                        "state": "open",
                        "repository_url": repository_url,
                        "url": f"{repository_url}/issues/12",
                        "html_url": f"https://github.com/{ROOT_REPOSITORY}/issues/12",
                        "updated_at": "2026-08-17T00:00:12Z",
                    }
                ],
                [
                    {
                        "id": 11,
                        "node_id": "blocker-node-11",
                        "number": 11,
                        "state": "closed",
                        "repository_url": repository_url,
                        "url": f"{repository_url}/issues/11",
                        "html_url": f"https://github.com/{ROOT_REPOSITORY}/issues/11",
                        "updated_at": "2026-08-17T00:00:11Z",
                    }
                ],
            ]
        raise AssertionError(command)

    port = GhIssuePort(run_gh_json=run_gh_json)
    readback = port.find_exact_title(ROOT_REPOSITORY, "contract")

    assert readback is not None
    assert [comment.body for comment in readback.comments] == ["first", "second"]
    assert readback.blocked_by == (11, 12)
    assert readback.blocker_states == ((11, "CLOSED"), (12, "OPEN"))
    assert readback.contract_digest == digest_value(readback.canonical_without_digest())
    assert commands == [
        (
            "api",
            "--paginate",
            "--slurp",
            f"repos/{ROOT_REPOSITORY}/issues?state=all&per_page=100",
        ),
        ("api", f"repos/{ROOT_REPOSITORY}/issues/77"),
        (
            "api",
            "--paginate",
            "--slurp",
            f"repos/{ROOT_REPOSITORY}/issues/77/comments?per_page=100",
        ),
        (
            "api",
            "--paginate",
            "--slurp",
            f"repos/{ROOT_REPOSITORY}/issues/77/dependencies/blocked_by?per_page=100",
        ),
    ]
