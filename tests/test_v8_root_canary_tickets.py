import json
from copy import deepcopy
from dataclasses import dataclass, field, replace
from pathlib import Path
import sys

import pytest

import scripts.provision_v8_root_canary as provision_module
from scripts.provision_v8_root_canary import (
    APPROVAL,
    GhIssuePort,
    POLICY_WITNESS_PATH,
    ROOT_REPOSITORY,
    RootCanaryBlockerReadback,
    RootCanaryProvisionError,
    RootCanaryLabelReadback,
    RootCanaryRepositoryIdentity,
    RootCanaryTicketReadback,
    canonical_body,
    canonical_json_bytes,
    digest_value,
    load_ticket_manifest,
    provision_root_tickets,
    root_ticket_specs,
    write_ticket_manifest,
    write_ticket_runbook,
)


def test_gh_cli_json_readback_decodes_utf8_bytes(monkeypatch):
    calls = []

    def fake_check_output(command):
        calls.append(command)
        return '[{"title":"Café"}]'.encode("utf-8")

    monkeypatch.setattr(provision_module.subprocess, "check_output", fake_check_output)

    assert provision_module._run_gh_json(("api", "issues")) == [{"title": "Café"}]
    assert calls == [("gh", "api", "issues")]


ROOT = Path(__file__).parents[1]
SCRIPTS = ROOT / "skills" / "orchestrator" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


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
    find_calls: list[str] = field(default_factory=list)
    read_complete_calls: list[int] = field(default_factory=list)
    preflight_error: Exception | None = None
    preflight_calls: int = 0
    read_complete_fail_after: int | None = None
    _next_number: int = 101

    def preflight_readback(self, _repository):
        self.preflight_calls += 1
        if self.preflight_error is not None:
            raise self.preflight_error

    def find_exact_title(self, _repository, title):
        self.find_calls.append(title)
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
        self.existing[title] = self.readbacks[number]
        return self.readbacks[number]

    def read_complete(self, _repository, number):
        self.read_complete_calls.append(number)
        if (
            self.read_complete_fail_after is not None
            and len(self.read_complete_calls) > self.read_complete_fail_after
        ):
            raise RuntimeError("synthetic readback failure")
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
    snapshot_source = (
        ROOT / "skills" / "orchestrator" / "scripts" / "gwo_v8" / "github_snapshot.py"
    ).read_text("utf-8")
    assert POLICY_WITNESS_PATH == ".gwo-v8/policy-witness.json"
    assert f'policy_path: str = "{POLICY_WITNESS_PATH}"' in snapshot_source


def test_checked_in_policy_witness_is_canonical_and_plancontrol_accepts_its_digest():
    from gwo_v8._canonical import digest_value as canonical_digest, load_canonical_json
    from gwo_v8.plan_control import _normalize_policy

    witness_path = ROOT / POLICY_WITNESS_PATH
    witness_bytes = witness_path.read_bytes()
    witness = load_canonical_json(witness_bytes)

    assert set(witness) == {
        "schema_version",
        "ref",
        "digest",
        "authority_grants",
        "allowed_capabilities",
        "exclusive_resources",
    }
    assert witness["schema_version"] == 1
    assert witness["ref"] == "policy:gwo-v8-root-canary"
    assert witness["authority_grants"] == {
        "campaign": [
            {
                "operation_id": "repository.read.v1",
                "resource_id": "campaign.snapshot.v1",
            }
        ],
        "worker": [
            {
                "operation_id": "workspace.write.v1",
                "resource_id": "work-run.workspace.v1",
            }
        ],
        "recovery_worker": [
            {
                "operation_id": "workspace.write.v1",
                "resource_id": "work-run.workspace.v1",
            }
        ],
        "review": [
            {
                "operation_id": "repository.read.v1",
                "resource_id": "review.subject.v1",
            }
        ],
    }
    assert witness["allowed_capabilities"] == ["git", "local_check"]
    assert witness["exclusive_resources"] == ["repository.target.v1"]
    assert witness["digest"] == canonical_digest(
        {key: value for key, value in witness.items() if key != "digest"}
    )
    assert _normalize_policy(witness) == witness


