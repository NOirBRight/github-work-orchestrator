from __future__ import annotations

import sys
import json
import subprocess
from pathlib import Path

import pytest


SCRIPTS = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "orchestrator"
    / "scripts"
)
sys.path.insert(0, str(SCRIPTS))


def _policy():
    from gwo_v8._canonical import digest_value

    value = {
        "schema_version": 1,
        "ref": "policy:one",
        "authority_grants": {
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
        },
        "allowed_capabilities": ["git", "local_check"],
        "exclusive_resources": ["repository.target.v1"],
    }
    value["digest"] = digest_value(value)
    return value


class _ContentClient:
    def __init__(self):
        from gwo_v8._canonical import canonical_bytes
        from gwo_v8.activation import GitHubContent

        self.policy = GitHubContent(
            canonical_bytes(_policy()),
            "blob:policy",
        )

    def read(self, repository, branch, path):
        assert repository == "owner/repository"
        assert branch == "gwo-control"
        assert path == ".gwo-v8/policy-witness.json"
        return self.policy


class _IssueClient:
    def __init__(
        self,
        *,
        body: str,
        blockers=(),
        comments=(),
        state="open",
        labels=("ready-for-agent",),
        pull_request=False,
        repository_url="https://api.github.com/repos/owner/repository",
    ):
        self.body = body
        self.blockers = None if blockers is None else tuple(blockers)
        self.comments = tuple(comments)
        self.state = state
        self.labels = tuple(labels)
        self.pull_request = pull_request
        self.repository_url = repository_url

    def read_issue(self, repository, number):
        value = {
            "number": number,
            "title": f"Contract {number}",
            "body": self.body,
            "state": self.state,
            "state_reason": None,
            "type": None,
            "repository_url": self.repository_url,
            "html_url": (
                f"https://github.com/owner/repository/issues/{number}"
            ),
            "url": (
                f"https://api.github.com/repos/owner/repository/issues/{number}"
            ),
            "labels": [
                {
                    "id": index,
                    "node_id": f"LABEL_{index}",
                    "name": name,
                    "color": "0052cc",
                    "description": f"{name} description",
                    "url": (
                        "https://api.github.com/repos/owner/repository/"
                        f"labels/{name}"
                    ),
                }
                for index, name in enumerate(self.labels, start=1)
            ],
        }
        if self.pull_request:
            value["pull_request"] = {
                "url": (
                    "https://api.github.com/repos/owner/repository/"
                    f"pulls/{number}"
                )
            }
        return value

    def read_comments(self, repository, number):
        return self.comments

    def read_blockers(self, repository, number):
        return self.blockers

    def read_branch_oid(self, repository, branch):
        return "a" * 40


def _blocker(number: int, *, repository="owner/repository"):
    return {
        "number": number,
        "state": "open",
        "repository_url": f"https://api.github.com/repos/{repository}",
        "html_url": f"https://github.com/{repository}/issues/{number}",
        "url": f"https://api.github.com/repos/{repository}/issues/{number}",
    }


def _comment(number: int):
    return {
        "id": number,
        "node_id": f"COMMENT_{number}",
        "url": (
            "https://api.github.com/repos/owner/repository/"
            f"issues/comments/{number}"
        ),
        "html_url": (
            "https://github.com/owner/repository/issues/109"
            f"#issuecomment-{number}"
        ),
        "body": f"Comment {number}",
        "user": {"login": f"user-{number}"},
        "created_at": f"2026-07-{number:02d}T00:00:00Z",
        "updated_at": f"2026-07-{number:02d}T00:00:00Z",
        "author_association": "CONTRIBUTOR",
    }


def _source(issue_client):
    from gwo_v8.github_snapshot import GitHubReadySnapshotSource

    return GitHubReadySnapshotSource(
        content_client=_ContentClient(),
        issue_client=issue_client,
        control_branch="gwo-control",
        target_branch="main",
    )


