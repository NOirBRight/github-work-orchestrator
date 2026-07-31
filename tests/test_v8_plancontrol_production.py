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


def _campaign_source():
    from gwo_v8._canonical import digest_value

    value = {
        "repository": "owner/repository",
        "input_ref": "refs/heads/main",
        "resolved_commit_oid": "a" * 40,
        "tree_oid": "b" * 40,
    }
    return {**value, "digest": digest_value(value)}


def _contract(number=109, body="Do the work"):
    return {
        "id": number,
        "node_id": f"ISSUE_{number}",
        "number": number,
        "title": "Contract",
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
                "url": "https://api.github.com/repos/owner/repository/labels/ready-for-agent",
                "name": "ready-for-agent",
                "color": "0052cc",
                "default": False,
                "description": "ready",
            }
        ],
        "comments": [],
        "updated_at": "2026-07-30T00:00:00Z",
    }


def _frozen_blocker(number=108, *, state="open"):
    from gwo_v8._canonical import digest_value

    contract = {
        "key": f"issue:{number}",
        "state": state,
        "repository": {
            "full_name": "owner/repository",
            "url": "https://api.github.com/repos/owner/repository",
        },
    }
    return {
        **contract,
        "source": {
            "ref": contract["key"],
            "digest": digest_value(contract),
        },
    }


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
            "id": number,
            "node_id": f"ISSUE_{number}",
            "number": number,
            "title": f"Contract {number}",
            "body": self.body,
            "state": self.state,
            "state_reason": None,
            "type": None,
            "updated_at": "2026-07-30T00:00:00Z",
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
                    "default": False,
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

    def read_branch_source(self, repository, branch):
        return {
            "input_ref": "refs/heads/main",
            "resolved_commit_oid": "a" * 40,
            "tree_oid": "b" * 40,
        }


def _blocker(number: int, *, repository="owner/repository"):
    return {
        "id": number,
        "node_id": f"ISSUE_{number}",
        "number": number,
        "state": "open",
        "repository_url": f"https://api.github.com/repos/{repository}",
        "html_url": f"https://github.com/{repository}/issues/{number}",
        "url": f"https://api.github.com/repos/{repository}/issues/{number}",
        "updated_at": "2026-07-30T00:00:00Z",
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
        "id": 109,
        "node_id": "ISSUE_109",
        "number": 109,
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
                    "default": False,
                "description": "ready-for-agent description",
                "url": (
                    "https://api.github.com/repos/owner/repository/"
                    "labels/ready-for-agent"
                ),
            }
        ],
        "comments": list(comments),
        "updated_at": "2026-07-30T00:00:00Z",
    }
    assert len(ticket["native_blockers"]) == 31
    assert ticket["native_blockers"][0]["key"] == "issue:200"
    assert ticket["native_blockers"][-1]["key"] == "issue:230"
    assert snapshot["campaign_source"]["input_ref"] == "refs/heads/main"
    assert snapshot["campaign_source"]["resolved_commit_oid"] == "a" * 40


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

    @staticmethod
    def _writer_value(
        writer_generation,
        *,
        status="cut_over",
        repository="owner/repository",
    ):
        from gwo_v8._canonical import digest_value

        def record_id(value):
            identity = {
                key: value[key]
                for key in (
                    "repository",
                    "kind",
                    "status",
                    "previous_writer_generation",
                    "writer_generation",
                    "activation_id",
                    "plan_digest",
                    "canary_evidence_digest",
                    "canary_evidence_refs",
                    "canary_manifest_ref",
                    "worker_capacity",
                    "coordinator_capacity",
                    "reason",
                )
            }
            return f"writer-transition:{digest_value(identity)[:24]}"

        pending = {
            "record_id": "",
            "repository": repository,
            "kind": "cutover_pending",
            "status": "pending",
            "previous_writer_generation": "v6.1",
            "writer_generation": writer_generation,
            "activation_id": None,
            "plan_digest": "a" * 64,
            "canary_evidence_digest": "b" * 64,
            "canary_evidence_refs": ["github://canary/evidence"],
            "canary_manifest_ref": "github://canary/manifest",
            "worker_capacity": 0,
            "coordinator_capacity": 0,
            "reason": None,
            "created_at": "2026-07-30T00:00:00+00:00",
        }
        record = {
            **pending,
            "record_id": "",
            "kind": "cutover",
            "status": "cut_over",
            "previous_writer_generation": writer_generation,
            "activation_id": "activation:cutover",
            "worker_capacity": 8,
            "coordinator_capacity": 1,
        }
        records = [pending, record]
        current = record
        if status == "draining":
            current = {
                **record,
                "record_id": "",
                "kind": "drain",
                "status": "draining",
                "worker_capacity": 0,
                "coordinator_capacity": 0,
                "reason": "test drain",
            }
            records.append(current)
        elif status == "pending":
            current = pending
        elif status not in {"cut_over", "draining", "pending"}:
            current = {
                **record,
                "record_id": "",
                "status": status,
            }
            records.append(current)
        for item in records:
            item["record_id"] = record_id(item)
        return {
            "schema_version": 1,
            "current": {
                "repository": repository,
                "writer_generation": writer_generation,
                "record_id": current["record_id"],
            },
            "records": records,
        }

    @staticmethod
    def _activation_value(writer_generation, *, repository="owner/repository"):
        receipt = {
            "schema_version": 1,
            "repository": repository,
            "writer_generation": writer_generation,
            "activation_id": "activation:cutover",
            "plan_digest": "a" * 64,
            "expected_previous_digest": None,
            "plan_record_ref": f"github://{repository}/cutover-plan",
            "created_at": "2026-07-30T00:00:00+00:00",
        }
        return {
            "schema_version": 1,
            "repository": repository,
            "active_plan_digest": receipt["plan_digest"],
            "receipts": [receipt],
        }

    def __init__(self, writer_generation="writer:one", *, repository="owner/repository"):
        from gwo_v8._canonical import canonical_bytes
        from gwo_v8.activation import GitHubContent

        self.repository = repository
        writer = canonical_bytes(
            self._writer_value(writer_generation, repository=repository)
        )
        activation = canonical_bytes(
            self._activation_value(writer_generation, repository=repository)
        )
        policy = canonical_bytes(_policy())
        self._content_type = GitHubContent
        self._commits = {
            "commit:1": {
                ".gwo-v8/writer-transition.json": GitHubContent(
                    writer,
                    "blob:writer:one",
                ),
                ".gwo/v8/active-plan.json": GitHubContent(
                    activation,
                    "blob:activation:one",
                ),
                ".gwo-v8/policy-witness.json": GitHubContent(
                    policy,
                    "blob:policy",
                ),
            }
        }
        self.head = "commit:1"
        self.writes = []
        self.before_ref_cas = None
        self.after_ref_cas = None
        self.drain_blockers = []
        self.activation_barrier = None

    def read_ref(self, repository, branch):
        assert repository == self.repository
        assert branch == "gwo-control"
        return self.head

    def read_at_ref(self, repository, ref_digest, path):
        assert repository == self.repository
        return self._commits[ref_digest].get(path)

    def read(self, repository, branch, path):
        assert repository == self.repository
        assert branch == "gwo-control"
        return self._commits[self.head].get(path)

    def compare_and_swap_ref(
        self,
        repository,
        branch,
        *,
        expected_ref_digest,
        changes,
        message,
    ):
        assert repository == self.repository
        assert branch == "gwo-control"
        if "activate Plan Revision" in message and self.activation_barrier is not None:
            self.activation_barrier.wait()
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
        if self.after_ref_cas is not None:
            callback = self.after_ref_cas
            self.after_ref_cas = None
            callback()
        return self.head

    def advance_writer(self, writer_generation, *, status="cut_over"):
        from gwo_v8._canonical import canonical_bytes
        from gwo_v8.transition import _writer_drain_dispatch_blocker

        if status == "draining":
            blocker = _writer_drain_dispatch_blocker(
                self,
                self.repository,
                self.head,
                writer_generation=writer_generation,
                cut_over_record_id=self._writer_value(
                    writer_generation,
                    repository=self.repository,
                )["current"]["record_id"],
            )
            if blocker is not None:
                self.drain_blockers.append(blocker)
                raise RuntimeError(blocker)

        tree = dict(self._commits[self.head])
        tree[".gwo-v8/writer-transition.json"] = self._content_type(
            canonical_bytes(
                self._writer_value(
                    writer_generation,
                    status=status,
                    repository=self.repository,
                )
            ),
            "blob:writer:two",
        )
        tree[".gwo/v8/active-plan.json"] = self._content_type(
            canonical_bytes(
                self._activation_value(
                    writer_generation,
                    repository=self.repository,
                )
            ),
            "blob:activation:two",
        )
        self.head = f"commit:{len(self._commits) + 1}"
        self._commits[self.head] = tree