def test_github_snapshot_reads_the_checked_in_policy_witness_bytes_exactly():
    from gwo_v8.activation import GitHubContent
    from gwo_v8.github_snapshot import GitHubReadySnapshotSource
    from gwo_v8._canonical import load_canonical_json

    witness_path = ROOT / POLICY_WITNESS_PATH
    witness_bytes = witness_path.read_bytes()
    witness = load_canonical_json(witness_bytes)
    calls = []

    class ContentClient:
        def read(self, repository, branch, path):
            calls.append((repository, branch, path))
            return GitHubContent(witness_bytes, "blob:policy-witness")

    source = GitHubReadySnapshotSource(
        content_client=ContentClient(),
        issue_client=object(),
        control_branch="gwo-control",
        target_branch="main",
    )

    assert source._policy(ROOT_REPOSITORY) == (witness, witness_bytes)
    assert calls == [(ROOT_REPOSITORY, "gwo-control", POLICY_WITNESS_PATH)]


@pytest.mark.parametrize("content", [None, b"{}"])
def test_policy_witness_readback_rejects_missing_or_invalid_content(content):
    from gwo_v8.activation import GitHubContent
    from gwo_v8.github_snapshot import GitHubReadySnapshotSource
    from gwo_v8.plan_control import PlanControlError, _normalize_policy

    class ContentClient:
        def read(self, _repository, _branch, _path):
            return None if content is None else GitHubContent(content, "blob:policy")

    source = GitHubReadySnapshotSource(
        content_client=ContentClient(),
        issue_client=object(),
        control_branch="gwo-control",
        target_branch="main",
    )

    with pytest.raises(PlanControlError) as error:
        policy, _ = source._policy(ROOT_REPOSITORY)
        _normalize_policy(policy)
    assert error.value.code == "POLICY_WITNESS_INVALID"


def test_plancontrol_rejects_a_tampered_digest_from_the_checked_in_witness():
    from gwo_v8._canonical import load_canonical_json
    from gwo_v8.plan_control import PlanControlError, _normalize_policy

    witness = load_canonical_json((ROOT / POLICY_WITNESS_PATH).read_bytes())
    witness["ref"] = "policy:tampered"

    with pytest.raises(PlanControlError) as error:
        _normalize_policy(witness)
    assert error.value.code == "POLICY_WITNESS_INVALID"


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
    assert f"Policy Witness: `{POLICY_WITNESS_PATH}`" in text
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
    assert payload["schema"] == "gwo-v8-root-canary-tickets.v2"
    assert payload["repository"] == ROOT_REPOSITORY
    assert payload["ready_refs"] == [
        f"issue:{number}"
        for number in (101, 102, 103, 104)
    ]
    assert [item["key"] for item in payload["tickets"]] == [
        "issue:101",
        "issue:102",
        "issue:103",
        "issue:104",
    ]
    assert all(
        set(item) == {"key", "labels", "source", "contract", "native_blockers"}
        for item in payload["tickets"]
    )
    assert all(
        set(item["contract"]) == {
            "id",
            "node_id",
            "number",
            "title",
            "body",
            "state",
            "state_reason",
            "type",
            "repository",
            "labels",
            "comments",
            "updated_at",
        }
        for item in payload["tickets"]
    )
    assert all(item["contract"]["state"] == "open" for item in payload["tickets"])
    assert first.read_bytes() == canonical_json_bytes(payload)


def test_manifest_writer_requires_exact_fixed_order_and_four_unique_issue_numbers(
    fake_github, approved_token, tmp_path
):
    entries = provision_root_tickets(fake_github, ROOT_REPOSITORY, approved_token)

    with pytest.raises(RootCanaryProvisionError, match="ROOT_TICKET_MANIFEST_INVALID"):
        write_ticket_manifest(tmp_path / "too-few.json", entries[:3])

    with pytest.raises(RootCanaryProvisionError, match="ROOT_TICKET_MANIFEST_INVALID"):
        write_ticket_manifest(tmp_path / "reordered.json", (entries[1], entries[0], *entries[2:]))

    duplicate = replace(
        entries[1],
        readback=replace(entries[1].readback, number=entries[0].readback.number),
    )
    with pytest.raises(RootCanaryProvisionError, match="ROOT_TICKET_MANIFEST_INVALID"):
        write_ticket_manifest(tmp_path / "duplicate.json", (entries[0], duplicate, *entries[2:]))


def test_manifest_ticket_digest_matches_current_snapshot_projection(
    fake_github, approved_token, tmp_path
):
    entries = provision_root_tickets(fake_github, ROOT_REPOSITORY, approved_token)
    path = tmp_path / "tickets.json"
    write_ticket_manifest(path, entries)
    payload = json.loads(path.read_text("utf-8"))

    for item in payload["tickets"]:
        projection = {
            "number": item["contract"]["number"],
            "contract": item["contract"],
            "labels": item["labels"],
            "source_ref": item["key"],
            "native_blockers": item["native_blockers"],
        }
        assert item["source"] == {
            "ref": item["key"],
            "digest": digest_value(projection),
        }