def test_production_snapshot_freezes_all_comment_and_blocker_pages():
    blockers = tuple(_blocker(number) for number in range(200, 231))
    body = (
        "Complete contract\n\n## Blocked by\n\n"
        + "\n".join(f"- #{number}" for number in range(200, 231))
    )
    comments = (_comment(1), _comment(2), _comment(3))

    snapshot = _source(
        _IssueClient(
            body=body,
            blockers=blockers,
            comments=comments,
        )
    ).snapshot("owner/repository", ("#109",))
    ticket = snapshot["tickets"][0]

    assert ticket["contract"] == {
        "title": "Contract 109",
        "body": body,
        "state": "open",
        "state_reason": None,
        "type": None,
        "repository": {
            "full_name": "owner/repository",
            "url": "https://api.github.com/repos/owner/repository",
        },
        "labels": [
            {
                "id": 1,
                "node_id": "LABEL_1",
                "name": "ready-for-agent",
                "color": "0052cc",
                "description": "ready-for-agent description",
                "url": (
                    "https://api.github.com/repos/owner/repository/"
                    "labels/ready-for-agent"
                ),
            }
        ],
        "comments": list(comments),
    }
    assert len(ticket["native_blockers"]) == 31
    assert ticket["native_blockers"][0]["key"] == "issue:200"
    assert ticket["native_blockers"][-1]["key"] == "issue:230"
    assert snapshot["campaign_source"]["ref"] == "refs/heads/main"


def test_cli_issue_client_flattens_every_comment_and_blocker_page(
    monkeypatch,
):
    from gwo_v8.github_snapshot import GitHubCliIssueReadClient

    responses = iter(
        (
            [[_comment(1), _comment(2)], [_comment(3)]],
            [[_blocker(200)], [_blocker(201), _blocker(202)]],
        )
    )
    observed = []

    def run(arguments):
        observed.append(arguments)
        return subprocess.CompletedProcess(
            arguments,
            0,
            stdout=json.dumps(next(responses)),
            stderr="",
        )

    client = GitHubCliIssueReadClient()
    monkeypatch.setattr(client, "_run", run)

    assert [item["id"] for item in client.read_comments(
        "owner/repository",
        109,
    )] == [1, 2, 3]
    assert [item["number"] for item in client.read_blockers(
        "owner/repository",
        109,
    )] == [200, 201, 202]
    assert all(arguments[1:3] == ["--paginate", "--slurp"] for arguments in observed)


@pytest.mark.parametrize(
    ("issue_options", "expected_code"),
    (
        ({"state": "closed"}, "TICKET_LABEL_INVALID"),
        ({"labels": ("ready-for-agent", "needs-info")}, "TICKET_LABEL_INVALID"),
        ({"pull_request": True}, "GITHUB_SNAPSHOT_INVALID"),
    ),
)
def test_production_snapshot_rejects_non_frontier_issue(
    issue_options,
    expected_code,
):
    from gwo_v8.plan_control import PlanControlError

    with pytest.raises(PlanControlError) as rejected:
        _source(_IssueClient(body="Contract", **issue_options)).snapshot(
            "owner/repository",
            ("issue:109",),
        )

    assert rejected.value.code == expected_code


def test_production_snapshot_rejects_foreign_native_blocker():
    from gwo_v8.plan_control import PlanControlError

    issue = _IssueClient(
        body="Contract\n\n## Blocked by\n\n- #200",
        blockers=(_blocker(200, repository="other/repository"),),
    )

    with pytest.raises(PlanControlError) as rejected:
        _source(issue).snapshot("owner/repository", ("issue:109",))

    assert rejected.value.code == "GITHUB_SNAPSHOT_INVALID"


@pytest.mark.parametrize(
    ("body", "blockers", "expected_code"),
    (
        (
            "Contract",
            (_blocker(200),),
            "GITHUB_BLOCKERS_OMITTED",
        ),
        (
            "Contract\n\n## Blocked by\n\n- #201",
            (_blocker(200),),
            "GITHUB_BLOCKERS_CONFLICT",
        ),
        (
            "Contract\n\n## Blocked by\n\n- #200",
            (),
            "GITHUB_BLOCKERS_CONFLICT",
        ),
        (
            "Contract",
            None,
            "GITHUB_BLOCKERS_OMITTED",
        ),
    ),
)
def test_production_snapshot_fails_closed_on_blocker_omission_or_conflict(
    body,
    blockers,
    expected_code,
):
    from gwo_v8.plan_control import PlanControlError

    with pytest.raises(PlanControlError) as rejected:
        _source(_IssueClient(body=body, blockers=blockers)).snapshot(
            "owner/repository",
            ("issue:109",),
        )

    assert rejected.value.code == expected_code


