import json
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.provision_v8_root_canary import (
    APPROVAL,
    GhIssuePort,
    ROOT_REPOSITORY,
    RootCanaryProvisionError,
    RootCanaryTicketReadback,
    canonical_body,
    canonical_json_bytes,
    provision_root_tickets,
    root_ticket_specs,
    write_ticket_manifest,
    write_ticket_runbook,
)


ROOT = Path(__file__).parents[1]


@dataclass
class FakeGithub:
    next_readback: object | None = None
    existing: dict[str, object] = field(default_factory=dict)
    readbacks: dict[int, object] = field(default_factory=dict)
    create_calls: list[tuple[str, str, str, tuple[str, ...]]] = field(default_factory=list)
    _next_number: int = 101

    def find_exact_title(self, _repository, title):
        return self.existing.get(title)

    def create_issue(self, repository, title, body, labels):
        number = self._next_number
        self._next_number += 1
        self.create_calls.append((repository, title, body, labels))
        self.readbacks[number] = RootCanaryTicketReadback(
            number=number,
            state="OPEN",
            labels=labels,
            body=body,
            comments=(),
            blocked_by=(),
            blocker_states=(),
            contract_digest=f"digest-{number}",
        )
        return SimpleNamespace(number=number)

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
    return SimpleNamespace(
        number=101,
        state="OPEN",
        labels=("ready-for-agent",),
        body="wrong body",
        comments=(),
        blocked_by=(999,),
        blocker_states=((999, "OPEN"),),
        contract_digest="digest",
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
    wrong = SimpleNamespace(
        number=701,
        state="OPEN",
        labels=("ready-for-agent",),
        body=canonical_body(spec).replace(spec.path, "docs/canary/not-alpha.md"),
        comments=(),
        blocked_by=(),
        blocker_states=(),
        contract_digest="wrong",
    )
    fake_github.existing[spec.title] = wrong
    fake_github.readbacks[wrong.number] = wrong

    with pytest.raises(RootCanaryProvisionError, match="ROOT_TICKET_NOT_READY"):
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
            {"number": 1, "title": "duplicate"},
            {"number": 2, "title": "duplicate"},
        ]
    )
    with pytest.raises(RootCanaryProvisionError, match="ROOT_TICKET_TITLE_DUPLICATE"):
        port.find_exact_title(ROOT_REPOSITORY, "duplicate")


def test_github_port_readback_includes_complete_comments_and_blocker_pages():
    commands = []

    def run_gh_json(command):
        commands.append(command)
        if command[1] == "list":
            return [{"number": 77, "title": "contract"}]
        return {
            "number": 77,
            "state": "OPEN",
            "body": "body",
            "labels": [{"name": "ready-for-agent"}],
            "comments": [{"body": "first"}, {"body": "second"}],
            "blockedBy": [
                {"number": 12, "state": "OPEN"},
                {"number": 11, "state": "CLOSED"},
            ],
        }

    port = GhIssuePort(run_gh_json=run_gh_json)
    readback = port.find_exact_title(ROOT_REPOSITORY, "contract")

    assert readback is not None
    assert readback.comments == ("first", "second")
    assert readback.blocked_by == (12, 11)
    assert readback.blocker_states == ((11, "CLOSED"), (12, "OPEN"))
    assert commands[0][0:4] == ("issue", "list", "--repo", ROOT_REPOSITORY)
    assert commands[1][-1] == "number,state,body,labels,comments,blockedBy"