def test_manifest_tickets_are_consumable_by_current_plancontrol_snapshot_parser(
    fake_github, approved_token, tmp_path
):
    import sys

    scripts = ROOT / "skills" / "orchestrator" / "scripts"
    if str(scripts) not in sys.path:
        sys.path.insert(0, str(scripts))
    from gwo_v8.plan_control import _normalize_ticket

    entries = provision_root_tickets(fake_github, ROOT_REPOSITORY, approved_token)
    path = tmp_path / "tickets.json"
    write_ticket_manifest(path, entries)
    payload = json.loads(path.read_text("utf-8"))

    assert [
        _normalize_ticket(item, repository=ROOT_REPOSITORY)
        for item in payload["tickets"]
    ] == payload["tickets"]


REAL_TICKET_MANIFEST = (
    ROOT / "tests" / "fixtures" / "gwo-v8-root-canary-tickets-195-198.json"
)


def _real_ticket_manifest_payload():
    return json.loads(REAL_TICKET_MANIFEST.read_text("utf-8"))


def _write_manifest_payload(path, payload):
    path.write_bytes(canonical_json_bytes(payload))


def test_real_manifest_loader_accepts_authoritative_four_ticket_fixture():
    manifest = load_ticket_manifest(
        REAL_TICKET_MANIFEST,
        require_real_root_numbers=True,
    )

    assert manifest["schema"] == "gwo-v8-root-canary-tickets.v2"
    assert manifest["repository"] == ROOT_REPOSITORY
    assert manifest["ready_refs"] == [
        "issue:195",
        "issue:196",
        "issue:197",
        "issue:198",
    ]
    assert [ticket["key"] for ticket in manifest["tickets"]] == manifest[
        "ready_refs"
    ]

    manifest["tickets"][0]["contract"]["title"] = "detached"
    assert _real_ticket_manifest_payload()["tickets"][0]["contract"]["title"] != (
        "detached"
    )


@pytest.mark.parametrize(
    ("name", "mutate"),
    [
        (
            "missing",
            lambda payload: payload["ready_refs"].pop(),
        ),
        (
            "duplicate",
            lambda payload: payload["ready_refs"].__setitem__(1, "issue:195"),
        ),
        (
            "reordered",
            lambda payload: payload.__setitem__(
                "ready_refs",
                ["issue:196", "issue:195", "issue:197", "issue:198"],
            ),
        ),
    ],
)
def test_manifest_loader_rejects_invalid_real_ticket_order(tmp_path, name, mutate):
    payload = deepcopy(_real_ticket_manifest_payload())
    mutate(payload)
    path = tmp_path / f"{name}.json"
    _write_manifest_payload(path, payload)

    with pytest.raises(
        RootCanaryProvisionError,
        match="ROOT_TICKET_REAL_ISSUES_REQUIRED",
    ):
        load_ticket_manifest(path, require_real_root_numbers=True)


def test_manifest_loader_rejects_tampered_source_digest(tmp_path):
    payload = deepcopy(_real_ticket_manifest_payload())
    payload["tickets"][0]["source"]["digest"] = "0" * 64
    path = tmp_path / "tampered.json"
    _write_manifest_payload(path, payload)

    with pytest.raises(
        RootCanaryProvisionError,
        match="TICKET_CONTRACT_DIGEST_MISMATCH",
    ):
        load_ticket_manifest(path, require_real_root_numbers=True)


def test_manifest_loader_rejects_repository_mismatch(tmp_path):
    payload = deepcopy(_real_ticket_manifest_payload())
    payload["repository"] = "other/repository"
    path = tmp_path / "foreign.json"
    _write_manifest_payload(path, payload)

    with pytest.raises(
        RootCanaryProvisionError,
        match="ROOT_REPOSITORY_MISMATCH",
    ):
        load_ticket_manifest(path, require_real_root_numbers=True)


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


def test_github_port_preflight_uses_paginated_rest_issue_readback():
    commands = []

    def run_gh_json(command):
        commands.append(command)
        return [[]]

    GhIssuePort(run_gh_json=run_gh_json).preflight_readback(ROOT_REPOSITORY)

    assert len(commands) == 1
    assert commands == [
        (
            "api",
            "--paginate",
            "--slurp",
            f"repos/{ROOT_REPOSITORY}/issues?state=all&per_page=100",
        )
    ]