def test_production_snapshot_uses_body_fallback_when_native_is_unavailable():
    issue = _IssueClient(
        body=(
            "Contract\n\n## Blocked by\n\n"
            "- #200\n"
            "- issue:201\n"
            "- https://github.com/owner/repository/issues/202"
        ),
        blockers=None,
    )

    ticket = _source(issue).snapshot(
        "owner/repository",
        ("issue:109",),
    )["tickets"][0]

    assert [
        blocker["key"] for blocker in ticket["native_blockers"]
    ] == ["issue:200", "issue:201", "issue:202"]
    assert all(
        blocker["repository"]["full_name"] == "owner/repository"
        for blocker in ticket["native_blockers"]
    )


def test_production_source_canonicalizes_all_local_issue_spellings():
    source = _source(_IssueClient(body="Contract"))

    assert source.canonical_ready_refs(
        "owner/repository",
        (
            "#109",
            "issue:110",
            "https://github.com/owner/repository/issues/111",
        ),
    ) == ("issue:109", "issue:110", "issue:111")


@pytest.mark.parametrize(
    "ready_ref",
    (
        "#0",
        "109",
        "issue:unknown",
        "https://github.com/other/repository/issues/109",
        "https://github.com/owner/repository/pull/109",
    ),
)
def test_production_source_rejects_unknown_ready_reference(ready_ref):
    from gwo_v8.plan_control import PlanControlError

    with pytest.raises(PlanControlError) as rejected:
        _source(_IssueClient(body="Contract")).canonical_ready_refs(
            "owner/repository",
            (ready_ref,),
        )

    assert rejected.value.code == "READY_REFS_INVALID"


class _RefContentClient:
    """One exact control-branch-ref fake for RP3 persistence tests."""

    def __init__(self, writer_generation="writer:one"):
        from gwo_v8._canonical import canonical_bytes
        from gwo_v8.activation import GitHubContent

        writer = canonical_bytes(
            {
                "schema_version": 1,
                "current": {
                    "repository": "owner/repository",
                    "writer_generation": writer_generation,
                    "record_id": "writer-record:one",
                },
                "records": [{"record_id": "writer-record:one"}],
            }
        )
        self._content_type = GitHubContent
        self._commits = {
            "commit:1": {
                ".gwo-v8/writer-transition.json": GitHubContent(
                    writer,
                    "blob:writer:one",
                )
            }
        }
        self.head = "commit:1"
        self.writes = []
        self.before_ref_cas = None

    def read_ref(self, repository, branch):
        assert repository == "owner/repository"
        assert branch == "gwo-control"
        return self.head

    def read_at_ref(self, repository, ref_digest, path):
        assert repository == "owner/repository"
        return self._commits[ref_digest].get(path)

    def compare_and_swap_ref(
        self,
        repository,
        branch,
        *,
        expected_ref_digest,
        changes,
        message,
    ):
        del message
        assert repository == "owner/repository"
        assert branch == "gwo-control"
        if self.before_ref_cas is not None:
            callback = self.before_ref_cas
            self.before_ref_cas = None
            callback()
        if expected_ref_digest != self.head:
            raise RuntimeError("synthetic control-ref CAS conflict")
        tree = dict(self._commits[self.head])
        for path, content in changes.items():
            tree[path] = self._content_type(
                content,
                f"blob:{len(self.writes) + 1}:{path}",
            )
        self.writes.append((expected_ref_digest, dict(changes)))
        self.head = f"commit:{len(self._commits) + 1}"
        self._commits[self.head] = tree
        return self.head

    def advance_writer(self, writer_generation):
        from gwo_v8._canonical import canonical_bytes

        tree = dict(self._commits[self.head])
        tree[".gwo-v8/writer-transition.json"] = self._content_type(
            canonical_bytes(
                {
                    "schema_version": 1,
                    "current": {
                        "repository": "owner/repository",
                        "writer_generation": writer_generation,
                        "record_id": "writer-record:two",
                    },
                    "records": [{"record_id": "writer-record:two"}],
                }
            ),
            "blob:writer:two",
        )
        self.head = f"commit:{len(self._commits) + 1}"
        self._commits[self.head] = tree