class _PlanSource:
    def __init__(self, body="Do the work"):
        self.body = body

    def snapshot(self, repository, refs):
        from gwo_v8.plan_control import frozen_ticket_contract_digest

        assert repository == "owner/repository"
        assert refs == ("issue:109",)
        policy = _policy()
        contract = _contract(body=self.body)
        blockers = []
        return {
            "repository": repository,
            "target_branch": "main",
            "campaign_source": _campaign_source(),
            "policy": policy,
            "tickets": [
                {
                    "key": "issue:109",
                    "labels": ["ready-for-agent"],
                    "source": {
                        "ref": "issue:109",
                        "digest": frozen_ticket_contract_digest(
                            key="issue:109",
                            contract=contract,
                            labels=["ready-for-agent"],
                            native_blockers=blockers,
                        ),
                    },
                    "contract": contract,
                    "native_blockers": blockers,
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
    assert index["schema_version"] == "gwo.plan.github-index.v5"
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
    active = repository.active_receipt(handle)
    assert active is not None
    repository.hydrate_campaign_artifacts(
        fresh_artifacts,
        repository.observe_campaign(handle, active.revision_digest),
    )
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


def test_rp6_6_runtime_assertion_never_enters_plancontrol_state(
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
    repository = InMemoryPlanRepository(writer_generation="writer:one")

    def builder(*, artifacts, **_kwargs):
        return _PlanningGateway(artifacts)

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

    assert handle.repository == "owner/repository"
    assert not hasattr(repository, "runtime_assertions")


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
        compilation_record_artifact_digest="8" * 64,
        planning_receipt_digest="9" * 64,
        planning_output_artifact_digest="a" * 64,
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


@pytest.mark.parametrize(
    ("status", "read_allowed", "new_work_allowed"),
    (
        ("cut_over", True, True),
        ("draining", True, False),
        ("pending", False, False),
        ("rolled_back", False, False),
        ("blocked", False, False),
    ),
)
def test_rp4_1_writer_transition_authority_is_operation_sensitive(
    tmp_path,
    status,
    read_allowed,
    new_work_allowed,
):
    from gwo_v8.plan_control import CampaignHandle, PlanControlError
    from gwo_v8.plan_control_github import GitHubPlanRepository
    from gwo_v8.runtime_gateway import ArtifactStore

    client = _RefContentClient()
    client.advance_writer("writer:one", status=status)
    repository = GitHubPlanRepository(
        client,
        repository="owner/repository",
        branch="gwo-control",
        writer_generation="writer:one",
        maximum_state_bytes=4096,
    )
    handle = CampaignHandle("owner/repository", "campaign:one")
    if read_allowed:
        assert repository.active_receipt(handle) is None
    else:
        with pytest.raises(PlanControlError):
            repository.active_receipt(handle)
    control = _ref_control(client, ArtifactStore(tmp_path / "artifacts"))[0]
    if new_work_allowed:
        assert control.start("owner/repository", ["issue:109"])
    else:
        with pytest.raises(PlanControlError) as rejected:
            control.start("owner/repository", ["issue:109"])
        assert rejected.value.code in {
            "WRITER_FENCE_CONFLICT",
            "WRITER_FENCE_READBACK_INVALID",
        }


def test_rp4_1_rejects_malformed_writer_transition_before_mutation(tmp_path):
    from gwo_v8._canonical import canonical_bytes
    from gwo_v8.plan_control import PlanControlError
    from gwo_v8.runtime_gateway import ArtifactStore

    client = _RefContentClient()
    tree = client._commits[client.head]
    tree[".gwo-v8/writer-transition.json"] = client._content_type(
        canonical_bytes(
            {
                "schema_version": 1,
                "current": {
                    "repository": "owner/repository",
                    "writer_generation": "writer:one",
                    "record_id": "writer-record:one",
                },
                "records": [{"record_id": "writer-record:one"}],
            }
        ),
        "blob:malformed-writer",
    )
    control, _repository, _gateway = _ref_control(
        client,
        ArtifactStore(tmp_path / "artifacts"),
    )
    with pytest.raises(PlanControlError) as rejected:
        control.start("owner/repository", ["issue:109"])
    assert rejected.value.code == "WRITER_FENCE_READBACK_INVALID"
    assert client.writes == []


def test_rp4_2_persists_exact_campaign_source_after_branch_advances(tmp_path):
    from gwo_v8._canonical import digest_value
    from gwo_v8.runtime_gateway import ArtifactStore

    class BranchSource(_PlanSource):
        def __init__(self):
            super().__init__()
            self.commit = "a" * 40
            self.tree = "b" * 40

        def snapshot(self, repository, refs):
            value = super().snapshot(repository, refs)
            source = {
                "repository": repository,
                "input_ref": "refs/heads/main",
                "resolved_commit_oid": self.commit,
                "tree_oid": self.tree,
            }
            value["campaign_source"] = {
                **source,
                "digest": digest_value(source),
            }
            return value

    client = _RefContentClient()
    source = BranchSource()
    artifacts = ArtifactStore(tmp_path / "first")
    first, _repository, _gateway = _ref_control(
        client,
        artifacts,
        source=source,
    )
    handle = first.start("owner/repository", ["issue:109"])
    from gwo_v8._canonical import load_canonical_json

    expected = load_canonical_json(
        first.read_active(handle).plan_spec_bytes
    )["campaign"]["source"]
    source.commit, source.tree = "c" * 40, "d" * 40

    fresh_artifacts = ArtifactStore(tmp_path / "fresh")
    recovered, repository, _gateway = _ref_control(
        client,
        fresh_artifacts,
        source=source,
    )
    active = repository.active_receipt(handle)
    assert active is not None
    repository.hydrate_campaign_artifacts(
        fresh_artifacts,
        repository.observe_campaign(handle, active.revision_digest),
    )
    assert load_canonical_json(
        recovered.read_active(handle).plan_spec_bytes
    )["campaign"]["source"] == expected
    assert expected["resolved_commit_oid"] == "a" * 40
    assert expected["tree_oid"] == "b" * 40


def test_rp4_3_installed_host_supports_exact_successor_revisions(tmp_path):
    from gwo_v8.plan_control import InMemoryPlanRepository, PlanControlError
    from gwo_v8.plan_control_host import ProductionPlanControlStartHost
    from gwo_v8.runtime_gateway import ProfileMapping, RuntimeConfiguration
    from gwo_v8.runtime_profile import RuntimeProfile

    profile = RuntimeProfile(
        name="coordinator",
        provider="test",
        model="model:test",
        thinking="high",
        mode="safe",
        features={},
    )
    source = _PlanSource()
    repository = InMemoryPlanRepository(writer_generation="writer:one")
    gateways = []

    def builder(*, artifacts, **_kwargs):
        gateway = _PlanningGateway(artifacts)
        gateways.append(gateway)
        return gateway

    def host(artifact_root):
        return ProductionPlanControlStartHost(
            source=source,
            repository=repository,
            runtime_configuration=RuntimeConfiguration(
                profiles={profile.digest: profile},
                host_mappings={"coordinator": ProfileMapping(profile.digest)},
            ),
            repository_contexts={},
            gateway_store_path=tmp_path / "gateway.json",
            artifact_root=artifact_root,
            _gateway_builder=builder,
        )

    first_host = host(tmp_path / "artifacts")
    handle = first_host.start("owner/repository", ["issue:109"])
    initial = repository.active_receipt(handle).revision_digest
    source.body = "Successor contract"
    assert first_host.start_successor(
        handle,
        ["issue:109"],
        expected_previous_revision_digest=initial,
    ) == handle
    successor = repository.active_receipt(handle).revision_digest
    assert successor != initial
    assert first_host.start_successor(
        handle,
        ["issue:109"],
        expected_previous_revision_digest=initial,
    ) == handle
    with pytest.raises(PlanControlError) as stale:
        first_host.start_successor(
            handle,
            ["issue:109"],
            expected_previous_revision_digest="f" * 64,
        )
    assert stale.value.code == "ACTIVATION_CAS_CONFLICT"
    restarted = host(tmp_path / "artifacts")
    assert restarted.start_successor(
        handle,
        ["issue:109"],
        expected_previous_revision_digest=initial,
    ) == handle
    assert repository.active_receipt(handle).revision_digest == successor
    assert sum(gateway.progresses for gateway in gateways) == 2


@pytest.mark.parametrize("change", ("issue", "comments", "blockers", "source"))
def test_rp4_4_snapshot_capture_rejects_selected_frontier_mutation(change):
    from gwo_v8.plan_control import PlanControlError

    class MutatingIssueClient(_IssueClient):
        def __init__(self):
            super().__init__(body="Contract", blockers=(), comments=())
            self.calls = 0
            self.source_calls = 0

        def _mutate(self):
            if change == "issue":
                self.body = "Changed contract"
            elif change == "comments":
                self.comments = (_comment(1),)
            elif change == "blockers":
                self.body = "Contract\n\n## Blocked by\n\n- #200"
                self.blockers = (_blocker(200),)

        def read_issue(self, repository, number):
            self.calls += 1
            if change != "source" and self.calls == 2:
                self._mutate()
            return super().read_issue(repository, number)

        def read_branch_source(self, repository, branch):
            self.source_calls += 1
            value = super().read_branch_source(repository, branch)
            if change == "source" and self.source_calls == 2:
                return {**value, "resolved_commit_oid": "c" * 40}
            return value

    with pytest.raises(PlanControlError) as rejected:
        _source(MutatingIssueClient()).snapshot("owner/repository", ("#109",))
    assert rejected.value.code == "GITHUB_SNAPSHOT_CONCURRENT_CHANGE"


def test_rp4_4_concurrent_snapshot_never_reaches_planning_or_publication(tmp_path):
    from gwo_v8.plan_control import InMemoryPlanRepository, PlanControl, PlanControlError
    from gwo_v8.runtime_gateway import ArtifactStore

    class MutatingSourceClient(_IssueClient):
        def __init__(self):
            super().__init__(body="Contract")
            self.calls = 0

        def read_issue(self, repository, number):
            self.calls += 1
            if self.calls == 2:
                self.body = "Changed contract"
            return super().read_issue(repository, number)

    artifacts = ArtifactStore(tmp_path / "artifacts")
    repository = InMemoryPlanRepository(writer_generation="writer:one")
    gateway = _PlanningGateway(artifacts)
    control = PlanControl(
        source=_source(MutatingSourceClient()),
        artifacts=artifacts,
        gateway=gateway,
        repository=repository,
    )
    with pytest.raises(PlanControlError) as rejected:
        control.start("owner/repository", ["issue:109"])
    assert rejected.value.code == "GITHUB_SNAPSHOT_CONCURRENT_CHANGE"
    assert gateway.progresses == 0
    assert not repository.attempts and not repository.revisions and not repository.claims


def test_rp4_5_cli_dependency_fallback_is_precise_and_reachable(monkeypatch):
    from gwo_v8.github_snapshot import GitHubCliIssueReadClient
    from gwo_v8.plan_control import PlanControlError

    def unsupported(*_args, **_kwargs):
        return subprocess.CompletedProcess(
            args=[],
            returncode=1,
            stdout="",
            stderr=(
                "HTTP 404: Issue dependencies are not supported for this repository"
            ),
        )

    monkeypatch.setattr("gwo_v8.github_snapshot.subprocess.run", unsupported)
    client = GitHubCliIssueReadClient()
    assert client.read_blockers("owner/repository", 109) is None

    def forbidden(*_args, **_kwargs):
        return subprocess.CompletedProcess(
            args=[],
            returncode=1,
            stdout="",
            stderr="HTTP 403: Resource not accessible by integration",
        )

    monkeypatch.setattr("gwo_v8.github_snapshot.subprocess.run", forbidden)
    with pytest.raises(PlanControlError) as rejected:
        client.read_blockers("owner/repository", 109)
    assert rejected.value.code == "GITHUB_SNAPSHOT_UNAVAILABLE"


def test_rp4_6_long_lived_host_hydrates_remote_campaign_on_demand(tmp_path):
    from gwo_v8.plan_control_github import GitHubPlanRepository
    from gwo_v8.plan_control_host import ProductionPlanControlStartHost
    from gwo_v8.runtime_gateway import ProfileMapping, RuntimeConfiguration
    from gwo_v8.runtime_profile import RuntimeProfile

    profile = RuntimeProfile(
        name="coordinator",
        provider="test",
        model="model:test",
        thinking="high",
        mode="safe",
        features={},
    )
    client = _RefContentClient()
    gateways = []

    def make_host(name):
        repository = GitHubPlanRepository(
            client,
            repository="owner/repository",
            branch="gwo-control",
            writer_generation="writer:one",
            maximum_state_bytes=4096,
        )

        def builder(*, artifacts, **_kwargs):
            gateway = _PlanningGateway(artifacts)
            gateways.append((name, gateway))
            return gateway

        return ProductionPlanControlStartHost(
            source=_PlanSource(),
            repository=repository,
            runtime_configuration=RuntimeConfiguration(
                profiles={profile.digest: profile},
                host_mappings={"coordinator": ProfileMapping(profile.digest)},
            ),
            repository_contexts={},
            gateway_store_path=tmp_path / f"{name}.gateway.json",
            artifact_root=tmp_path / f"{name}.artifacts",
            _gateway_builder=builder,
        )

    first = make_host("first")
    second = make_host("second")
    handle = second.start("owner/repository", ["issue:109"])
    assert first.start("owner/repository", ["issue:109"]) == handle
    assert [gateway.progresses for name, gateway in gateways if name == "second"] == [1]
    assert [gateway.progresses for name, gateway in gateways if name == "first"] == [0]


def test_rp4_7_incomplete_ticket_contract_fails_before_planning(tmp_path):
    from gwo_v8.plan_control import InMemoryPlanRepository, PlanControl, PlanControlError
    from gwo_v8.runtime_gateway import ArtifactStore

    class IncompleteSource(_PlanSource):
        def snapshot(self, repository, refs):
            value = super().snapshot(repository, refs)
            value["tickets"][0]["contract"] = {
                "title": "Contract",
                "body": "Incomplete",
            }
            return value

    artifacts = ArtifactStore(tmp_path / "artifacts")
    repository = InMemoryPlanRepository(writer_generation="writer:one")
    gateway = _PlanningGateway(artifacts)
    with pytest.raises(PlanControlError) as rejected:
        PlanControl(
            source=IncompleteSource(),
            artifacts=artifacts,
            gateway=gateway,
            repository=repository,
        ).start("owner/repository", ["issue:109"])
    assert rejected.value.code == "TICKET_CONTRACT_MISSING"
    assert gateway.progresses == 0
    assert not repository.attempts and not repository.revisions and not repository.claims


@pytest.mark.parametrize(
    "kwargs",
    (
        {"path": "../writer-transition.json"},
        {"path": ".gwo-v8/writer-transition.json"},
        {"object_prefix": ".gwo-v8/plan-control-v3.json/objects"},
        {"writer_control_path": ".gwo-v8/plan-control-v3/objects"},
    ),
)
def test_rp4_8_rejects_unsafe_or_overlapping_durable_paths_without_mutation(kwargs):
    from gwo_v8.plan_control import PlanControlError
    from gwo_v8.plan_control_github import GitHubPlanRepository

    client = _RefContentClient()
    with pytest.raises(PlanControlError) as rejected:
        GitHubPlanRepository(
            client,
            repository="owner/repository",
            branch="gwo-control",
            writer_generation="writer:one",
            **kwargs,
        )
    assert rejected.value.code == "PLAN_CONTROL_COMPOSITION_INVALID"
    assert client.writes == []


@pytest.mark.parametrize("field", ("exclusive_resources", "capability_requirements"))
def test_rp4_9_missing_per_ticket_planning_facts_never_publish_or_claim(
    tmp_path,
    field,
):
    from gwo_v8.plan_control import InMemoryPlanRepository, PlanControl, PlanControlError
    from gwo_v8.runtime_gateway import ArtifactStore, PlanningReceipt

    class OmissionGateway(_PlanningGateway):
        def progress(self, subject, preflight):
            self.progresses += 1
            payload = {
                "admitted_work": ["issue:109"],
                "dependency_additions": [],
                "exclusive_resources": {"issue:109": []},
                "capability_requirements": {"issue:109": ["git"]},
                "decision_requirements": [],
            }
            payload[field] = {}
            output = self.artifacts.put_canonical(
                {
                    "schema_version": "gwo.runtime.output.v1",
                    "subject_digest": subject.digest,
                    "stable_action_id": subject.stable_action_id,
                    "authority_digest": subject.authority_digest,
                    "payload": payload,
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

    artifacts = ArtifactStore(tmp_path / "artifacts")
    repository = InMemoryPlanRepository(writer_generation="writer:one")
    gateway = OmissionGateway(artifacts)
    with pytest.raises(PlanControlError) as rejected:
        PlanControl(
            source=_PlanSource(),
            artifacts=artifacts,
            gateway=gateway,
            repository=repository,
        ).start("owner/repository", ["issue:109"])
    assert rejected.value.code == "PLAN_INTENT_OMISSION"
    assert gateway.progresses == 1
    assert not repository.revisions and not repository.claims and not repository.activations


def test_rp4_10_rejects_every_broken_activation_receipt_chain():
    from gwo_v8.plan_control import ActivationReceipt, InMemoryPlanRepository, PlanControlError
    from gwo_v8.plan_control_github import _repo_from_state, _repo_value

    def receipt(revision, previous, action):
        return ActivationReceipt(
            repository="owner/repository",
            campaign_key="campaign:one",
            revision_digest=revision,
            expected_previous_revision_digest=previous,
            writer_generation="writer:one",
            ready_refs=("issue:109",),
            ticket_keys=("issue:109",),
            planning_subject_digest="1" * 64,
            planning_stable_action_id=action,
            planning_preflight_receipt_digest="2" * 64,
            compilation_record_artifact_digest="a" * 64,
            planning_receipt_digest="b" * 64,
            planning_output_artifact_digest="c" * 64,
        )

    initial = receipt("3" * 64, None, "planning:initial")
    successor = receipt("4" * 64, initial.revision_digest, "planning:successor")
    fork = receipt("5" * 64, initial.revision_digest, "planning:fork")
    orphan = receipt("6" * 64, None, "planning:orphan")
    cases = {
        "truncated": ([successor], successor),
        "forked": ([initial, successor, fork], successor),
        "orphan": ([initial, successor, orphan], successor),
        "duplicate": ([initial, successor, successor], successor),
    }
    for receipts, current in cases.values():
        repository = InMemoryPlanRepository(writer_generation="writer:one")
        repository.activations[("owner/repository", "campaign:one")] = current
        for item in receipts:
            repository.activation_receipts[
                (
                    item.repository,
                    item.campaign_key,
                    item.revision_digest,
                    item.planning_stable_action_id,
                )
            ] = item
        state = _repo_value("owner/repository", "writer:one", repository)
        if receipts[-1] == successor and len(receipts) == 3:
            state["activation_receipts"].append(state["activation_receipts"][-1])
        with pytest.raises(PlanControlError) as rejected:
            _repo_from_state(state, "owner/repository", "writer:one")
        assert rejected.value.code == "DURABLE_STATE_INVALID"


def test_rp4_11_deep_dependency_graphs_have_typed_iterative_outcomes():
    from gwo_v8.plan_control import PlanControlError, _assert_acyclic

    depth = 1_500
    acyclic = {
        f"issue:{index}": ({f"issue:{index + 1}"} if index + 1 < depth else set())
        for index in range(depth)
    }
    _assert_acyclic(acyclic)
    cyclic = {key: set(values) for key, values in acyclic.items()}
    cyclic[f"issue:{depth - 1}"].add("issue:0")
    with pytest.raises(PlanControlError) as rejected:
        _assert_acyclic(cyclic)
    assert rejected.value.code == "DEPENDENCY_CYCLE"


def test_rp4_12_in_memory_activation_cas_is_lock_serialized():
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from gwo_v8.plan_control import ActivationReceipt, InMemoryPlanRepository, PlanControlError

    def receipt(revision, previous, action):
        return ActivationReceipt(
            repository="owner/repository",
            campaign_key="campaign:one",
            revision_digest=revision,
            expected_previous_revision_digest=previous,
            writer_generation="writer:one",
            ready_refs=("issue:109",),
            ticket_keys=("issue:109",),
            planning_subject_digest="1" * 64,
            planning_stable_action_id=action,
            planning_preflight_receipt_digest="2" * 64,
            compilation_record_artifact_digest="a" * 64,
            planning_receipt_digest="b" * 64,
            planning_output_artifact_digest="c" * 64,
        )

    repository = InMemoryPlanRepository(writer_generation="writer:one")

    def race(receipts):
        barrier = Barrier(len(receipts))

        def activate(item):
            barrier.wait()
            try:
                repository.activate(item)
                return "won"
            except PlanControlError as error:
                return error.code

        with ThreadPoolExecutor(max_workers=len(receipts)) as pool:
            return list(pool.map(activate, receipts))

    initials = [receipt("3" * 64, None, "planning:one"), receipt("4" * 64, None, "planning:two")]
    initial_results = race(initials)
    assert sorted(initial_results) == ["ACTIVATION_CAS_CONFLICT", "won"]
    winner = repository.active_receipt(
        type("Handle", (), {"repository": "owner/repository", "campaign_key": "campaign:one"})()
    )
    successors = [
        receipt("5" * 64, winner.revision_digest, "planning:three"),
        receipt("6" * 64, winner.revision_digest, "planning:four"),
    ]
    successor_results = race(successors)
    assert sorted(successor_results) == ["ACTIVATION_CAS_CONFLICT", "won"]


@pytest.mark.parametrize(
    "tamper",
    ("activation", "historical", "duplicate_json", "transition"),
)
def test_rp5_1_writer_ledger_is_complete_and_activation_bound(tamper):
    from gwo_v8._canonical import canonical_bytes
    from gwo_v8.plan_control import CampaignHandle, PlanControlError
    from gwo_v8.plan_control_github import GitHubPlanRepository

    client = _RefContentClient()
    tree = client._commits[client.head]
    writer = client._writer_value("writer:one")
    if tamper == "activation":
        writer["records"][1]["activation_id"] = "activation:wrong"
    elif tamper == "historical":
        writer["records"][0]["canary_evidence_refs"] = "not-a-list"
    elif tamper == "duplicate_json":
        tree[".gwo-v8/writer-transition.json"] = client._content_type(
            b'{"schema_version":1,"schema_version":1,"current":{},"records":[]}',
            "blob:duplicate-writer",
        )
    else:
        writer["records"].pop(0)
    if tamper != "duplicate_json":
        tree[".gwo-v8/writer-transition.json"] = client._content_type(
            canonical_bytes(writer),
            f"blob:writer-{tamper}",
        )
    repository = GitHubPlanRepository(
        client,
        repository="owner/repository",
        branch="gwo-control",
        writer_generation="writer:one",
    )
    with pytest.raises(PlanControlError):
        repository.active_receipt(CampaignHandle("owner/repository", "campaign:one"))
    assert client.writes == []


@pytest.mark.parametrize(
    "field",
    (
        "ready_refs",
        "ticket_keys",
        "planning_subject_digest",
        "planning_stable_action_id",
        "planning_preflight_receipt_digest",
        "writer_generation",
        "revision_digest",
        "compilation_record_artifact_digest",
        "planning_receipt_digest",
        "planning_output_artifact_digest",
    ),
)
def test_rp5_2_tampered_active_receipt_cannot_mutate_claims(tmp_path, field):
    from dataclasses import replace

    from gwo_v8.plan_control import InMemoryPlanRepository, PlanControl, PlanControlError
    from gwo_v8.runtime_gateway import ArtifactStore

    artifacts = ArtifactStore(tmp_path / "artifacts")
    repository = InMemoryPlanRepository(writer_generation="writer:one")
    control = PlanControl(
        source=_PlanSource(),
        artifacts=artifacts,
        gateway=_PlanningGateway(artifacts),
        repository=repository,
    )
    handle = control.start("owner/repository", ["issue:109"])
    original = repository.active_receipt(handle)
    replacements = {
        "ready_refs": ("issue:110",),
        "ticket_keys": ("issue:110",),
        "planning_subject_digest": "9" * 64,
        "planning_stable_action_id": "planning:forged",
        "planning_preflight_receipt_digest": "8" * 64,
        "writer_generation": "writer:forged",
        "revision_digest": "7" * 64,
        "compilation_record_artifact_digest": "6" * 64,
        "planning_receipt_digest": "5" * 64,
        "planning_output_artifact_digest": "4" * 64,
    }
    forged = replace(original, **{field: replacements[field]})
    repository.activations[(handle.repository, handle.campaign_key)] = forged
    repository.activation_receipts[
        (
            forged.repository,
            forged.campaign_key,
            forged.revision_digest,
            forged.planning_stable_action_id,
        )
    ] = forged
    repository.pending_reservations[repository._reservation_key(forged)] = forged
    claims_before = dict(repository.claims)
    owners_before = dict(repository._claim_campaigns)
    pending_before = dict(repository.pending_reservations)
    with pytest.raises(PlanControlError) as rejected:
        control.start("owner/repository", ["issue:109"])
    assert rejected.value.code in {
        "ACTIVE_PLAN_CROSS_BINDING_INVALID",
        "PLAN_REVISION_PROVENANCE_INVALID",
    }
    assert repository.claims == claims_before
    assert repository._claim_campaigns == owners_before
    assert repository.pending_reservations == pending_before


def test_rp5_2_tampered_pending_reservation_cannot_finalize_claims(tmp_path):
    from dataclasses import replace

    from gwo_v8.plan_control import InMemoryPlanRepository, PlanControl, PlanControlError
    from gwo_v8.runtime_gateway import ArtifactStore

    artifacts = ArtifactStore(tmp_path / "artifacts")
    repository = InMemoryPlanRepository(writer_generation="writer:one")
    control = PlanControl(source=_PlanSource(), artifacts=artifacts, gateway=_PlanningGateway(artifacts), repository=repository)
    handle = control.start("owner/repository", ["issue:109"])
    receipt = repository.active_receipt(handle)
    repository.claims.clear()
    repository._claim_campaigns.clear()
    forged = replace(receipt, ticket_keys=("issue:110",))
    repository.pending_reservations[repository._reservation_key(receipt)] = forged
    before = dict(repository.pending_reservations)
    with pytest.raises(PlanControlError) as rejected:
        control.start("owner/repository", ["issue:109"])
    assert rejected.value.code == "ACTIVE_PLAN_CROSS_BINDING_INVALID"
    assert repository.claims == {}
    assert repository.pending_reservations == before


def test_rp5_3_draining_allows_only_proven_rollforward(tmp_path):
    from gwo_v8.plan_control import PlanControlError
    from gwo_v8.runtime_gateway import ArtifactStore

    client = _RefContentClient()
    artifacts = ArtifactStore(tmp_path / "artifacts")
    control, repository, gateway = _ref_control(client, artifacts)
    handle = control.start("owner/repository", ["issue:109"])
    receipt = repository.active_receipt(handle)
    # Model a post-activation crash: the Receipt is durable but claims have
    # not yet been finalized.  Draining can perform only this exact recovery.
    def crash_boundary(restored):
        restored.claims.clear()
        restored._claim_campaigns.clear()
        restored.pending_reservations[restored._reservation_key(receipt)] = receipt

    repository._mutate("test crash boundary", crash_boundary)
    client.advance_writer("writer:one", status="draining")
    assert control.start("owner/repository", ["issue:109"]) == handle
    assert repository.read_claim_proofs(handle, receipt.revision_digest)
    completed = repository.read_attempt(
        handle,
        receipt.expected_previous_revision_digest,
    )
    assert repository.save_attempt(completed) == completed
    before_progress = gateway.progresses
    with pytest.raises(PlanControlError) as rejected:
        _ref_control(client, artifacts, source=_PlanSource("new work"))[0].start(
            "owner/repository",
            ["issue:109"],
            campaign_key="campaign:new",
        )
    assert rejected.value.code == "WRITER_FENCE_CONFLICT"
    assert gateway.progresses == before_progress


def test_rp5_4_hydration_retries_one_stable_ref_and_rejects_changed_identity(tmp_path):
    from gwo_v8.plan_control import PlanControlError
    from gwo_v8.plan_control_github import GitHubPlanRepository
    from gwo_v8.plan_control_host import ProductionPlanControlStartHost
    from gwo_v8.runtime_gateway import ProfileMapping, RuntimeConfiguration
    from gwo_v8.runtime_profile import RuntimeProfile

    profile = RuntimeProfile(name="coordinator", provider="test", model="model:test", thinking="high", mode="safe", features={})
    client = _RefContentClient()

    def make_host(name):
        repository = GitHubPlanRepository(client, repository="owner/repository", branch="gwo-control", writer_generation="writer:one", maximum_state_bytes=4096)
        return ProductionPlanControlStartHost(
            source=_PlanSource(),
            repository=repository,
            runtime_configuration=RuntimeConfiguration(profiles={profile.digest: profile}, host_mappings={"coordinator": ProfileMapping(profile.digest)}),
            repository_contexts={},
            gateway_store_path=tmp_path / f"{name}.gateway.json",
            artifact_root=tmp_path / f"{name}.artifacts",
            _gateway_builder=lambda *, artifacts, **_kwargs: _PlanningGateway(artifacts),
        )

    waiting = make_host("waiting")
    publisher = make_host("publisher")
    handle = publisher.start("owner/repository", ["issue:109"])
    repository = waiting._repository
    original = repository._hydrate_repo_artifacts
    changed = False

    def stable_ref_refresh(repo, artifacts, **kwargs):
        nonlocal changed
        original(repo, artifacts, **kwargs)
        if not changed:
            changed = True
            client.advance_writer("writer:one")

    repository._hydrate_repo_artifacts = stable_ref_refresh
    assert waiting.start("owner/repository", ["issue:109"]) == handle

    fresh = make_host("changed")
    original = fresh._repository._hydrate_repo_artifacts

    def changed_identity(repo, artifacts, **kwargs):
        original(repo, artifacts, **kwargs)
        client.advance_writer("writer:other")

    fresh._repository._hydrate_repo_artifacts = changed_identity
    with pytest.raises(PlanControlError) as rejected:
        fresh.start("owner/repository", ["issue:109"])
    assert rejected.value.code in {
        "DURABLE_STATE_CONCURRENT_CHANGE",
        "WRITER_FENCE_CONFLICT",
    }


@pytest.mark.parametrize(
    "mutate",
    ("missing", "extra", "wrong_type", "wrong_repository", "wrong_source"),
)
def test_rp5_5_blockers_require_one_complete_bound_contract(tmp_path, mutate):
    from gwo_v8.plan_control import InMemoryPlanRepository, PlanControl, PlanControlError
    from gwo_v8.runtime_gateway import ArtifactStore

    class BlockedSource(_PlanSource):
        def snapshot(self, repository, refs):
            value = super().snapshot(repository, refs)
            blocker = _frozen_blocker()
            if mutate == "missing":
                blocker.pop("source")
            elif mutate == "extra":
                blocker["unexpected"] = True
            elif mutate == "wrong_type":
                blocker["state"] = 1
            elif mutate == "wrong_repository":
                blocker["repository"]["full_name"] = "other/repository"
            else:
                blocker["source"]["ref"] = "issue:999"
            value["tickets"][0]["native_blockers"] = [blocker]
            return value

    artifacts = ArtifactStore(tmp_path / "artifacts")
    repository = InMemoryPlanRepository(writer_generation="writer:one")
    gateway = _PlanningGateway(artifacts)
    with pytest.raises(PlanControlError) as rejected:
        PlanControl(source=BlockedSource(), artifacts=artifacts, gateway=gateway, repository=repository).start("owner/repository", ["issue:109"])
    assert rejected.value.code in {"SNAPSHOT_INVALID", "PLAN_CONTROL_INVALID"}
    assert gateway.preflights == 0 and gateway.progresses == 0
    assert not repository.attempts and not repository.revisions and not repository.claims


@pytest.mark.parametrize(
    "path",
    (
        ".gwo-v8/canary/state.json",
        ".gwo-v8/runtime-gateway/state.json",
        ".gwo-v8/legacy-writer-fence.json",
        ".gwo-v8/plan-control-v2.json",
        ".GWO-v8/plan-control-v3.json",
        ".gwo-v8/plan-control-v3/objects/../index.json",
    ),
)
def test_rp5_6_closed_gwo_namespace_registry_rejects_every_sibling(path):
    from gwo_v8.plan_control import PlanControlError
    from gwo_v8.plan_control_github import GitHubPlanRepository

    client = _RefContentClient()
    with pytest.raises(PlanControlError) as rejected:
        GitHubPlanRepository(
            client,
            repository="owner/repository",
            branch="gwo-control",
            writer_generation="writer:one",
            path=path,
        )
    assert rejected.value.code == "PLAN_CONTROL_COMPOSITION_INVALID"
    assert client.writes == []
    GitHubPlanRepository(
        client,
        repository="owner/repository",
        branch="gwo-control",
        writer_generation="writer:one",
    )


def test_rp5_7_installed_github_successor_fences_invalid_lineage(tmp_path):
    from gwo_v8.plan_control import CampaignHandle, PlanControlError
    from gwo_v8.plan_control_host import install_github_plan_control_start
    from gwo_v8.runtime_gateway import ProfileMapping, RuntimeConfiguration
    from gwo_v8.runtime_profile import RuntimeProfile

    profile = RuntimeProfile(name="coordinator", provider="test", model="model:test", thinking="high", mode="safe", features={})
    client = _RefContentClient()
    issue = _IssueClient(body="Initial contract")
    gateways = []

    def builder(*, artifacts, **_kwargs):
        gateway = _PlanningGateway(artifacts)
        gateways.append(gateway)
        return gateway

    def host(name, source=issue):
        return install_github_plan_control_start(
            repository="owner/repository",
            control_branch="gwo-control",
            target_branch="main",
            writer_generation="writer:one",
            runtime_configuration=RuntimeConfiguration(profiles={profile.digest: profile}, host_mappings={"coordinator": ProfileMapping(profile.digest)}),
            repository_contexts={},
            gateway_store_path=tmp_path / f"{name}.gateway.json",
            artifact_root=tmp_path / f"{name}.artifacts",
            _content_client=client,
            _issue_client=source,
            _gateway_builder=builder,
        )

    first = host("first")
    handle = first.start("owner/repository", ["issue:109"])
    previous = first._repository.active_receipt(handle).revision_digest
    issue.body = "Successor contract"
    second = host("second")
    assert second.start_successor(handle, ["issue:109"], expected_previous_revision_digest=previous) == handle
    assert second.start_successor(handle, ["issue:109"], expected_previous_revision_digest=previous) == handle
    restarted = host("restarted")
    assert restarted.start_successor(handle, ["issue:109"], expected_previous_revision_digest=previous) == handle
    current = restarted._repository.active_receipt(handle).revision_digest
    issue.body = "Concurrent successor contract"
    from concurrent.futures import ThreadPoolExecutor

    concurrent_hosts = (host("concurrent-a"), host("concurrent-b"))

    def concurrent_start(candidate):
        try:
            return candidate.start_successor(
                handle,
                ["issue:109"],
                expected_previous_revision_digest=current,
            )
        except PlanControlError as error:
            return error.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(concurrent_start, concurrent_hosts))
    assert any(outcome == handle for outcome in outcomes)
    assert all(
        outcome == handle
        or outcome in {
            "DURABLE_CAS_CONFLICT",
            "ACTIVATION_CAS_CONFLICT",
            "PLAN_PUBLICATION_CONFLICT",
            "ACTIVE_PLAN_CROSS_BINDING_INVALID",
        }
        for outcome in outcomes
    ), outcomes
    assert len(second._repository._read_repo().activation_receipts) == 3
    progress_before = sum(item.progresses for item in gateways)
    with pytest.raises(PlanControlError) as stale:
        second.start_successor(handle, ["issue:109"], expected_previous_revision_digest="f" * 64)
    assert stale.value.code == "ACTIVATION_CAS_CONFLICT"
    with pytest.raises(PlanControlError) as missing:
        second.start_successor(CampaignHandle("owner/repository", "campaign:missing"), ["issue:109"], expected_previous_revision_digest=previous)
    assert missing.value.code == "ACTIVATION_CAS_CONFLICT"
    with pytest.raises(PlanControlError) as foreign:
        second.start_successor(CampaignHandle("other/repository", handle.campaign_key), ["issue:109"], expected_previous_revision_digest=previous)
    assert foreign.value.code == "START_SUCCESSOR_INVALID"
    assert sum(item.progresses for item in gateways) == progress_before


def _r6_writer_record_id(value):
    from gwo_v8._canonical import digest_value

    fields = (
        "repository",
        "kind",
        "status",
        "previous_writer_generation",
        "writer_generation",
        "activation_id",
        "plan_digest",
        "canary_evidence_digest",
        "canary_evidence_refs",
        "canary_manifest_ref",
        "worker_capacity",
        "coordinator_capacity",
        "reason",
    )
    return "writer-transition:" + digest_value(
        {field: value[field] for field in fields}
    )[:24]


@pytest.mark.parametrize(
    ("field", "replacement"),
    (
        ("canary_evidence_digest", "d" * 64),
        ("canary_evidence_refs", ["github://canary/substituted"]),
        ("canary_manifest_ref", "github://canary/substituted-manifest"),
    ),
)
def test_rc6_1_writer_lineage_table_rejects_rekeyed_canary_substitution(
    field,
    replacement,
):
    """The record hash is re-addressed: rejection must be the edge table."""
    from gwo_v8._canonical import canonical_bytes
    from gwo_v8.plan_control import CampaignHandle, PlanControlError
    from gwo_v8.plan_control_github import GitHubPlanRepository

    client = _RefContentClient()
    writer = client._writer_value("writer:one", status="draining")
    changed = writer["records"][-1]
    changed[field] = replacement
    changed["record_id"] = _r6_writer_record_id(changed)
    writer["current"]["record_id"] = changed["record_id"]
    client._commits[client.head][".gwo-v8/writer-transition.json"] = (
        client._content_type(canonical_bytes(writer), "blob:r6-substitution")
    )
    repository = GitHubPlanRepository(
        client,
        repository="owner/repository",
        branch="gwo-control",
        writer_generation="writer:one",
    )
    with pytest.raises(PlanControlError) as rejected:
        repository.active_receipt(CampaignHandle("owner/repository", "campaign:one"))
    assert rejected.value.code == "WRITER_FENCE_READBACK_INVALID"
    assert client.writes == []


def test_rc6_1_writer_lineage_table_accepts_rollback_then_fresh_recutover():
    from gwo_v8._canonical import canonical_bytes
    from gwo_v8.plan_control import CampaignHandle
    from gwo_v8.plan_control_github import GitHubPlanRepository

    client = _RefContentClient()
    initial = client._writer_value("writer:one")
    pending, cutover = initial["records"]
    drain = {
        **cutover,
        "kind": "drain",
        "status": "draining",
        "worker_capacity": 0,
        "coordinator_capacity": 0,
        "reason": "controlled rollback",
    }
    rollback = {
        **drain,
        "kind": "rollback",
        "status": "rolled_back",
        "previous_writer_generation": "writer:one",
        "writer_generation": "v6.1",
        "canary_evidence_digest": None,
    }
    fresh_pending = {
        **pending,
        "plan_digest": "c" * 64,
        "canary_evidence_digest": "d" * 64,
        "canary_evidence_refs": ["github://canary/recutover"],
        "canary_manifest_ref": "github://canary/recutover-manifest",
    }
    fresh_cutover = {
        **fresh_pending,
        "kind": "cutover",
        "status": "cut_over",
        "previous_writer_generation": "writer:one",
        "activation_id": "activation:recutover",
        "worker_capacity": 8,
        "coordinator_capacity": 1,
    }
    records = [pending, cutover, drain, rollback, fresh_pending, fresh_cutover]
    for record in records:
        record["record_id"] = _r6_writer_record_id(record)
    writer = {
        "schema_version": 1,
        "current": {
            "repository": "owner/repository",
            "writer_generation": "writer:one",
            "record_id": fresh_cutover["record_id"],
        },
        "records": records,
    }
    old_receipt = client._activation_value("writer:one")["receipts"][0]
    recutover_receipt = {
        **old_receipt,
        "activation_id": "activation:recutover",
        "plan_digest": "c" * 64,
        "expected_previous_digest": "a" * 64,
    }
    activation = {
        "schema_version": 1,
        "repository": "owner/repository",
        "active_plan_digest": "c" * 64,
        "receipts": [old_receipt, recutover_receipt],
    }
    client._commits[client.head][".gwo-v8/writer-transition.json"] = (
        client._content_type(canonical_bytes(writer), "blob:r6-lineage")
    )
    client._commits[client.head][".gwo/v8/active-plan.json"] = (
        client._content_type(canonical_bytes(activation), "blob:r6-activation")
    )
    repository = GitHubPlanRepository(
        client,
        repository="owner/repository",
        branch="gwo-control",
        writer_generation="writer:one",
    )
    assert repository.active_receipt(
        CampaignHandle("owner/repository", "campaign:one")
    ) is None


@pytest.mark.parametrize(
    ("status", "operation", "allowed"),
    (
        ("cut_over", "NEW_ATTEMPT", True),
        ("cut_over", "FIRST_PUBLICATION", True),
        ("draining", "NEW_ATTEMPT", False),
        ("draining", "FIRST_ACTIVATION", False),
        ("draining", "RECOVER_ATTEMPT", True),
        ("draining", "SEMANTIC_COMPLETION", True),
        ("draining", "FINALIZE_COMMITTED_CLAIMS", True),
        ("pending", "RECOVER_ACTIVATION", False),
        ("rolled_back", "RECOVER_ATTEMPT", False),
    ),
)
def test_rc6_2_writer_operation_matrix_is_closed(status, operation, allowed):
    from gwo_v8.plan_control import PlanControlError
    from gwo_v8.plan_control_github import GitHubPlanRepository, _WriterOperation

    repository = GitHubPlanRepository(
        _RefContentClient(),
        repository="owner/repository",
        branch="gwo-control",
        writer_generation="writer:one",
    )
    authority = {"status": status}
    if allowed:
        repository._assert_writer_operation(authority, _WriterOperation[operation])
    else:
        with pytest.raises(PlanControlError):
            repository._assert_writer_operation(authority, _WriterOperation[operation])


@pytest.mark.parametrize("tamper", ("extra", "missing", "stale", "cross_campaign"))
def test_rc6_3_active_envelope_rejects_complete_campaign_claim_ledger(
    tmp_path,
    tamper,
):
    from gwo_v8.plan_control import InMemoryPlanRepository, PlanControl, PlanControlError
    from gwo_v8.runtime_gateway import ArtifactStore

    artifacts = ArtifactStore(tmp_path / "artifacts")
    repository = InMemoryPlanRepository(writer_generation="writer:one")
    gateway = _PlanningGateway(artifacts)
    control = PlanControl(
        source=_PlanSource(), artifacts=artifacts, gateway=gateway, repository=repository
    )
    handle = control.start("owner/repository", ["issue:109"])
    active = repository.active_receipt(handle)
    assert active is not None
    if tamper == "extra":
        repository.claims[(handle.repository, "issue:110")] = active.revision_digest
        repository._claim_campaigns[(handle.repository, "issue:110")] = handle.campaign_key
    elif tamper == "missing":
        repository.claims.pop((handle.repository, "issue:109"))
        repository._claim_campaigns.pop((handle.repository, "issue:109"))
    elif tamper == "stale":
        repository.claims[(handle.repository, "issue:109")] = "f" * 64
    else:
        repository._claim_campaigns[(handle.repository, "issue:109")] = "campaign:other"
    before_claims = dict(repository.claims)
    before_campaigns = dict(repository._claim_campaigns)
    with pytest.raises(PlanControlError) as rejected:
        control.start("owner/repository", ["issue:109"])
    assert rejected.value.code == "ACTIVE_PLAN_CROSS_BINDING_INVALID"
    assert repository.claims == before_claims
    assert repository._claim_campaigns == before_campaigns
    assert gateway.progresses == 1


def test_rc6_4_target_hydration_stages_one_campaign_across_unrelated_ref_change(
    tmp_path,
):
    from gwo_v8.runtime_gateway import ArtifactStore

    client = _RefContentClient()
    original_artifacts = ArtifactStore(tmp_path / "original")
    control, repository, _gateway = _ref_control(client, original_artifacts)
    handle = control.start("owner/repository", ["issue:109"])
    active = repository.active_receipt(handle)
    assert active is not None
    fresh_root = tmp_path / "fresh"
    fresh_artifacts = ArtifactStore(fresh_root)
    observed = repository.observe_campaign(handle, active.revision_digest)
    original = repository._hydrate_repo_artifacts
    calls = 0

    def interleave(repo, artifacts, **kwargs):
        nonlocal calls
        original(repo, artifacts, **kwargs)
        calls += 1
        if calls == 1:
            # This is an unrelated control-ref file: target Campaign and
            # Writer identities stay exactly the same across the retry.
            assert not fresh_root.exists()
            tree = dict(client._commits[client.head])
            tree[".gwo-v8/unrelated-campaign-marker.json"] = client._content_type(
                b"{}", "blob:r6-unrelated"
            )
            client.head = f"commit:{len(client._commits) + 1}"
            client._commits[client.head] = tree

    repository._hydrate_repo_artifacts = interleave
    repository.hydrate_campaign_artifacts(fresh_artifacts, observed)
    assert calls == 2
    assert fresh_artifacts.get(active.revision_digest).digest == active.revision_digest


@pytest.mark.parametrize(
    "tamper",
    (
        "foreign_repository",
        "number",
        "labels",
        "comments",
        "state",
        "source_digest",
    ),
)
def test_rc6_5_ticket_contract_matrix_fails_before_preflight(tmp_path, tamper):
    from gwo_v8.plan_control import InMemoryPlanRepository, PlanControl, PlanControlError
    from gwo_v8.runtime_gateway import ArtifactStore

    class TamperedSource(_PlanSource):
        def snapshot(self, repository, refs):
            value = super().snapshot(repository, refs)
            ticket = value["tickets"][0]
            contract = ticket["contract"]
            if tamper == "foreign_repository":
                contract["repository"]["full_name"] = "other/repository"
            elif tamper == "number":
                contract["number"] = 110
            elif tamper == "labels":
                ticket["labels"] = ["needs-triage"]
            elif tamper == "comments":
                contract["comments"] = [{"id": 1}]
            elif tamper == "state":
                contract["state"] = "closed"
            else:
                ticket["source"]["digest"] = "f" * 64
            return value

    artifacts = ArtifactStore(tmp_path / "artifacts")
    repository = InMemoryPlanRepository(writer_generation="writer:one")
    gateway = _PlanningGateway(artifacts)
    with pytest.raises(PlanControlError):
        PlanControl(
            source=TamperedSource(),
            artifacts=artifacts,
            gateway=gateway,
            repository=repository,
        ).start("owner/repository", ["issue:109"])
    assert gateway.preflights == 0 and gateway.progresses == 0
    assert not repository.attempts and not repository.revisions and not repository.claims


def test_rc6_6_successor_does_not_reparse_an_old_ticket_override(tmp_path):
    from gwo_v8.plan_control import InMemoryPlanRepository
    from gwo_v8.plan_control_host import ProductionPlanControlStartHost
    from gwo_v8.runtime_gateway import ProfileMapping, RuntimeConfiguration
    from gwo_v8.runtime_profile import RuntimeProfile
    from gwo_v8.plan_control import frozen_ticket_contract_digest

    class ChangedTicketSource(_PlanSource):
        def snapshot(self, repository, refs):
            value = super().snapshot(repository, ("issue:109",))
            key = refs[0]
            number = int(key.removeprefix("issue:"))
            contract = _contract(number=number, body=f"Contract {number}")
            value["tickets"] = [{
                "key": key,
                "labels": ["ready-for-agent"],
                "source": {
                    "ref": key,
                    "digest": frozen_ticket_contract_digest(
                        key=key,
                        contract=contract,
                        labels=["ready-for-agent"],
                        native_blockers=[],
                    ),
                },
                "contract": contract,
                "native_blockers": [],
            }]
            return value

    class SelectedGateway(_PlanningGateway):
        def progress(self, subject, preflight):
            from gwo_v8.runtime_gateway import PlanningReceipt

            self.progresses += 1
            keys = [
                item["key"]
                for item in self.artifacts.read_json(subject.snapshot_artifact_digest)["tickets"]
            ]
            output = self.artifacts.put_canonical({
                "schema_version": "gwo.runtime.output.v1",
                "subject_digest": subject.digest,
                "stable_action_id": subject.stable_action_id,
                "authority_digest": subject.authority_digest,
                "payload": {
                    "admitted_work": keys,
                    "dependency_additions": [],
                    "exclusive_resources": {key: [] for key in keys},
                    "capability_requirements": {key: ["git"] for key in keys},
                    "decision_requirements": [],
                },
            })
            return PlanningReceipt(
                subject_digest=subject.digest,
                stable_action_id=subject.stable_action_id,
                status="completed",
                receipt_digest="4" * 64,
                output_artifact_digest=output.digest,
                planning_output_artifact_digest=output.digest,
            )

    profile = RuntimeProfile(
        name="host", provider="test", model="model:test", thinking="high", mode="safe", features={}
    )
    repository = InMemoryPlanRepository(writer_generation="writer:one")
    host = ProductionPlanControlStartHost(
        source=ChangedTicketSource(),
        repository=repository,
        runtime_configuration=RuntimeConfiguration(
            profiles={profile.digest: profile},
            host_mappings={"coordinator": ProfileMapping(profile.digest)},
        ),
        repository_contexts={},
        gateway_store_path=tmp_path / "gateway.json",
        artifact_root=tmp_path / "artifacts",
        _gateway_builder=lambda *, artifacts, **_kwargs: SelectedGateway(artifacts),
    )
    options = {
        "coordinator": None,
        "ticket_overrides": [{
            "ticket_key": "issue:109",
            "role": "worker",
            "mapping": {
                "primary_profile_digest": profile.digest,
                "availability_fallback_profile_digest": None,
            },
        }],
    }
    handle = host.start("owner/repository", ["issue:109"], options)
    previous = repository.active_receipt(handle).revision_digest
    assert host.start_successor(
        handle,
        ["issue:110"],
        expected_previous_revision_digest=previous,
    ) == handle
    assert not hasattr(repository, "runtime_assertions")


def test_r7c1_reservation_crash_retries_the_same_progress_operation(tmp_path):
    """A reservation proves identity, not prior Runtime materialization."""

    from gwo_v8.plan_control import InMemoryPlanRepository, PlanControl
    from gwo_v8.runtime_gateway import ArtifactStore

    class CrashBeforeRuntimeAction(_PlanningGateway):
        def __init__(self, artifacts):
            super().__init__(artifacts)
            self.progress_invocations = 0

        def progress(self, subject, preflight):
            self.progress_invocations += 1
            if self.progress_invocations == 1:
                raise RuntimeError("crash after PlanControl reservation")
            return super().progress(subject, preflight)

    artifacts = ArtifactStore(tmp_path / "artifacts")
    repository = InMemoryPlanRepository(writer_generation="writer:one")
    gateway = CrashBeforeRuntimeAction(artifacts)
    control = PlanControl(
        source=_PlanSource(),
        artifacts=artifacts,
        gateway=gateway,
        repository=repository,
    )

    with pytest.raises(RuntimeError, match="after PlanControl reservation"):
        control.start("owner/repository", ["issue:109"])

    handle = control.start("owner/repository", ["issue:109"])

    assert repository.active_receipt(handle) is not None
    assert gateway.progress_invocations == 2
    assert gateway.progresses == 1


@pytest.mark.parametrize("status", ("cut_over", "draining"))
def test_r7c1_github_writer_policy_exposes_only_closed_progress_modes(status):
    """The installed host receives only the authoritative Writer mode."""

    from gwo_v8.plan_control_github import GitHubPlanRepository
    from gwo_v8.runtime_gateway import CampaignPlanningSubject

    client = _RefContentClient()
    if status == "draining":
        client.advance_writer("writer:one", status=status)
    repository = GitHubPlanRepository(
        client,
        repository="owner/repository",
        branch="gwo-control",
        writer_generation="writer:one",
    )
    subject = CampaignPlanningSubject(
        repository="owner/repository",
        campaign_key="campaign:r7-policy",
        campaign_handle="campaign-handle:r7-policy",
        expected_previous_plan_revision_digest=None,
        snapshot_artifact_digest="a" * 64,
        policy_witness_digest="b" * 64,
        planning_request_artifact_digest="c" * 64,
        stable_action_id="planning:r7-policy",
    )

    assert repository.planning_progress_mode(subject) == status


@pytest.mark.parametrize("initial_state", ("absent", "prepared"))
def test_r9_writer_drain_before_active_dispatch_cas_has_zero_effects(
    initial_state,
    tmp_path,
):
    """A pre-existing drain rejects either provider-effect boundary."""

    from gwo_v8.plan_control_github import GitHubPlanRepository
    from gwo_v8.runtime_gateway import RuntimeGatewayError

    client = _RefContentClient()
    client.advance_writer("writer:one", status="draining")
    repository = GitHubPlanRepository(
        client,
        repository="owner/repository",
        branch="gwo-control",
        writer_generation="writer:one",
    )
    gateway, subject, preflight, adapter = _r8_authorized_runtime_gateway(
        tmp_path,
        repository,
        initial_state,
    )
    before = (
        len(adapter.prepare_calls),
        adapter.created_agent_count,
        len(adapter.command_calls),
    )

    with pytest.raises(RuntimeGatewayError) as rejected:
        gateway.progress(subject, preflight)

    assert rejected.value.code == "RUNTIME_RECOVERY_ONLY"
    assert (
        len(adapter.prepare_calls),
        adapter.created_agent_count,
        len(adapter.command_calls),
    ) == before


@pytest.mark.parametrize("boundary", ("prepare", "start"))
def test_r9_dispatch_ticket_is_exact_and_draining_rejects_replay(
    boundary,
):
    """One active ticket is exact; a later drain admits no replay."""

    from dataclasses import replace

    from gwo_v8.plan_control_github import GitHubPlanRepository
    from gwo_v8.runtime_gateway import CampaignPlanningSubject

    client = _RefContentClient()
    repository = GitHubPlanRepository(
        client,
        repository="owner/repository",
        branch="gwo-control",
        writer_generation="writer:one",
    )
    subject = CampaignPlanningSubject(
        repository="owner/repository",
        campaign_key="campaign:r8-replay",
        campaign_handle="campaign-handle:r8-replay",
        expected_previous_plan_revision_digest=None,
        snapshot_artifact_digest="a" * 64,
        policy_witness_digest="b" * 64,
        planning_request_artifact_digest="c" * 64,
        stable_action_id="planning:r8-replay",
    )

    dispatch = repository.planning_effect_dispatch()
    ticket = dispatch.enter(subject, boundary)
    assert type(ticket) is str and ticket
    assert dispatch.enter(subject, boundary) == ticket
    assert dispatch.enter(
        replace(subject, campaign_key="campaign:r8-other"), boundary
    ) is None
    assert dispatch.enter(
        subject, "start" if boundary == "prepare" else "prepare"
    ) is None
    dispatch.resolve(subject, boundary, ticket)
    assert dispatch.enter(
        replace(subject, campaign_key="campaign:r8-other"), boundary
    ) is None
    client.advance_writer("writer:one", status="draining")
    restarted = GitHubPlanRepository(
        client,
        repository="owner/repository",
        branch="gwo-control",
        writer_generation="writer:one",
    )

    recovered_dispatch = restarted.planning_effect_dispatch()
    assert recovered_dispatch.enter(subject, boundary) is None
    assert recovered_dispatch.enter(
        subject, "start" if boundary == "prepare" else "prepare"
    ) is None
    assert recovered_dispatch.enter(
        replace(subject, campaign_key="campaign:r8-other"), boundary
    ) is None


def test_r10_foreign_campaign_reconcile_cannot_resolve_active_dispatch():
    """Only the exact Campaign can turn its active ticket into recovery."""

    from dataclasses import replace

    from gwo_v8.plan_control_github import GitHubPlanRepository
    from gwo_v8.runtime_gateway import CampaignPlanningSubject
    from gwo_v8.transition import (
        GitHubWriterTransitionControl,
        WriterTransitionBlocked,
        _record,
    )

    client = _RefContentClient()
    repository = GitHubPlanRepository(
        client,
        repository="owner/repository",
        branch="gwo-control",
        writer_generation="writer:one",
    )
    subject_a = CampaignPlanningSubject(
        repository="owner/repository",
        campaign_key="campaign:r10-owner",
        campaign_handle="campaign-handle:r10-owner",
        expected_previous_plan_revision_digest=None,
        snapshot_artifact_digest="a" * 64,
        policy_witness_digest="b" * 64,
        planning_request_artifact_digest="c" * 64,
        stable_action_id="planning:r10-shared-action",
    )
    subject_b = replace(
        subject_a,
        campaign_key="campaign:r10-foreign",
        campaign_handle="campaign-handle:r10-foreign",
    )
    dispatch = repository.planning_effect_dispatch()
    assert dispatch.enter(subject_a, "prepare").startswith("planning-dispatch:")
    transitions = GitHubWriterTransitionControl(
        client,
        branch="gwo-control",
        initial_writer="writer:one",
    )
    drain = _record(
        repository="owner/repository",
        kind="drain",
        status="draining",
        previous_writer_generation="writer:one",
        writer_generation="writer:one",
        activation_id="activation:cutover",
        plan_digest="a" * 64,
        canary_evidence_digest="b" * 64,
        canary_evidence_refs=("github://canary/evidence",),
        canary_manifest_ref="github://canary/manifest",
        worker_capacity=0,
        coordinator_capacity=0,
        reason="r10 foreign reconcile",
    )

    dispatch.reconcile(subject_b, ("prepare",))

    with pytest.raises(WriterTransitionBlocked) as blocked:
        transitions.publish(drain)
    assert blocked.value.code == "WRITER_DRAIN_DISPATCH_ACTIVE"

    dispatch.reconcile(subject_a, ("prepare",))
    transitions.publish(drain)
    assert repository.planning_progress_mode(subject_a) == "draining"


@pytest.mark.parametrize(
    ("foreign", "boundary", "ticket_suffix"),
    (
        ("campaign", "prepare", ""),
        ("action", "prepare", ""),
        ("boundary", "start", ""),
        ("ticket", "prepare", ":foreign"),
    ),
)
def test_r10_dispatch_resolution_requires_exact_subject_boundary_and_ticket(
    foreign,
    boundary,
    ticket_suffix,
):
    """Foreign resolution leaves the owner active and Writer-drain blocked."""

    from dataclasses import replace

    from gwo_v8.plan_control import PlanControlError
    from gwo_v8.plan_control_github import GitHubPlanRepository
    from gwo_v8.runtime_gateway import CampaignPlanningSubject
    from gwo_v8.transition import (
        GitHubWriterTransitionControl,
        WriterTransitionBlocked,
        _record,
    )

    client = _RefContentClient()
    repository = GitHubPlanRepository(
        client,
        repository="owner/repository",
        branch="gwo-control",
        writer_generation="writer:one",
    )
    owner = CampaignPlanningSubject(
        repository="owner/repository",
        campaign_key="campaign:r10-resolution-owner",
        campaign_handle="campaign-handle:r10-resolution-owner",
        expected_previous_plan_revision_digest=None,
        snapshot_artifact_digest="a" * 64,
        policy_witness_digest="b" * 64,
        planning_request_artifact_digest="c" * 64,
        stable_action_id="planning:r10-resolution-owner",
    )
    subject = (
        replace(
            owner,
            campaign_key="campaign:r10-resolution-foreign",
            campaign_handle="campaign-handle:r10-resolution-foreign",
        )
        if foreign == "campaign"
        else replace(owner, stable_action_id="planning:r10-resolution-foreign")
        if foreign == "action"
        else owner
    )
    dispatch = repository.planning_effect_dispatch()
    ticket = dispatch.enter(owner, "prepare")
    assert type(ticket) is str
    transitions = GitHubWriterTransitionControl(
        client,
        branch="gwo-control",
        initial_writer="writer:one",
    )
    drain = _record(
        repository="owner/repository",
        kind="drain",
        status="draining",
        previous_writer_generation="writer:one",
        writer_generation="writer:one",
        activation_id="activation:cutover",
        plan_digest="a" * 64,
        canary_evidence_digest="b" * 64,
        canary_evidence_refs=("github://canary/evidence",),
        canary_manifest_ref="github://canary/manifest",
        worker_capacity=0,
        coordinator_capacity=0,
        reason="r10 exact resolution",
    )

    with pytest.raises(PlanControlError) as rejected:
        dispatch.resolve(subject, boundary, ticket + ticket_suffix)
    assert rejected.value.code == "WRITER_FENCE_READBACK_INVALID"
    with pytest.raises(WriterTransitionBlocked) as blocked:
        transitions.publish(drain)
    assert blocked.value.code == "WRITER_DRAIN_DISPATCH_ACTIVE"

    dispatch.resolve(owner, "prepare", ticket)
    transitions.publish(drain)
    assert repository.planning_progress_mode(owner) == "draining"


def _r10_dispatch_subject(ordinal, *, repository="owner/repository"):
    from gwo_v8.runtime_gateway import CampaignPlanningSubject

    return CampaignPlanningSubject(
        repository=repository,
        campaign_key=f"campaign:r10-ledger:{ordinal}",
        campaign_handle=f"campaign-handle:r10-ledger:{ordinal}",
        expected_previous_plan_revision_digest=None,
        snapshot_artifact_digest="a" * 64,
        policy_witness_digest="b" * 64,
        planning_request_artifact_digest="c" * 64,
        stable_action_id=f"planning:r10-ledger:{ordinal}",
    )


def _r10_dispatch_entry(client, subject, *, state="recovery", attempt=1):
    from gwo_v8._canonical import load_canonical_json
    from gwo_v8.transition import _planning_effect_dispatch_ticket

    writer = load_canonical_json(
        client._commits[client.head][".gwo-v8/writer-transition.json"].content
    )
    entry = {
        "repository": subject.repository,
        "campaign_key": subject.campaign_key,
        "campaign_handle": subject.campaign_handle,
        "subject_digest": subject.digest,
        "stable_action_id": subject.stable_action_id,
        "effect_boundary": "prepare",
        "writer_generation": "writer:one",
        "writer_cut_over_record_id": writer["current"]["record_id"],
        "writer_observation_ref": client.head,
        "attempt": attempt,
        "state": state,
    }
    return {**entry, "ticket": _planning_effect_dispatch_ticket(entry)}


def _r10_install_dispatch_entries(
    client,
    entries,
    *,
    repository="owner/repository",
):
    from gwo_v8._canonical import canonical_bytes
    from gwo_v8.transition import _PLANNING_EFFECT_DISPATCH_PATH, _PLANNING_EFFECT_DISPATCH_SCHEMA

    tree = dict(client._commits[client.head])
    tree[_PLANNING_EFFECT_DISPATCH_PATH] = client._content_type(
        canonical_bytes(
            {
                "schema_version": _PLANNING_EFFECT_DISPATCH_SCHEMA,
                "repository": repository,
                "entries": entries,
            }
        ),
        "blob:r10-dispatch-ledger",
    )
    client.head = f"commit:{len(client._commits) + 1}"
    client._commits[client.head] = tree


def _r10_drain_record(reason, *, repository="owner/repository"):
    from gwo_v8.transition import _record

    return _record(
        repository=repository,
        kind="drain",
        status="draining",
        previous_writer_generation="writer:one",
        writer_generation="writer:one",
        activation_id="activation:cutover",
        plan_digest="a" * 64,
        canary_evidence_digest="b" * 64,
        canary_evidence_refs=("github://canary/evidence",),
        canary_manifest_ref="github://canary/manifest",
        worker_capacity=0,
        coordinator_capacity=0,
        reason=reason,
    )


def test_r10_recovery_compaction_is_bounded_stable_and_retains_exact_retry():
    """The sixteenth recovery is compacted by immutable identity, not history order."""

    from gwo_v8._canonical import load_canonical_json
    from gwo_v8.plan_control_github import GitHubPlanRepository
    from gwo_v8.transition import _planning_effect_dispatch_entry_order

    client = _RefContentClient()
    repository = GitHubPlanRepository(
        client,
        repository="owner/repository",
        branch="gwo-control",
        writer_generation="writer:one",
    )
    dispatch = repository.planning_effect_dispatch()
    for ordinal in range(16):
        subject = _r10_dispatch_subject(ordinal)
        ticket = dispatch.enter(subject, "prepare")
        dispatch.resolve(subject, "prepare", ticket)

    new_subject = _r10_dispatch_subject(16)
    ticket = dispatch.enter(new_subject, "prepare")
    ledger = load_canonical_json(
        client._commits[client.head][
            ".gwo-v8/planning-effect-dispatch-v1.json"
        ].content
    )

    assert len(ledger["entries"]) == 16
    assert [entry["campaign_key"] for entry in ledger["entries"]] == sorted(
        entry["campaign_key"] for entry in ledger["entries"]
    )
    assert "campaign:r10-ledger:0" not in {
        entry["campaign_key"] for entry in ledger["entries"]
    }
    assert "campaign:r10-ledger:15" in {
        entry["campaign_key"] for entry in ledger["entries"]
    }
    assert ledger["entries"] == sorted(
        ledger["entries"],
        key=_planning_effect_dispatch_entry_order,
    )

    dispatch.resolve(new_subject, "prepare", ticket)
    retry = dispatch.enter(_r10_dispatch_subject(15), "prepare")
    assert retry.startswith("planning-dispatch:") and retry != ticket


@pytest.mark.parametrize("kind", ("entry_count", "text", "canonical_bytes", "attempt"))
def test_r10_writer_and_plan_owner_reject_the_same_invalid_dispatch_ledger(kind):
    """The shared parser fails closed before either admission or drain CAS."""

    from gwo_v8.plan_control import PlanControlError
    from gwo_v8.plan_control_github import GitHubPlanRepository
    from gwo_v8.transition import (
        GitHubWriterTransitionControl,
        WriterTransitionBlocked,
        _planning_effect_dispatch_ticket,
    )

    client = _RefContentClient()
    subject = _r10_dispatch_subject(0)
    entries = [_r10_dispatch_entry(client, subject)]
    if kind == "entry_count":
        entries = [
            _r10_dispatch_entry(client, _r10_dispatch_subject(ordinal))
            for ordinal in range(17)
        ]
    elif kind == "text":
        entries[0]["campaign_handle"] = "x" * 257
        entries[0]["ticket"] = _planning_effect_dispatch_ticket(entries[0])
    elif kind == "canonical_bytes":
        from gwo_v8._canonical import canonical_bytes
        from gwo_v8.transition import (
            _PLANNING_EFFECT_DISPATCH_MAX_CANONICAL_BYTES,
            _PLANNING_EFFECT_DISPATCH_SCHEMA,
        )

        entries = [
            _r10_dispatch_entry(client, _r10_dispatch_subject(ordinal))
            for ordinal in range(16)
        ]
        for entry in entries:
            entry["campaign_key"] = entry["campaign_key"].ljust(256, "k")
            entry["campaign_handle"] = entry["campaign_handle"].ljust(256, "h")
            entry["stable_action_id"] = entry["stable_action_id"].ljust(256, "a")
            entry["writer_generation"] = entry["writer_generation"].ljust(256, "g")
            entry["writer_cut_over_record_id"] = entry[
                "writer_cut_over_record_id"
            ].ljust(256, "r")
            entry["writer_observation_ref"] = entry[
                "writer_observation_ref"
            ].ljust(256, "o")
            entry["ticket"] = _planning_effect_dispatch_ticket(entry)
        assert len(
            canonical_bytes(
                {
                    "schema_version": _PLANNING_EFFECT_DISPATCH_SCHEMA,
                    "repository": "owner/repository",
                    "entries": entries,
                }
            )
        ) > _PLANNING_EFFECT_DISPATCH_MAX_CANONICAL_BYTES
    else:
        entries[0]["attempt"] = 17
        entries[0]["ticket"] = _planning_effect_dispatch_ticket(entries[0])
    _r10_install_dispatch_entries(client, entries)
    repository = GitHubPlanRepository(
        client,
        repository="owner/repository",
        branch="gwo-control",
        writer_generation="writer:one",
    )
    transitions = GitHubWriterTransitionControl(
        client,
        branch="gwo-control",
        initial_writer="writer:one",
    )

    with pytest.raises(PlanControlError) as rejected:
        repository.planning_effect_dispatch().enter(subject, "prepare")
    assert rejected.value.code == "WRITER_FENCE_READBACK_INVALID"
    with pytest.raises(WriterTransitionBlocked) as blocked:
        transitions.publish(_r10_drain_record(f"r10 invalid {kind}"))
    assert blocked.value.code == "WRITER_DRAIN_DISPATCH_INVALID"
    assert client.writes == []


def test_r10_canonical_byte_limit_accepts_exactly_one_last_byte_and_no_more():
    """The Writer parser accepts the exact canonical limit and rejects +1."""

    from copy import deepcopy

    from gwo_v8._canonical import canonical_bytes
    from gwo_v8.plan_control import PlanControlError
    from gwo_v8.plan_control_github import GitHubPlanRepository
    from gwo_v8.transition import (
        GitHubWriterTransitionControl,
        WriterTransitionBlocked,
        _PLANNING_EFFECT_DISPATCH_MAX_CANONICAL_BYTES,
        _PLANNING_EFFECT_DISPATCH_MAX_TEXT_BYTES,
        _PLANNING_EFFECT_DISPATCH_SCHEMA,
        _planning_effect_dispatch_ticket,
    )

    def ledger_bytes(entries):
        return canonical_bytes(
            {
                "schema_version": _PLANNING_EFFECT_DISPATCH_SCHEMA,
                "repository": "owner/repository",
                "entries": entries,
            }
        )

    client = _RefContentClient()
    entries = [
        _r10_dispatch_entry(client, _r10_dispatch_subject(ordinal))
        for ordinal in range(16)
    ]
    fields = (
        "campaign_key",
        "campaign_handle",
        "stable_action_id",
        "writer_generation",
        "writer_cut_over_record_id",
        "writer_observation_ref",
    )
    for entry in entries:
        for field in fields:
            while len(entry[field].encode("utf-8")) < _PLANNING_EFFECT_DISPATCH_MAX_TEXT_BYTES:
                entry[field] += "x"
                entry["ticket"] = _planning_effect_dispatch_ticket(entry)
                if len(ledger_bytes(entries)) == _PLANNING_EFFECT_DISPATCH_MAX_CANONICAL_BYTES:
                    break
            if len(ledger_bytes(entries)) == _PLANNING_EFFECT_DISPATCH_MAX_CANONICAL_BYTES:
                break
        if len(ledger_bytes(entries)) == _PLANNING_EFFECT_DISPATCH_MAX_CANONICAL_BYTES:
            break
    assert len(ledger_bytes(entries)) == _PLANNING_EFFECT_DISPATCH_MAX_CANONICAL_BYTES

    _r10_install_dispatch_entries(client, entries)
    transitions = GitHubWriterTransitionControl(
        client,
        branch="gwo-control",
        initial_writer="writer:one",
    )
    transitions.publish(_r10_drain_record("r10 exact canonical bytes"))

    overflow_client = _RefContentClient()
    overflow_entries = deepcopy(entries)
    expanded = False
    for entry in overflow_entries:
        for field in fields:
            if len(entry[field].encode("utf-8")) < _PLANNING_EFFECT_DISPATCH_MAX_TEXT_BYTES:
                entry[field] += "x"
                entry["ticket"] = _planning_effect_dispatch_ticket(entry)
                expanded = True
                break
        if expanded:
            break
    assert expanded
    assert len(ledger_bytes(overflow_entries)) == (
        _PLANNING_EFFECT_DISPATCH_MAX_CANONICAL_BYTES + 1
    )
    _r10_install_dispatch_entries(overflow_client, overflow_entries)
    overflow_repository = GitHubPlanRepository(
        overflow_client,
        repository="owner/repository",
        branch="gwo-control",
        writer_generation="writer:one",
    )
    with pytest.raises(PlanControlError) as rejected:
        overflow_repository.planning_effect_dispatch().enter(
            _r10_dispatch_subject(0),
            "prepare",
        )
    assert rejected.value.code == "WRITER_FENCE_READBACK_INVALID"
    overflow_transitions = GitHubWriterTransitionControl(
        overflow_client,
        branch="gwo-control",
        initial_writer="writer:one",
    )
    with pytest.raises(WriterTransitionBlocked) as blocked:
        overflow_transitions.publish(_r10_drain_record("r10 overflow bytes"))
    assert blocked.value.code == "WRITER_DRAIN_DISPATCH_INVALID"


def test_r10_maximum_attempt_is_valid_but_the_next_exact_retry_is_bounded():
    """Attempt sixteen parses for Writer recovery; attempt seventeen cannot enter."""

    from gwo_v8.plan_control import PlanControlError
    from gwo_v8.plan_control_github import GitHubPlanRepository
    from gwo_v8.transition import GitHubWriterTransitionControl

    client = _RefContentClient()
    subject = _r10_dispatch_subject(0)
    _r10_install_dispatch_entries(
        client,
        [_r10_dispatch_entry(client, subject, attempt=16)],
    )
    repository = GitHubPlanRepository(
        client,
        repository="owner/repository",
        branch="gwo-control",
        writer_generation="writer:one",
    )
    with pytest.raises(PlanControlError) as rejected:
        repository.planning_effect_dispatch().enter(subject, "prepare")
    assert rejected.value.code == "PLANNING_EFFECT_DISPATCH_BOUNDED"

    transitions = GitHubWriterTransitionControl(
        client,
        branch="gwo-control",
        initial_writer="writer:one",
    )
    transitions.publish(_r10_drain_record("r10 maximum attempt"))
    assert repository.planning_progress_mode(subject) == "draining"


def test_r10_active_dispatch_saturation_fails_closed_before_writer_effect(tmp_path):
    """The fixed active-ticket budget leaves the Writer cut-over but blocked."""

    from gwo_v8.plan_control import PlanControlError
    from gwo_v8.plan_control_github import GitHubPlanRepository
    from gwo_v8.runtime_gateway import (
        CampaignPlanningSubject,
        RuntimeGatewayError,
    )
    from gwo_v8.transition import (
        GitHubWriterTransitionControl,
        WriterTransitionBlocked,
        _record,
    )

    client = _RefContentClient()
    repository = GitHubPlanRepository(
        client,
        repository="owner/repository",
        branch="gwo-control",
        writer_generation="writer:one",
    )
    dispatch = repository.planning_effect_dispatch()

    def subject(ordinal):
        return CampaignPlanningSubject(
            repository="owner/repository",
            campaign_key=f"campaign:r10-active:{ordinal}",
            campaign_handle=f"campaign-handle:r10-active:{ordinal}",
            expected_previous_plan_revision_digest=None,
            snapshot_artifact_digest="a" * 64,
            policy_witness_digest="b" * 64,
            planning_request_artifact_digest="c" * 64,
            stable_action_id=f"planning:r10-active:{ordinal}",
        )

    for ordinal in range(8):
        assert dispatch.enter(subject(ordinal), "prepare").startswith(
            "planning-dispatch:"
        )
    with pytest.raises(PlanControlError) as rejected:
        dispatch.enter(subject(8), "prepare")
    assert rejected.value.code == "PLANNING_EFFECT_DISPATCH_BOUNDED"
    gateway, planning_subject, preflight, adapter = _r8_authorized_runtime_gateway(
        tmp_path,
        repository,
        "absent",
    )
    before = (
        tuple(adapter.prepare_calls),
        adapter.created_agent_count,
        tuple(adapter.command_calls),
    )
    with pytest.raises(RuntimeGatewayError) as gateway_rejected:
        gateway.progress(planning_subject, preflight)
    assert gateway_rejected.value.code == "RUNTIME_PLANNING_DISPATCH_BOUNDED"
    assert (
        tuple(adapter.prepare_calls),
        adapter.created_agent_count,
        tuple(adapter.command_calls),
    ) == before

    transitions = GitHubWriterTransitionControl(
        client,
        branch="gwo-control",
        initial_writer="writer:one",
    )
    drain = _record(
        repository="owner/repository",
        kind="drain",
        status="draining",
        previous_writer_generation="writer:one",
        writer_generation="writer:one",
        activation_id="activation:cutover",
        plan_digest="a" * 64,
        canary_evidence_digest="b" * 64,
        canary_evidence_refs=("github://canary/evidence",),
        canary_manifest_ref="github://canary/manifest",
        worker_capacity=0,
        coordinator_capacity=0,
        reason="r10 active saturation",
    )
    with pytest.raises(WriterTransitionBlocked) as blocked:
        transitions.publish(drain)
    assert blocked.value.code == "WRITER_DRAIN_DISPATCH_ACTIVE"


def _r8_authorized_runtime_gateway(tmp_path, repository, initial_state):
    """Build one real Gateway plus the Git-backed Writer effect gate."""

    from dataclasses import replace

    from gwo_v8.planning_protocol import planning_prompt
    from gwo_v8.runtime_gateway import (
        ArtifactStore,
        CampaignPlanningSubject,
        ProfileMapping,
        RuntimeConfiguration,
        RuntimeGateway,
        _InMemoryRuntimeProviderAdapter,
        _RuntimeActionSpec,
        _RuntimeFailure,
    )
    from gwo_v8.runtime_profile import RuntimeProfile

    store = ArtifactStore(tmp_path / "artifacts")
    snapshot = store.put_canonical({"tickets": [{"key": "issue:109"}]})
    policy = store.put_canonical({"policy": "frozen"})
    unsigned = CampaignPlanningSubject(
        repository="owner/repository",
        campaign_key="campaign:r8-dispatch",
        campaign_handle="campaign-handle:r8-dispatch",
        expected_previous_plan_revision_digest=None,
        snapshot_artifact_digest=snapshot.digest,
        policy_witness_digest=policy.digest,
        planning_request_artifact_digest="0" * 64,
        stable_action_id="planning:r8-dispatch",
    )
    prompt = store.put_canonical(
        planning_prompt(
            subject_digest=unsigned.prompt_binding_digest,
            authority_digest=unsigned.authority_digest,
            snapshot_artifact_digest=snapshot.digest,
            policy_witness_artifact_digest=policy.digest,
        )
    )
    subject = replace(unsigned, planning_request_artifact_digest=prompt.digest)
    profile = RuntimeProfile(
        name="coordinator",
        provider="test",
        model="test-model",
        thinking="high",
        mode="safe",
        features={},
    )
    adapter = _InMemoryRuntimeProviderAdapter(store)
    gateway = RuntimeGateway(
        store_path=tmp_path / "gateway.json",
        _adapter=adapter,
        configuration=RuntimeConfiguration(
            profiles={profile.digest: profile},
            host_mappings={"coordinator": ProfileMapping(profile.digest)},
        ),
        _artifacts=store,
        _planning_effect_dispatch=repository.planning_effect_dispatch(),
    )
    preflight = gateway.planning_preflight(subject)
    if initial_state == "prepared":
        record = gateway._assignment_for_progress(
            subject, gateway._data["preflights"][subject.stable_action_id]
        )
        prompt_artifact, input_artifacts = gateway._resolve_input_artifacts(subject)
        prepared = adapter.prepare(
            _RuntimeActionSpec(
                subject.stable_action_id,
                subject,
                gateway._profile(record["profile_digest"]),
                prompt_artifact,
                input_artifacts,
            )
        )
        assert not isinstance(prepared, _RuntimeFailure)
    return gateway, subject, preflight, adapter


@pytest.mark.parametrize("initial_state", ("absent", "prepared"))
def test_r9_active_dispatch_rejects_writer_drain_before_provider_effect(
    initial_state,
    tmp_path,
):
    """A Writer drain cannot commit after the active dispatch CAS wins."""

    from gwo_v8.plan_control_github import GitHubPlanRepository
    from gwo_v8.transition import (
        GitHubWriterTransitionControl,
        WriterTransitionBlocked,
        _record,
    )

    client = _RefContentClient()
    repository = GitHubPlanRepository(
        client,
        repository="owner/repository",
        branch="gwo-control",
        writer_generation="writer:one",
    )
    gateway, subject, preflight, adapter = _r8_authorized_runtime_gateway(
        tmp_path,
        repository,
        initial_state,
    )
    transitions = GitHubWriterTransitionControl(
        client,
        branch="gwo-control",
        initial_writer="writer:one",
    )
    drain = _record(
        repository="owner/repository",
        kind="drain",
        status="draining",
        previous_writer_generation="writer:one",
        writer_generation="writer:one",
        activation_id="activation:cutover",
        plan_digest="a" * 64,
        canary_evidence_digest="b" * 64,
        canary_evidence_refs=("github://canary/evidence",),
        canary_manifest_ref="github://canary/manifest",
        worker_capacity=0,
        coordinator_capacity=0,
        reason="r9 dispatch race",
    )
    drain_blockers = []

    def attempt_drain():
        with pytest.raises(WriterTransitionBlocked) as rejected:
            transitions.publish(drain)
        drain_blockers.append(rejected.value.code)

    if initial_state == "absent":
        prepare = adapter.prepare

        def prepare_after_dispatch(spec):
            attempt_drain()
            return prepare(spec)

        adapter.prepare = prepare_after_dispatch
    else:
        command = adapter.command

        def command_after_dispatch(stable_action_id, transition):
            attempt_drain()
            return command(stable_action_id, transition)

        adapter.command = command_after_dispatch

    receipt = gateway.progress(subject, preflight)

    assert receipt.status == "completed"
    assert drain_blockers == ["WRITER_DRAIN_DISPATCH_ACTIVE"]
    assert repository.planning_progress_mode(subject) == "cut_over"
    assert ".gwo-v8/planning-effect-dispatch-v1.json" in client._commits[
        client.head
    ]
    if initial_state == "absent":
        assert adapter.prepare_calls == [subject.stable_action_id]
    else:
        assert adapter.created_agent_count == 1
        assert adapter.command_calls == [(subject.stable_action_id, "start")]
    transitions.publish(drain)
    assert repository.planning_progress_mode(subject) == "draining"


def test_r9_writer_snapshot_losing_to_active_dispatch_is_a_typed_blocker():
    """A drain CAS race re-reads the winning active dispatch, never commits."""

    from gwo_v8.plan_control_github import GitHubPlanRepository
    from gwo_v8.runtime_gateway import CampaignPlanningSubject
    from gwo_v8.transition import (
        GitHubWriterTransitionControl,
        WriterTransitionBlocked,
        _record,
    )

    client = _RefContentClient()
    repository = GitHubPlanRepository(
        client,
        repository="owner/repository",
        branch="gwo-control",
        writer_generation="writer:one",
    )
    subject = CampaignPlanningSubject(
        repository="owner/repository",
        campaign_key="campaign:r9-snapshot-race",
        campaign_handle="campaign-handle:r9-snapshot-race",
        expected_previous_plan_revision_digest=None,
        snapshot_artifact_digest="a" * 64,
        policy_witness_digest="b" * 64,
        planning_request_artifact_digest="c" * 64,
        stable_action_id="planning:r9-snapshot-race",
    )
    dispatch = repository.planning_effect_dispatch()
    client.before_ref_cas = lambda: dispatch.enter(subject, "prepare")
    transitions = GitHubWriterTransitionControl(
        client,
        branch="gwo-control",
        initial_writer="writer:one",
    )
    drain = _record(
        repository="owner/repository",
        kind="drain",
        status="draining",
        previous_writer_generation="writer:one",
        writer_generation="writer:one",
        activation_id="activation:cutover",
        plan_digest="a" * 64,
        canary_evidence_digest="b" * 64,
        canary_evidence_refs=("github://canary/evidence",),
        canary_manifest_ref="github://canary/manifest",
        worker_capacity=0,
        coordinator_capacity=0,
        reason="r9 snapshot race",
    )

    with pytest.raises(WriterTransitionBlocked) as blocked:
        transitions.publish(drain)

    assert blocked.value.code == "WRITER_DRAIN_DISPATCH_ACTIVE"
    assert repository.planning_progress_mode(subject) == "cut_over"
    assert dispatch.enter(subject, "prepare").startswith("planning-dispatch:")


@pytest.mark.parametrize("initial_state", ("absent", "prepared"))
def test_r9_restart_after_active_dispatch_before_provider_call_recovers_once(
    initial_state,
    tmp_path,
):
    """A pre-call crash leaves one reusable active ticket, never a dead lock."""

    from gwo_v8.plan_control_github import GitHubPlanRepository
    from gwo_v8.runtime_gateway import RuntimeGateway
    from gwo_v8.transition import (
        GitHubWriterTransitionControl,
        WriterTransitionBlocked,
        _record,
    )

    client = _RefContentClient()
    repository = GitHubPlanRepository(
        client,
        repository="owner/repository",
        branch="gwo-control",
        writer_generation="writer:one",
    )
    gateway, subject, preflight, adapter = _r8_authorized_runtime_gateway(
        tmp_path,
        repository,
        initial_state,
    )
    transitions = GitHubWriterTransitionControl(
        client,
        branch="gwo-control",
        initial_writer="writer:one",
    )
    drain = _record(
        repository="owner/repository",
        kind="drain",
        status="draining",
        previous_writer_generation="writer:one",
        writer_generation="writer:one",
        activation_id="activation:cutover",
        plan_digest="a" * 64,
        canary_evidence_digest="b" * 64,
        canary_evidence_refs=("github://canary/evidence",),
        canary_manifest_ref="github://canary/manifest",
        worker_capacity=0,
        coordinator_capacity=0,
        reason="r9 pre-call crash",
    )
    if initial_state == "absent":
        adapter.prepare = lambda _spec: (_ for _ in ()).throw(KeyboardInterrupt())
    else:
        adapter.command = lambda _action, _transition: (
            _ for _ in ()
        ).throw(KeyboardInterrupt())

    with pytest.raises(KeyboardInterrupt):
        gateway.progress(subject, preflight)
    with pytest.raises(WriterTransitionBlocked) as blocked:
        transitions.publish(drain)

    assert blocked.value.code == "WRITER_DRAIN_DISPATCH_ACTIVE"
    if initial_state == "absent":
        assert adapter.prepare_calls == []
    else:
        assert adapter.created_agent_count == 0
        assert adapter.command_calls == []

    if initial_state == "absent":
        adapter.prepare = type(adapter).prepare.__get__(adapter, type(adapter))
    else:
        adapter.command = type(adapter).command.__get__(adapter, type(adapter))
    restarted = RuntimeGateway(
        store_path=tmp_path / "gateway.json",
        _adapter=adapter,
        configuration=gateway._configuration,
        _artifacts=adapter._artifacts,
        _planning_effect_dispatch=repository.planning_effect_dispatch(),
    )

    assert restarted.progress(subject, preflight).status == "completed"
    transitions.publish(drain)
    assert repository.planning_progress_mode(subject) == "draining"
    if initial_state == "absent":
        assert adapter.prepare_calls == [subject.stable_action_id]
    else:
        assert adapter.created_agent_count == 1
        assert adapter.command_calls == [(subject.stable_action_id, "start")]


@pytest.mark.parametrize("initial_state", ("absent", "prepared"))
def test_r9_restart_after_provider_dispatch_before_resolution_recovers_once(
    initial_state,
    tmp_path,
):
    """A post-call crash resolves by readback without a duplicate dispatch."""

    from gwo_v8.plan_control_github import GitHubPlanRepository
    from gwo_v8.runtime_gateway import RuntimeGateway
    from gwo_v8.transition import (
        GitHubWriterTransitionControl,
        WriterTransitionBlocked,
        _record,
    )

    client = _RefContentClient()
    repository = GitHubPlanRepository(
        client,
        repository="owner/repository",
        branch="gwo-control",
        writer_generation="writer:one",
    )
    gateway, subject, preflight, adapter = _r8_authorized_runtime_gateway(
        tmp_path,
        repository,
        initial_state,
    )
    transitions = GitHubWriterTransitionControl(
        client,
        branch="gwo-control",
        initial_writer="writer:one",
    )
    drain = _record(
        repository="owner/repository",
        kind="drain",
        status="draining",
        previous_writer_generation="writer:one",
        writer_generation="writer:one",
        activation_id="activation:cutover",
        plan_digest="a" * 64,
        canary_evidence_digest="b" * 64,
        canary_evidence_refs=("github://canary/evidence",),
        canary_manifest_ref="github://canary/manifest",
        worker_capacity=0,
        coordinator_capacity=0,
        reason="r9 post-call crash",
    )
    if initial_state == "absent":
        prepare = adapter.prepare

        def crash_after_prepare(spec):
            prepare(spec)
            raise KeyboardInterrupt()

        adapter.prepare = crash_after_prepare
    else:
        command = adapter.command

        def crash_after_start(stable_action_id, transition):
            command(stable_action_id, transition)
            raise KeyboardInterrupt()

        adapter.command = crash_after_start

    with pytest.raises(KeyboardInterrupt):
        gateway.progress(subject, preflight)
    with pytest.raises(WriterTransitionBlocked) as blocked:
        transitions.publish(drain)

    assert blocked.value.code == "WRITER_DRAIN_DISPATCH_ACTIVE"
    if initial_state == "absent":
        assert adapter.prepare_calls == [subject.stable_action_id]
        adapter.prepare = type(adapter).prepare.__get__(adapter, type(adapter))
    else:
        assert adapter.created_agent_count == 1
        assert adapter.command_calls == [(subject.stable_action_id, "start")]
        adapter.command = type(adapter).command.__get__(adapter, type(adapter))
    restarted = RuntimeGateway(
        store_path=tmp_path / "gateway.json",
        _adapter=adapter,
        configuration=gateway._configuration,
        _artifacts=adapter._artifacts,
        _planning_effect_dispatch=repository.planning_effect_dispatch(),
    )

    assert restarted.progress(subject, preflight).status == "completed"
    transitions.publish(drain)
    assert repository.planning_progress_mode(subject) == "draining"
    assert adapter.created_agent_count == 1
    assert adapter.command_calls == [(subject.stable_action_id, "start")]
    assert adapter.prepare_calls == [subject.stable_action_id]


@pytest.mark.parametrize(
    ("writer_status", "expected_status", "expected_error"),
    (
        ("cut_over", "completed", None),
        ("draining", None, "RUNTIME_RECOVERY_ONLY"),
    ),
)
def test_r12_planning_transition_start_obeys_the_writer_fence(
    writer_status,
    expected_status,
    expected_error,
    tmp_path,
):
    """Planning START keeps #111 behavior only while Writer is cut over."""

    from gwo_v8.plan_control_github import GitHubPlanRepository
    from gwo_v8.runtime_gateway import RuntimeCommand, RuntimeGatewayError

    client = _RefContentClient()
    repository = GitHubPlanRepository(
        client,
        repository="owner/repository",
        branch="gwo-control",
        writer_generation="writer:one",
    )
    gateway, subject, _preflight, adapter = _r8_authorized_runtime_gateway(
        tmp_path,
        repository,
        "prepared",
    )
    if writer_status == "draining":
        client.advance_writer("writer:one", status="draining")
    before = (adapter.created_agent_count, tuple(adapter.command_calls))

    if expected_error is None:
        assert gateway.transition(subject.stable_action_id, RuntimeCommand.START).status == expected_status
        assert adapter.created_agent_count == before[0] + 1
    else:
        with pytest.raises(RuntimeGatewayError) as rejected:
            gateway.transition(subject.stable_action_id, RuntimeCommand.START)
        assert rejected.value.code == expected_error
        assert (adapter.created_agent_count, tuple(adapter.command_calls)) == before


@pytest.mark.parametrize(
    ("boundary", "initial_state", "command", "expected_status"),
    (
        ("start", "prepared", "start", "completed"),
        ("resume", "parked", "resume", "running"),
        ("permission_allow", "running", "permission_allow", "running"),
    ),
)
def test_r12_cut_over_transition_effects_block_writer_drain_until_dispatch(
    boundary,
    initial_state,
    command,
    expected_status,
    tmp_path,
):
    """Every legal Planning transition effect shares the real Writer CAS fence."""

    from gwo_v8.plan_control_github import GitHubPlanRepository
    from gwo_v8.runtime_gateway import PermissionResponse, RuntimeCommand
    from gwo_v8.transition import GitHubWriterTransitionControl, WriterTransitionBlocked

    client = _RefContentClient()
    repository = GitHubPlanRepository(
        client,
        repository="owner/repository",
        branch="gwo-control",
        writer_generation="writer:one",
    )
    gateway, subject, preflight, adapter = _r8_authorized_runtime_gateway(
        tmp_path,
        repository,
        "prepared",
    )
    if initial_state == "parked":
        adapter._complete_action = lambda _action: None  # type: ignore[method-assign]
        assert gateway.progress(subject, preflight).status == "running"
        assert gateway.transition(subject.stable_action_id, RuntimeCommand.PARK).status == "parked"
    elif initial_state == "running":
        adapter._complete_action = lambda _action: None  # type: ignore[method-assign]
        adapter._actions[subject.stable_action_id].pending_permissions = [
            ("request:r12", "write", "repository")
        ]
        assert gateway.progress(subject, preflight).status == "running"

    transition = (
        getattr(RuntimeCommand, command.upper())
        if command != "permission_allow"
        else PermissionResponse("request:r12", "allow")
    )
    transitions = GitHubWriterTransitionControl(
        client,
        branch="gwo-control",
        initial_writer="writer:one",
    )
    drain_blockers = []

    def attempt_drain():
        with pytest.raises(WriterTransitionBlocked) as blocked:
            transitions.publish(_r10_drain_record(f"r12 {boundary} race"))
        drain_blockers.append(blocked.value.code)

    native_command = adapter.command

    def command_after_active(stable_action_id, observed_transition):
        if observed_transition == transition:
            attempt_drain()
        return native_command(stable_action_id, observed_transition)

    adapter.command = command_after_active  # type: ignore[method-assign]
    receipt = gateway.transition(subject.stable_action_id, transition)

    assert receipt.status == expected_status
    assert drain_blockers == ["WRITER_DRAIN_DISPATCH_ACTIVE"]
    transitions.publish(_r10_drain_record(f"r12 {boundary} drain"))
    assert repository.planning_progress_mode(subject) == "draining"


@pytest.mark.parametrize(
    ("ledger_repository", "consumer_repository", "accepted"),
    (
        pytest.param("r" * 256, "r" * 256, True, id="exact-256-bytes"),
        pytest.param("r" * 257, "r" * 257, False, id="257-bytes"),
        pytest.param("", "owner/repository", False, id="empty"),
        pytest.param(None, "owner/repository", False, id="non-string"),
    ),
)
def test_r11_dispatch_ledger_repository_header_is_bounded_for_both_consumers(
    ledger_repository,
    consumer_repository,
    accepted,
):
    """Plan admission and Writer drain share one exact header-repository bound."""

    from gwo_v8.plan_control import PlanControlError
    from gwo_v8.plan_control_github import GitHubPlanRepository
    from gwo_v8.transition import GitHubWriterTransitionControl, WriterTransitionBlocked

    plan_client = _RefContentClient(repository=consumer_repository)
    _r10_install_dispatch_entries(
        plan_client,
        [],
        repository=ledger_repository,
    )
    subject = _r10_dispatch_subject(0, repository=consumer_repository)
    plan_repository = GitHubPlanRepository(
        plan_client,
        repository=consumer_repository,
        branch="gwo-control",
        writer_generation="writer:one",
    )
    if accepted:
        assert plan_repository.planning_effect_dispatch().enter(
            subject,
            "prepare",
        ).startswith("planning-dispatch:")
        assert len(plan_client.writes) == 1
    else:
        with pytest.raises(PlanControlError) as rejected:
            plan_repository.planning_effect_dispatch().enter(subject, "prepare")
        assert rejected.value.code == "WRITER_FENCE_READBACK_INVALID"
        assert plan_client.writes == []

    writer_client = _RefContentClient(repository=consumer_repository)
    _r10_install_dispatch_entries(
        writer_client,
        [],
        repository=ledger_repository,
    )
    transitions = GitHubWriterTransitionControl(
        writer_client,
        branch="gwo-control",
        initial_writer="writer:one",
    )
    drain = _r10_drain_record(
        "r11 repository header",
        repository=consumer_repository,
    )
    if accepted:
        transitions.publish(drain)
        assert len(writer_client.writes) == 1
    else:
        with pytest.raises(WriterTransitionBlocked) as blocked:
            transitions.publish(drain)
        assert blocked.value.code == "WRITER_DRAIN_DISPATCH_INVALID"
        assert writer_client.writes == []


@pytest.mark.parametrize(
    ("boundary", "initial_state", "transition"),
    (
        ("prepare", "absent", None),
        ("start", "prepared", "start"),
        ("resume", "parked", "resume"),
        ("permission_allow", "running", "permission_allow"),
    ),
)
def test_r13_effect_ticket_blocks_writer_drain_until_authoritative_proof(
    boundary,
    initial_state,
    transition,
    tmp_path,
):
    """A returned effect remains Writer-fenced until its readback proves it."""

    from gwo_v8.plan_control_github import GitHubPlanRepository
    from gwo_v8.runtime_gateway import PermissionResponse, RuntimeCommand
    from gwo_v8.transition import GitHubWriterTransitionControl, WriterTransitionBlocked

    client = _RefContentClient()
    repository = GitHubPlanRepository(
        client,
        repository="owner/repository",
        branch="gwo-control",
        writer_generation="writer:one",
    )
    gateway, subject, preflight, adapter = _r8_authorized_runtime_gateway(
        tmp_path,
        repository,
        "prepared" if initial_state != "absent" else "absent",
    )
    if initial_state == "parked":
        adapter._complete_action = lambda _action: None  # type: ignore[method-assign]
        assert gateway.progress(subject, preflight).status == "running"
        assert gateway.transition(subject.stable_action_id, RuntimeCommand.PARK).status == "parked"
    elif initial_state == "running":
        adapter._complete_action = lambda _action: None  # type: ignore[method-assign]
        adapter._actions[subject.stable_action_id].pending_permissions = [
            ("request:r13", "write", "repository")
        ]
        assert gateway.progress(subject, preflight).status == "running"

    command = (
        None
        if transition is None
        else (
            getattr(RuntimeCommand, transition.upper())
            if transition != "permission_allow"
            else PermissionResponse("request:r13", "allow")
        )
    )
    transitions = GitHubWriterTransitionControl(
        client,
        branch="gwo-control",
        initial_writer="writer:one",
    )
    returned = False
    drain_blockers = []
    native_observe = adapter.observe

    def observe_after_effect(stable_action_id):
        if returned and not drain_blockers:
            with pytest.raises(WriterTransitionBlocked) as blocked:
                transitions.publish(_r10_drain_record(f"r13 {boundary} proof"))
            drain_blockers.append(blocked.value.code)
        return native_observe(stable_action_id)

    adapter.observe = observe_after_effect  # type: ignore[method-assign]
    if boundary == "prepare":
        native_prepare = adapter.prepare

        def prepare_then_mark(spec):
            nonlocal returned
            result = native_prepare(spec)
            returned = True
            return result

        adapter.prepare = prepare_then_mark  # type: ignore[method-assign]
        assert gateway.progress(subject, preflight).status == "completed"
    else:
        native_command = adapter.command

        def command_then_mark(stable_action_id, observed_transition):
            nonlocal returned
            result = native_command(stable_action_id, observed_transition)
            if observed_transition == command:
                returned = True
            return result

        adapter.command = command_then_mark  # type: ignore[method-assign]
        assert gateway.transition(subject.stable_action_id, command).status in {
            "running",
            "completed",
        }

    assert drain_blockers == ["WRITER_DRAIN_DISPATCH_ACTIVE"]
    transitions.publish(_r10_drain_record(f"r13 {boundary} converged"))


def _r13_fill_dispatch_ledger_to_limit(entries):
    """Pad exact valid entry fields to the shared canonical-byte boundary."""

    from gwo_v8._canonical import canonical_bytes
    from gwo_v8.transition import (
        _PLANNING_EFFECT_DISPATCH_MAX_CANONICAL_BYTES,
        _PLANNING_EFFECT_DISPATCH_SCHEMA,
        _planning_effect_dispatch_ticket,
    )

    def payload_size():
        return len(
            canonical_bytes(
                {
                    "schema_version": _PLANNING_EFFECT_DISPATCH_SCHEMA,
                    "repository": "owner/repository",
                    "entries": entries,
                }
            )
        )

    for entry in entries[1:]:
        for field in (
            "campaign_key",
            "campaign_handle",
            "stable_action_id",
            "writer_generation",
            "writer_cut_over_record_id",
            "writer_observation_ref",
        ):
            remaining = _PLANNING_EFFECT_DISPATCH_MAX_CANONICAL_BYTES - payload_size()
            if remaining <= 0:
                break
            current = entry[field]
            growth = min(remaining, 256 - len(current.encode("utf-8")))
            entry[field] = current + ("x" * growth)
            entry["ticket"] = _planning_effect_dispatch_ticket(entry)
        if payload_size() == _PLANNING_EFFECT_DISPATCH_MAX_CANONICAL_BYTES:
            break
    assert payload_size() == _PLANNING_EFFECT_DISPATCH_MAX_CANONICAL_BYTES


def test_r13_exact_limit_resolution_compacts_recovery_evidence_before_cas():
    """Resolution uses the shared bounded renderer and keeps its own evidence."""

    from gwo_v8._canonical import load_canonical_json
    from gwo_v8.plan_control_github import GitHubPlanRepository
    from gwo_v8.transition import (
        GitHubWriterTransitionControl,
        WriterTransitionBlocked,
        _PLANNING_EFFECT_DISPATCH_MAX_CANONICAL_BYTES,
    )

    client = _RefContentClient()
    owner = _r10_dispatch_subject(80)
    entries = [_r10_dispatch_entry(client, owner, state="active")]
    entries.extend(
        _r10_dispatch_entry(
            client,
            _r10_dispatch_subject(ordinal),
            state="active" if ordinal < 86 else "recovery",
        )
        for ordinal in range(81, 88)
    )
    _r13_fill_dispatch_ledger_to_limit(entries)
    _r10_install_dispatch_entries(client, entries)
    repository = GitHubPlanRepository(
        client,
        repository="owner/repository",
        branch="gwo-control",
        writer_generation="writer:one",
    )
    dispatch = repository.planning_effect_dispatch()
    ticket = entries[0]["ticket"]

    dispatch.resolve(owner, "prepare", ticket)

    ledger = load_canonical_json(
        client._commits[client.head][
            ".gwo-v8/planning-effect-dispatch-v1.json"
        ].content
    )
    assert len(
        client._commits[client.head][
            ".gwo-v8/planning-effect-dispatch-v1.json"
        ].content
    ) <= _PLANNING_EFFECT_DISPATCH_MAX_CANONICAL_BYTES
    resolved = [entry for entry in ledger["entries"] if entry["ticket"] == ticket]
    assert len(resolved) == 1 and resolved[0]["state"] == "recovery"
    assert dispatch.enter(_r10_dispatch_subject(99), "prepare").startswith(
        "planning-dispatch:"
    )

    transitions = GitHubWriterTransitionControl(
        client,
        branch="gwo-control",
        initial_writer="writer:one",
    )
    with pytest.raises(WriterTransitionBlocked) as blocked:
        transitions.publish(_r10_drain_record("r13 compacted reader"))
    assert blocked.value.code == "WRITER_DRAIN_DISPATCH_ACTIVE"


def test_r13_uncompactable_limit_resolution_fails_before_cas():
    """No over-budget recovery ledger is committed when nothing is prunable."""

    from gwo_v8.plan_control import PlanControlError
    from gwo_v8.plan_control_github import GitHubPlanRepository

    client = _RefContentClient()
    owner = _r10_dispatch_subject(100)
    entries = [_r10_dispatch_entry(client, owner, state="active")]
    entries.extend(
        _r10_dispatch_entry(client, _r10_dispatch_subject(ordinal), state="active")
        for ordinal in range(101, 108)
    )
    _r13_fill_dispatch_ledger_to_limit(entries)
    _r10_install_dispatch_entries(client, entries)
    repository = GitHubPlanRepository(
        client,
        repository="owner/repository",
        branch="gwo-control",
        writer_generation="writer:one",
    )
    dispatch = repository.planning_effect_dispatch()
    before = client.head

    with pytest.raises(PlanControlError) as rejected:
        dispatch.resolve(owner, "prepare", entries[0]["ticket"])

    assert rejected.value.code == "PLANNING_EFFECT_DISPATCH_BOUNDED"
    assert client.head == before


@pytest.mark.parametrize(
    ("boundary", "initial_state", "transition"),
    (
        ("prepare", "absent", None),
        ("start", "prepared", "start"),
        ("resume", "parked", "resume"),
        ("permission_allow", "running", "permission_allow"),
    ),
)
def test_r13_restart_after_return_before_readback_proves_once_without_duplicate_effect(
    boundary,
    initial_state,
    transition,
    tmp_path,
):
    """A post-return crash leaves one active ticket until exact restart proof."""

    from gwo_v8.plan_control_github import GitHubPlanRepository
    from gwo_v8.runtime_gateway import (
        PermissionResponse,
        RuntimeCommand,
        RuntimeGateway,
    )
    from gwo_v8.transition import GitHubWriterTransitionControl, WriterTransitionBlocked

    client = _RefContentClient()
    repository = GitHubPlanRepository(
        client,
        repository="owner/repository",
        branch="gwo-control",
        writer_generation="writer:one",
    )
    gateway, subject, preflight, adapter = _r8_authorized_runtime_gateway(
        tmp_path,
        repository,
        "prepared" if initial_state != "absent" else "absent",
    )
    if initial_state in {"parked", "running"}:
        adapter._complete_action = lambda _action: None  # type: ignore[method-assign]
        if initial_state == "running":
            adapter._actions[subject.stable_action_id].pending_permissions = [
                ("request:r13-crash", "write", "repository")
            ]
        assert gateway.progress(subject, preflight).status == "running"
        if initial_state == "parked":
            assert gateway.transition(subject.stable_action_id, RuntimeCommand.PARK).status == "parked"
    command = (
        None
        if transition is None
        else (
            getattr(RuntimeCommand, transition.upper())
            if transition != "permission_allow"
            else PermissionResponse("request:r13-crash", "allow")
        )
    )
    returned = False
    native_observe = adapter.observe

    def crash_before_readback(stable_action_id):
        nonlocal returned
        if returned:
            returned = False
            raise KeyboardInterrupt()
        return native_observe(stable_action_id)

    adapter.observe = crash_before_readback  # type: ignore[method-assign]
    if boundary == "prepare":
        native_prepare = adapter.prepare

        def prepare_then_mark(spec):
            nonlocal returned
            result = native_prepare(spec)
            returned = True
            return result

        adapter.prepare = prepare_then_mark  # type: ignore[method-assign]
        with pytest.raises(KeyboardInterrupt):
            gateway.progress(subject, preflight)
    else:
        native_command = adapter.command

        def command_then_mark(stable_action_id, observed_transition):
            nonlocal returned
            result = native_command(stable_action_id, observed_transition)
            if observed_transition == command:
                returned = True
            return result

        adapter.command = command_then_mark  # type: ignore[method-assign]
        with pytest.raises(KeyboardInterrupt):
            gateway.transition(subject.stable_action_id, command)

    after_crash = (
        tuple(adapter.prepare_calls),
        adapter.created_agent_count,
        tuple(adapter.command_calls),
    )
    transitions = GitHubWriterTransitionControl(
        client,
        branch="gwo-control",
        initial_writer="writer:one",
    )
    with pytest.raises(WriterTransitionBlocked) as blocked:
        transitions.publish(_r10_drain_record(f"r13 {boundary} crash"))
    assert blocked.value.code == "WRITER_DRAIN_DISPATCH_ACTIVE"

    adapter.observe = native_observe  # type: ignore[method-assign]
    restarted = RuntimeGateway(
        store_path=tmp_path / "gateway.json",
        _adapter=adapter,
        configuration=gateway._configuration,
        _artifacts=adapter._artifacts,
        _planning_effect_dispatch=repository.planning_effect_dispatch(),
    )
    assert restarted.progress(subject, preflight).status in {"running", "completed"}
    if boundary == "prepare":
        assert tuple(adapter.prepare_calls) == after_crash[0]
    else:
        assert (
            tuple(adapter.prepare_calls),
            adapter.created_agent_count,
            tuple(adapter.command_calls),
        ) == after_crash
    transitions.publish(_r10_drain_record(f"r13 {boundary} recovered"))


@pytest.mark.parametrize(
    ("boundary", "evidence"),
    (
        ("resume", "parked"),
        ("permission_allow", "pending"),
        ("permission_allow", "deny"),
    ),
)
def test_r13_restart_readback_does_not_resolve_the_wrong_effect_boundary(
    boundary,
    evidence,
    tmp_path,
):
    """Only the ticket's exact boundary proof may release Writer drain."""

    from gwo_v8.plan_control_github import GitHubPlanRepository
    from gwo_v8.runtime_gateway import PermissionResponse, RuntimeCommand
    from gwo_v8.transition import GitHubWriterTransitionControl, WriterTransitionBlocked

    client = _RefContentClient()
    repository = GitHubPlanRepository(
        client,
        repository="owner/repository",
        branch="gwo-control",
        writer_generation="writer:one",
    )
    gateway, subject, preflight, adapter = _r8_authorized_runtime_gateway(
        tmp_path,
        repository,
        "prepared",
    )
    adapter._complete_action = lambda _action: None  # type: ignore[method-assign]
    if boundary == "resume":
        assert gateway.progress(subject, preflight).status == "running"
        assert gateway.transition(subject.stable_action_id, RuntimeCommand.PARK).status == "parked"
    else:
        adapter._actions[subject.stable_action_id].pending_permissions = [
            ("request:r13-proof", "write", "repository")
        ]
        assert gateway.progress(subject, preflight).status == "running"
    dispatch = repository.planning_effect_dispatch()
    assert dispatch.enter(subject, boundary).startswith("planning-dispatch:")
    if evidence == "deny":
        adapter.observe(subject.stable_action_id)
        assert adapter.command(
            subject.stable_action_id,
            PermissionResponse("request:r13-proof", "deny"),
        )

    assert gateway.transition(subject.stable_action_id, RuntimeCommand.PARK).status == "parked"
    transitions = GitHubWriterTransitionControl(
        client,
        branch="gwo-control",
        initial_writer="writer:one",
    )
    with pytest.raises(WriterTransitionBlocked) as blocked:
        transitions.publish(_r10_drain_record(f"r13 wrong {boundary} {evidence}"))
    assert blocked.value.code == "WRITER_DRAIN_DISPATCH_ACTIVE"