def test_github_port_never_sends_unsupported_graphql_blocked_by_query():
    commands = []

    def run_gh_json(command):
        commands.append(command)
        return [[]]

    GhIssuePort(run_gh_json=run_gh_json).preflight_readback(ROOT_REPOSITORY)

    assert all(command[0:2] != ("api", "graphql") for command in commands)
    assert all("blockedBy" not in " ".join(command) for command in commands)


def test_github_port_parses_the_actual_issue_create_url_output_safely():
    commands = []
    repository_url = f"https://api.github.com/repos/{ROOT_REPOSITORY}"

    def run_gh_json(command):
        commands.append(command)
        if command[-1] == f"repos/{ROOT_REPOSITORY}/issues/321":
            return {
                "id": 321,
                "node_id": "issue-node-321",
                "number": 321,
                "title": root_ticket_specs()[0].title,
                "state": "open",
                "body": canonical_body(root_ticket_specs()[0]),
                "repository_url": repository_url,
                "url": f"{repository_url}/issues/321",
                "html_url": f"https://github.com/{ROOT_REPOSITORY}/issues/321",
                "updated_at": "2026-08-17T00:00:00Z",
                "labels": [],
            }
        if command[-1].endswith("/issues/321/comments?per_page=100"):
            return [[]]
        if command[-1].endswith("/issues/321/dependencies/blocked_by?per_page=100"):
            return [[]]
        raise AssertionError(command)

    def run_gh_text(command):
        commands.append(command)
        return "https://github.com/NOirBRight/github-work-orchestrator/issues/321\n"

    port = GhIssuePort(run_gh_json=run_gh_json, run_gh_text=run_gh_text)

    result = port.create_issue(
        ROOT_REPOSITORY,
        root_ticket_specs()[0].title,
        canonical_body(root_ticket_specs()[0]),
        ("ready-for-agent",),
    )

    assert result.number == 321
    assert commands[0] == (
        "issue",
        "create",
        "--repo",
        ROOT_REPOSITORY,
        "--title",
        root_ticket_specs()[0].title,
        "--body",
        canonical_body(root_ticket_specs()[0]),
        "--label",
        "ready-for-agent",
    )


@pytest.mark.parametrize(
    "output",
    [
        "https://github.com/other/repository/issues/321\n",
        "https://github.com/NOirBRight/github-work-orchestrator/issues/0\n",
        "created https://github.com/NOirBRight/github-work-orchestrator/issues/321\n",
    ],
)
def test_github_port_rejects_unsafe_issue_create_output(output):
    port = GhIssuePort(run_gh_json=lambda _command: output)
    with pytest.raises(RootCanaryProvisionError, match="ROOT_TICKET_READBACK_INVALID"):
        port.create_issue(
            ROOT_REPOSITORY,
            root_ticket_specs()[0].title,
            canonical_body(root_ticket_specs()[0]),
            ("ready-for-agent",),
        )


def test_provision_preflights_and_validates_all_existing_tickets_before_creating(
    fake_github, approved_token, tmp_path
):
    wrong_spec = root_ticket_specs()[1]
    wrong = _readback(
        701,
        wrong_spec,
        body=canonical_body(wrong_spec).replace(
            wrong_spec.path, "docs/canary/not-beta.md"
        ),
    )
    fake_github.existing[wrong_spec.title] = wrong
    fake_github.readbacks[wrong.number] = wrong

    with pytest.raises(RootCanaryProvisionError, match="ROOT_TICKET_NOT_READY"):
        provision_root_tickets(
            fake_github,
            ROOT_REPOSITORY,
            approved_token,
            lock_path=tmp_path / "root.lock",
        )

    assert fake_github.create_calls == []


def test_provision_fails_closed_when_readback_breaks_after_a_partial_create(
    fake_github, approved_token, tmp_path
):
    fake_github.read_complete_fail_after = 0

    with pytest.raises(
        RootCanaryProvisionError, match="ROOT_TICKET_READBACK_UNAVAILABLE"
    ):
        provision_root_tickets(
            fake_github,
            ROOT_REPOSITORY,
            approved_token,
            lock_path=tmp_path / "root.lock",
        )

    assert len(fake_github.create_calls) == 1


def test_provision_uses_a_durable_lock_and_releases_it_after_success(
    fake_github, approved_token, tmp_path
):
    lock_path = tmp_path / "root.lock"
    entries = provision_root_tickets(
        fake_github,
        ROOT_REPOSITORY,
        approved_token,
        lock_path=lock_path,
    )
    assert len(entries) == 4
    assert not lock_path.exists()

    lock_path.write_text("held\n", encoding="utf-8")
    with pytest.raises(RootCanaryProvisionError, match="ROOT_CANARY_LOCK_UNAVAILABLE"):
        provision_root_tickets(
            fake_github,
            ROOT_REPOSITORY,
            approved_token,
            lock_path=lock_path,
        )