class _PlanSource:
    def __init__(self, body="Do the work"):
        self.body = body

    def snapshot(self, repository, refs):
        assert repository == "owner/repository"
        assert refs == ("issue:109",)
        policy = _policy()
        return {
            "repository": repository,
            "target_branch": "main",
            "campaign_source": {
                "ref": "refs/heads/main",
                "digest": "1" * 64,
            },
            "policy": policy,
            "tickets": [
                {
                    "key": "issue:109",
                    "labels": ["ready-for-agent"],
                    "source": {"ref": "issue:109", "digest": "2" * 64},
                    "contract": {"title": "Contract", "body": self.body},
                    "native_blockers": [],
                }
            ],
        }


class _PlanningGateway:
    def __init__(self, artifacts):
        self.artifacts = artifacts
        self.preflights = 0
        self.progresses = 0

    def planning_preflight(self, subject):
        from gwo_v8.runtime_gateway import PlanningPreflightReceipt

        self.preflights += 1
        return PlanningPreflightReceipt(
            subject_digest=subject.digest,
            stable_action_id=subject.stable_action_id,
            receipt_digest="3" * 64,
        )

    def progress(self, subject, preflight):
        from gwo_v8.runtime_gateway import PlanningReceipt

        assert preflight.receipt_digest == "3" * 64
        self.progresses += 1
        output = self.artifacts.put_canonical(
            {
                "schema_version": "gwo.runtime.output.v1",
                "subject_digest": subject.digest,
                "stable_action_id": subject.stable_action_id,
                "authority_digest": subject.authority_digest,
                "payload": {
                    "admitted_work": ["issue:109"],
                    "dependency_additions": [],
                    "exclusive_resources": {"issue:109": []},
                    "capability_requirements": {
                        "issue:109": ["git", "local_check"]
                    },
                    "decision_requirements": [],
                },
            }
        )
        return PlanningReceipt(
            subject_digest=subject.digest,
            stable_action_id=subject.stable_action_id,
            status="completed",
            receipt_digest="4" * 64,
            output_artifact_digest=output.digest,
            planning_output_artifact_digest=output.digest,
        )


def _ref_control(client, artifacts, *, source=None):
    from gwo_v8.plan_control import PlanControl
    from gwo_v8.plan_control_github import GitHubPlanRepository

    repository = GitHubPlanRepository(
        client,
        repository="owner/repository",
        branch="gwo-control",
        writer_generation="writer:one",
        maximum_state_bytes=4096,
    )
    gateway = _PlanningGateway(artifacts)
    return (
        PlanControl(
            source=_PlanSource() if source is None else source,
            artifacts=artifacts,
            gateway=gateway,
            repository=repository,
        ),
        repository,
        gateway,
    )


def test_rp3_2_governed_objects_recover_a_fresh_plancontrol_host(tmp_path):
    from gwo_v8._canonical import load_canonical_json
    from gwo_v8.runtime_gateway import ArtifactStore

    client = _RefContentClient()
    first_artifacts = ArtifactStore(tmp_path / "first-artifacts")
    first, _repository, first_gateway = _ref_control(client, first_artifacts)
    handle = first.start("owner/repository", ["issue:109"])

    root = client._commits[client.head][".gwo-v8/plan-control-v3.json"]
    index = load_canonical_json(root.content)
    assert index["schema_version"] == "gwo.plan.github-index.v3"
    assert len(root.content) < 4096
    assert "snapshot_bytes_base64" not in root.content.decode("utf-8")
    assert any(
        path.startswith(".gwo-v8/plan-control-v3/objects/")
        for path in client._commits[client.head]
    )

    fresh_artifacts = ArtifactStore(tmp_path / "fresh-artifacts")
    restarted, repository, restarted_gateway = _ref_control(
        client,
        fresh_artifacts,
    )
    repository._hydrate_artifacts(fresh_artifacts)
    assert restarted.read_active(handle).current_revision_digest == (
        first.read_active(handle).current_revision_digest
    )
    assert first_gateway.progresses == 1
    assert restarted_gateway.progresses == 0


def test_rp3_3_writer_and_plancontrol_transition_share_one_control_ref(tmp_path):
    from gwo_v8.plan_control import PlanControlError
    from gwo_v8.runtime_gateway import ArtifactStore

    client = _RefContentClient()
    client.before_ref_cas = lambda: client.advance_writer("writer:other")
    control, _repository, gateway = _ref_control(
        client,
        ArtifactStore(tmp_path / "artifacts"),
    )

    with pytest.raises(PlanControlError) as rejected:
        control.start("owner/repository", ["issue:109"])

    assert rejected.value.code == "DURABLE_CAS_CONFLICT"
    assert ".gwo-v8/plan-control-v3.json" not in client._commits[client.head]
    assert gateway.progresses == 0


def test_rp3_4_keeps_append_only_activation_receipts_in_governed_state(
    tmp_path,
):
    from gwo_v8.runtime_gateway import ArtifactStore

    client = _RefContentClient()
    artifacts = ArtifactStore(tmp_path / "artifacts")
    first, repository, _gateway = _ref_control(client, artifacts)
    handle = first.start("owner/repository", ["issue:109"])
    previous = repository.active_receipt(handle).revision_digest
    successor, restarted_repository, _next_gateway = _ref_control(
        client,
        artifacts,
        source=_PlanSource("Successor contract"),
    )
    successor.start(
        "owner/repository",
        ["issue:109"],
        campaign_key=handle.campaign_key,
        expected_previous_revision_digest=previous,
    )

    recovered = restarted_repository._read_repo()
    assert len(recovered.activation_receipts) == 2
    assert recovered.active_receipt(handle).revision_digest != previous


def test_rp3_5_active_readback_recompiles_the_frozen_plan_provenance(
    tmp_path,
):
    from dataclasses import replace

    from gwo_v8.plan_control import InMemoryPlanRepository, PlanControl, PlanControlError
    from gwo_v8.runtime_gateway import ArtifactStore

    class TamperingRepository:
        def __init__(self):
            self.inner = InMemoryPlanRepository(writer_generation="writer:one")
            self.writer_generation = self.inner.writer_generation
            self.tamper = False

        def __getattr__(self, name):
            return getattr(self.inner, name)

        def read_revision(self, digest):
            revision = self.inner.read_revision(digest)
            if revision is not None and self.tamper:
                return replace(revision, snapshot_digest="9" * 64)
            return revision

    artifacts = ArtifactStore(tmp_path / "artifacts")
    repository = TamperingRepository()
    control = PlanControl(
        source=_PlanSource(),
        artifacts=artifacts,
        gateway=_PlanningGateway(artifacts),
        repository=repository,
    )
    handle = control.start("owner/repository", ["issue:109"])
    repository.tamper = True

    with pytest.raises(PlanControlError) as rejected:
        control.read_active(handle)

    assert rejected.value.code == "PLAN_REVISION_PROVENANCE_INVALID"


def test_rp3_6_canonicalizes_refs_before_campaign_and_override_identity(
    tmp_path,
):
    from gwo_v8.plan_control import InMemoryPlanRepository
    from gwo_v8.plan_control_host import ProductionPlanControlStartHost
    from gwo_v8.runtime_gateway import ProfileMapping, RuntimeConfiguration
    from gwo_v8.runtime_profile import RuntimeProfile

    class CanonicalSource(_PlanSource):
        def canonical_ready_refs(self, repository, refs):
            assert repository == "owner/repository"
            spellings = {
                "#109": "issue:109",
                "https://github.com/owner/repository/issues/109": "issue:109",
            }
            return tuple(sorted(spellings.get(ref, ref) for ref in refs))

    profile = RuntimeProfile(
        name="coordinator",
        provider="test",
        model="model:test",
        thinking="high",
        mode="safe",
        features={},
    )
    repository = InMemoryPlanRepository(writer_generation="writer:one")

    def builder(*, artifacts, **_kwargs):
        return _PlanningGateway(artifacts)

    host = ProductionPlanControlStartHost(
        source=CanonicalSource(),
        repository=repository,
        runtime_configuration=RuntimeConfiguration(
            profiles={profile.digest: profile},
            host_mappings={"coordinator": ProfileMapping(profile.digest)},
        ),
        repository_contexts={},
        gateway_store_path=tmp_path / "gateway.json",
        artifact_root=tmp_path / "artifacts",
        _gateway_builder=builder,
    )
    options = {
        "ticket_overrides": [
            {
                "ticket_key": "issue:109",
                "role": "worker",
                "mapping": {
                    "primary_profile_digest": profile.digest,
                    "availability_fallback_profile_digest": None,
                },
            }
        ]
    }

    first = host.start("owner/repository", ["#109"], options)
    equivalent = host.start(
        "owner/repository",
        ["https://github.com/owner/repository/issues/109"],
    )

    assert equivalent == first
    assert repository.active_receipt(first).ready_refs == ("issue:109",)