def test_final_four_ticket_consistency_barrier_reads_back_every_ticket_again(
    fake_github, approved_token, tmp_path
):
    provision_root_tickets(
        fake_github,
        ROOT_REPOSITORY,
        approved_token,
        lock_path=tmp_path / "root.lock",
    )
    assert len(fake_github.find_calls) == 8


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
                "state_reason": "reopened",
                "type": {"id": "type-1", "name": "Task"},
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
    assert readback.state_reason == "reopened"
    assert readback.type == {"id": "type-1", "name": "Task"}
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


def _identity_test_issue(number=77, *, labels=()):
    repository_url = f"https://api.github.com/repos/{ROOT_REPOSITORY}"
    return {
        "id": number,
        "node_id": f"issue-node-{number}",
        "number": number,
        "title": "contract",
        "state": "open",
        "state_reason": None,
        "type": None,
        "body": "body",
        "repository_url": repository_url,
        "url": f"{repository_url}/issues/{number}",
        "html_url": f"https://github.com/{ROOT_REPOSITORY}/issues/{number}",
        "updated_at": "2026-08-17T00:00:00Z",
        "labels": list(labels),
    }


def test_github_port_rejects_foreign_label_identity():
    label = {
        "id": 1,
        "node_id": "label-node-1",
        "url": "https://api.github.com/repos/other/repository/labels/ready-for-agent",
        "name": "ready-for-agent",
        "color": "1f883d",
        "default": False,
        "description": None,
    }

    def run_gh_json(command):
        if command[-1] == f"repos/{ROOT_REPOSITORY}/issues/77":
            return _identity_test_issue(labels=(label,))
        if command[-1].endswith("/issues/77/comments?per_page=100"):
            return [[]]
        if command[-1].endswith("/issues/77/dependencies/blocked_by?per_page=100"):
            return [[]]
        raise AssertionError(command)

    with pytest.raises(RootCanaryProvisionError, match="ROOT_TICKET_READBACK_INVALID"):
        GhIssuePort(run_gh_json=run_gh_json).read_complete(ROOT_REPOSITORY, 77)


def test_github_port_rejects_foreign_comment_issue_identity():
    def run_gh_json(command):
        if command[-1] == f"repos/{ROOT_REPOSITORY}/issues/77":
            return _identity_test_issue()
        if command[-1].endswith("/issues/77/comments?per_page=100"):
            return [
                [
                    {
                        "id": 2,
                        "node_id": "comment-node-2",
                        "url": "https://api.github.com/repos/other/repository/issues/comments/2",
                        "html_url": f"https://github.com/{ROOT_REPOSITORY}/issues/77#issuecomment-2",
                        "body": "comment",
                        "user": {"login": "reviewer"},
                        "created_at": "2026-08-17T00:00:02Z",
                        "updated_at": "2026-08-17T00:00:02Z",
                        "author_association": "MEMBER",
                    }
                ]
            ]
        if command[-1].endswith("/issues/77/dependencies/blocked_by?per_page=100"):
            return [[]]
        raise AssertionError(command)

    with pytest.raises(RootCanaryProvisionError, match="ROOT_TICKET_READBACK_INVALID"):
        GhIssuePort(run_gh_json=run_gh_json).read_complete(ROOT_REPOSITORY, 77)


def test_github_port_rejects_foreign_blocker_issue_identity():
    def run_gh_json(command):
        if command[-1] == f"repos/{ROOT_REPOSITORY}/issues/77":
            return _identity_test_issue()
        if command[-1].endswith("/issues/77/comments?per_page=100"):
            return [[]]
        if command[-1].endswith("/issues/77/dependencies/blocked_by?per_page=100"):
            return [
                [
                    {
                        "id": 12,
                        "node_id": "blocker-node-12",
                        "number": 12,
                        "state": "open",
                        "repository_url": "https://api.github.com/repos/other/repository",
                        "url": "https://api.github.com/repos/other/repository/issues/12",
                        "html_url": "https://github.com/other/repository/issues/12",
                        "updated_at": "2026-08-17T00:00:12Z",
                    }
                ]
            ]
        raise AssertionError(command)

    with pytest.raises(RootCanaryProvisionError, match="ROOT_TICKET_READBACK_INVALID"):
        GhIssuePort(run_gh_json=run_gh_json).read_complete(ROOT_REPOSITORY, 77)