def test_rp3_7_recovers_missing_plancontrol_assertion_from_gateway_preflight(
    tmp_path,
):
    from gwo_v8.plan_control import InMemoryPlanRepository
    from gwo_v8.plan_control_host import ProductionPlanControlStartHost
    from gwo_v8.runtime_gateway import (
        CampaignStartRuntimeOverrides,
        ProfileMapping,
        RuntimeConfiguration,
    )
    from gwo_v8.runtime_profile import RuntimeProfile

    profile = RuntimeProfile(
        name="coordinator",
        provider="test",
        model="model:test",
        thinking="high",
        mode="safe",
        features={},
    )
    recovered_assertion = CampaignStartRuntimeOverrides(
        coordinator=ProfileMapping(profile.digest),
    )

    class RecoveringGateway(_PlanningGateway):
        def _campaign_start_assertion(
            self,
            repository,
            campaign_key,
            campaign_handle,
        ):
            assert repository == "owner/repository"
            assert campaign_key
            assert campaign_handle.startswith("campaign-handle:")
            return recovered_assertion

    repository = InMemoryPlanRepository(writer_generation="writer:one")

    def builder(*, artifacts, **_kwargs):
        return RecoveringGateway(artifacts)

    host = ProductionPlanControlStartHost(
        source=_PlanSource(),
        repository=repository,
        runtime_configuration=RuntimeConfiguration(
            profiles={profile.digest: profile},
            host_mappings={"coordinator": ProfileMapping(profile.digest)},
        ),
        repository_contexts={},
        gateway_store_path=tmp_path / "gateway.json",
        artifact_root=tmp_path / "artifacts",
        _gateway_builder=builder,
    )

    handle = host.start("owner/repository", ["issue:109"])

    assert repository.read_runtime_assertion(handle) == recovered_assertion.canonical()


def test_rp3_8_persists_losing_activation_reservation_cleanup(tmp_path):
    from gwo_v8.plan_control import (
        ActivationReceipt,
        PlanControlError,
        PlanningReservation,
    )
    from gwo_v8.runtime_gateway import ArtifactStore

    client = _RefContentClient()
    control, repository, _gateway = _ref_control(
        client,
        ArtifactStore(tmp_path / "artifacts"),
    )
    handle = control.start("owner/repository", ["issue:109"])
    losing = ActivationReceipt(
        repository="owner/repository",
        campaign_key=handle.campaign_key,
        revision_digest="5" * 64,
        expected_previous_revision_digest=None,
        writer_generation="writer:one",
        ready_refs=("issue:109",),
        ticket_keys=("issue:109",),
        planning_subject_digest="6" * 64,
        planning_stable_action_id="planning:loser",
        planning_preflight_receipt_digest="7" * 64,
    )
    repository.reserve_planning(
        PlanningReservation(
            repository=losing.repository,
            campaign_key=losing.campaign_key,
            ticket_keys=losing.ticket_keys,
            subject_digest=losing.planning_subject_digest,
            stable_action_id=losing.planning_stable_action_id,
            preflight_receipt_digest=losing.planning_preflight_receipt_digest,
        )
    )
    repository.reserve_claims(losing)
    with pytest.raises(PlanControlError) as conflict:
        repository.activate(losing)

    assert conflict.value.code == "ACTIVATION_CAS_CONFLICT"
    assert repository._read_repo().pending_reservations == {}


def test_rp3_9_hides_plancontrol_persistence_doubles_from_package_exports():
    import gwo_v8

    for private_name in (
        "GitHubPlanRepository",
        "GitHubReadySnapshotSource",
        "GitHubCliIssueReadClient",
        "InMemoryPlanRepository",
        "PlanningReservation",
        "PlanControl",
        "install_plan_control_start",
    ):
        assert private_name not in gwo_v8.__all__
        assert not hasattr(gwo_v8, private_name)
