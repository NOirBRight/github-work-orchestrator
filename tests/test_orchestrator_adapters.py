from __future__ import annotations

import json
from pathlib import Path
import subprocess

import pytest

from conftest import load_modules as _modules


ROOT = Path(__file__).resolve().parents[1]


def _contract(core):
    contract = {
        "design": ["Implement the isolated change."],
        "acceptance": ["Regression test passes."],
        "hotset": ["src/api"],
        "done_when": ["python -m pytest tests/api -q"],
        "dependencies": [],
        "priority": "P1",
        "difficulty": "standard",
        "risk": "standard",
        "unresolved_decisions": [],
    }
    contract["sha256"] = core.contract_hash(contract)
    return contract


def test_graphql_adapter_uses_one_frontier_call_and_flattens_connections():
    core, cli = _modules()
    contract = _contract(core)
    record = core.render_issue_record({"contract": contract, "dispatch": None})
    payload = {
        "data": {
            "repository": {
                "ref": {"target": {"oid": "a" * 40}},
                "readyIssues": {
                    "pageInfo": {"hasNextPage": False},
                    "nodes": [
                        {
                            "number": 7,
                            "title": "API",
                            "body": "raw",
                            "labels": {"nodes": [{"name": "orch:ready"}]},
                            "milestone": None,
                            "assignees": {"nodes": []},
                            "comments": {
                                "nodes": [
                                    {
                                        "databaseId": 99,
                                        "body": record,
                                        "author": {"login": "u"},
                                    }
                                ]
                            },
                        }
                    ],
                },
                "pullRequests": {
                    "pageInfo": {"hasNextPage": False},
                    "nodes": [],
                },
            }
        }
    }
    client = object.__new__(cli.GitHub)
    calls = []
    client.run = lambda args: calls.append(args) or json.dumps(payload)

    snapshot = client.snapshot("owner/repo", "dev")

    assert len(calls) == 1
    assert calls[0][:2] == ["api", "graphql"]
    assert snapshot["base_sha"] == "a" * 40
    assert snapshot["issues"][0]["managed_comment_id"] == 99
    assert snapshot["issues"][0]["contract_valid"] is True


def test_snapshot_recovers_durably_intended_pr_after_it_has_merged():
    core, cli = _modules()
    contract = _contract(core)
    candidate = "b" * 40
    record = core.render_issue_record(
        {
            "contract": contract,
            "dispatch": {
                "id": "dispatch-issue-7-a1",
                "attempt": 1,
                "generation": 1,
                "branch": "work/issue-7",
                "status": "integrating",
                "pr_number": 17,
                "candidate_sha": candidate,
            },
        }
    )
    issue = {
        "number": 7,
        "title": "API",
        "body": "raw",
        "labels": {"nodes": [{"name": "orch:active"}]},
        "milestone": None,
        "assignees": {"nodes": []},
        "comments": {
            "pageInfo": {"hasNextPage": False},
            "nodes": [{"databaseId": 99, "body": record, "author": {"login": "u"}}],
        },
    }
    first = {
        "data": {
            "repository": {
                "ref": {"target": {"oid": "a" * 40}},
                "activeIssues": {"pageInfo": {"hasNextPage": False}, "nodes": [issue]},
                "pullRequests": {
                    "pageInfo": {"hasNextPage": False},
                    "nodes": [],
                },
            }
        }
    }
    merged_pr = {
        "number": 17,
        "state": "MERGED",
        "title": "done",
        "body": "",
        "headRefName": "work/issue-7",
        "headRefOid": candidate,
        "baseRefName": "dev",
        "isDraft": False,
        "updatedAt": "2026-07-19T11:00:00Z",
        "mergedAt": "2026-07-19T11:00:00Z",
        "mergeStateStatus": "CLEAN",
        "reviewDecision": "APPROVED",
        "url": "https://github.test/owner/repo/pull/17",
        "reviews": {"pageInfo": {"hasNextPage": False}, "nodes": []},
        "files": {"pageInfo": {"hasNextPage": False}, "nodes": []},
        "commits": {"nodes": []},
    }
    second = {"data": {"repository": {"p17": merged_pr}}}
    client = object.__new__(cli.GitHub)
    replies = iter((json.dumps(first), json.dumps(second)))
    client.run = lambda _args: next(replies)
    snapshot = client.snapshot("owner/repo", "dev")
    assert snapshot["issues"][0]["state"] == "merged"
    assert snapshot["issues"][0]["pr"]["number"] == 17


def test_graphql_adapter_fails_closed_instead_of_silently_truncating():
    core, cli = _modules()
    payload = {
        "data": {
            "repository": {
                "ref": {"target": {"oid": "a" * 40}},
                "readyIssues": {"pageInfo": {"hasNextPage": True}, "nodes": []},
                "pullRequests": {"pageInfo": {"hasNextPage": False}, "nodes": []},
            }
        }
    }
    client = object.__new__(cli.GitHub)
    client.run = lambda args: json.dumps(payload)
    with __import__("pytest").raises(core.PolicyError) as error:
        client.snapshot("owner/repo", "dev")
    assert error.value.code == "SNAPSHOT_PAGINATION_REQUIRED"


def test_git_worktree_readback_exposes_branch_and_path_for_partial_recovery():
    _, cli = _modules()
    parsed = cli._parse_git_worktrees(
        "worktree C:/repo\nHEAD "
        + "a" * 40
        + "\nbranch refs/heads/dev\n\n"
        + "worktree C:/workers/issue-7\nHEAD "
        + "b" * 40
        + "\nbranch refs/heads/work/issue-7\n\n"
    )
    assert parsed == [
        {"path": "C:/repo", "branch": "dev"},
        {"path": "C:/workers/issue-7", "branch": "work/issue-7"},
    ]


def test_current_runtime_uses_read_back_features_instead_of_inventing_empty(
    monkeypatch,
):
    _, cli = _modules()
    monkeypatch.setenv("PASEO_AGENT_ID", "root-a")
    payload = {
        "Id": "root-a",
        "Provider": "codex",
        "Model": "gpt-current",
        "Thinking": "high",
        "Mode": "full-access",
        "RuntimeSettings": {"features": {"fast_mode": True}},
        "ParentAgentId": None,
        "Archived": False,
        "Cwd": "C:/repo",
        "Worktree": {"Id": "wt-root"},
    }
    monkeypatch.setattr(cli, "_tool", lambda *_args: "paseo")
    monkeypatch.setattr(cli, "_run", lambda _args: json.dumps(payload))
    runtime, _ = cli._paseo_current()
    assert runtime["settings"]["features"] == {"fast_mode": True}

    payload.pop("RuntimeSettings")
    runtime_without_evidence, _ = cli._paseo_current()
    assert "features" not in runtime_without_evidence["settings"]


def test_claim_updates_single_record_then_reads_back_active_label():
    core, cli = _modules()
    contract = _contract(core)
    issue = {
        "number": 7,
        "managed_record": {"contract": contract, "dispatch": None},
        "managed_comment_id": 99,
    }
    action = {
        "action_id": "create-worker-dispatch-issue-7-a1",
        "type": "create_worker",
        "dispatch_id": "dispatch-issue-7-a1",
        "issue": 7,
        "attempt": 1,
        "branch": "work/issue-7",
        "base_sha": "a" * 40,
        "wave_generation": 2,
    }
    client = object.__new__(cli.GitHub)
    calls = []

    def fake_run(args):
        calls.append(args)
        if args[:2] == ["api", "--method"]:
            body = next(value[5:] for value in args if value.startswith("body="))
            return json.dumps({"body": body})
        if args[:2] == ["issue", "view"]:
            return json.dumps({"labels": [{"name": "orch:active"}]})
        return ""

    client.run = fake_run
    client.claim("owner/repo", issue, action)

    assert [call[:2] for call in calls] == [
        ["api", "--method"],
        ["issue", "edit"],
        ["issue", "view"],
    ]
    body_argument = next(value for value in calls[0] if value.startswith("body="))
    parsed = core.parse_issue_record(body_argument[5:])
    assert parsed["dispatch"]["id"] == "dispatch-issue-7-a1"
    assert parsed["dispatch"]["base_sha"] == "a" * 40
    assert parsed["dispatch"]["status"] == "claiming"


def test_merge_is_atomically_bound_to_the_reviewed_head_sha():
    _, cli = _modules()
    client = object.__new__(cli.GitHub)
    calls = []

    def fake_run(args):
        calls.append(args)
        if args[:2] == ["pr", "view"]:
            return json.dumps(
                {
                    "state": "MERGED",
                    "mergedAt": "2026-07-19T11:00:00Z",
                    "headRefName": "work/issue-7",
                    "headRefOid": "b" * 40,
                }
            )
        return ""

    client.run = fake_run
    expected = "b" * 40
    client.merge("owner/repo", 17, "squash", expected)
    assert "--match-head-commit" in calls[0]
    assert calls[0][calls[0].index("--match-head-commit") + 1] == expected


def test_retire_persists_parked_terminal_state_before_releasing_slot(monkeypatch):
    _, cli = _modules()
    client = object.__new__(cli.GitHub)
    updated = []
    states = []
    monkeypatch.setattr(
        client,
        "update_record",
        lambda repo, issue: updated.append((repo, issue.copy())),
    )
    monkeypatch.setattr(
        client,
        "set_issue_state",
        lambda repo, number, state: states.append((repo, number, state)),
    )
    issue = {
        "number": 7,
        "dispatch": {"id": "dispatch-issue-7-a1", "status": "stopped"},
    }
    client.retire_issue("owner/repo", issue)
    assert issue["dispatch"]["status"] == "retired"
    assert issue["dispatch"]["parked"] is True
    assert states == [("owner/repo", 7, "blocked")]
    assert updated[0][1]["dispatch"]["status"] == "retired"


def test_new_accepted_sha_can_replace_open_stale_integration_intent(monkeypatch):
    core, cli = _modules()
    client = object.__new__(cli.GitHub)
    monkeypatch.setattr(client, "update_record", lambda *_args: None)
    issue = {
        "number": 7,
        "dispatch": {
            "id": "dispatch-issue-7-a1",
            "status": "integrating",
            "pr_number": 17,
            "candidate_sha": "a" * 40,
        },
    }
    client.mark_integrating("owner/repo", issue, 17, "b" * 40, "OPEN")
    assert issue["dispatch"]["candidate_sha"] == "b" * 40
    with __import__("pytest").raises(core.PolicyError) as closed:
        client.mark_integrating("owner/repo", issue, 17, "c" * 40, "MERGED")
    assert closed.value.code == "INTEGRATION_IDENTITY_CONFLICT"


def test_duplicate_or_lost_finish_notification_recovers_from_pr_facts():
    core, _ = _modules()
    contract = _contract(core)
    record = {
        "contract": contract,
        "dispatch": {
            "id": "dispatch-issue-7-a1",
            "attempt": 1,
            "generation": 1,
            "creator_agent_id": "root-a",
            "worker_agent_id": "worker-a",
            "workspace_id": "wt-a",
            "branch": "work/issue-7",
            "base_sha": "a" * 40,
            "status": "running",
        },
    }
    delivery = {
        "contract_sha256": contract["sha256"],
        "candidate_sha": "b" * 40,
        "changed_paths": ["src/api/x.py"],
        "tdd": {"red": "failed", "green": "passed", "refactor": "clean"},
        "verification": ["pytest"],
        "deviations": [],
        "risks": [],
    }
    issue = {
        "number": 7,
        "title": "x",
        "labels": [{"name": "orch:active"}],
        "comments": [{"id": 9, "body": core.render_issue_record(record)}],
    }
    pr = {
        "number": 17,
        "body": core.render_delivery(delivery),
        "headRefName": "work/issue-7",
        "headRefOid": "b" * 40,
        "baseRefName": "dev",
        "isDraft": False,
        "mergeStateStatus": "CLEAN",
        "reviewDecision": None,
        "statusCheckRollup": [],
        "reviews": [],
    }

    first = core.normalize_github_snapshot("owner/repo", [issue], [pr])
    second = core.normalize_github_snapshot("owner/repo", [issue], [pr])

    assert first == second
    assert first["issues"][0]["state"] == "review"
    assert core.plan_review_actions(first)["actions"][0]["pr"] == 17


def _git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(cwd), *args],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return result.stdout.strip()


def _worker_repo(tmp_path: Path, branch: str) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", branch)
    _git(repo, "config", "user.email", "test@example.invalid")
    _git(repo, "config", "user.name", "Test")
    (repo / "tracked.txt").write_text("base\n", encoding="utf-8")
    _git(repo, "add", "tracked.txt")
    _git(repo, "commit", "-m", "base")
    return repo


def test_cleanup_adapter_requires_exact_dispatch_agent_workspace_and_branch(
    tmp_path, monkeypatch
):
    _, cli = _modules()
    worker_repo = _worker_repo(tmp_path, "work/issue-8")

    class FakePaseo:
        def __init__(self):
            self.archived = []

        def agents_for_dispatch(self, _dispatch):
            return [{"id": "worker-7"}]

        def inspect(self, _agent):
            return {
                "Id": "worker-7",
                "ParentAgentId": "root-a",
                "Status": "idle",
                "Archived": False,
                "Cwd": str(worker_repo),
                "Worktree": {"Id": "workspace-7"},
            }

        def archive_agent(self, agent_id):
            self.archived.append(agent_id)

    fake = FakePaseo()
    monkeypatch.setattr(cli, "Paseo", lambda: fake)
    result = cli._cleanup_after_merge(
        "owner/repo",
        {
            "number": 7,
            "dispatch": {
                "id": "dispatch-issue-7-a1",
                "worker_agent_id": "worker-7",
                "workspace_id": "workspace-7",
                "branch": "work/issue-7",
            },
            "pr": {"head_sha": "a" * 40},
        },
        "root-a",
        object(),
        "dev",
    )
    assert result["blockers"] == ["dispatch-identity-mismatch"]
    assert fake.archived == []


def test_dispatch_identity_uses_registered_git_worktree_when_cli_omits_id(
    tmp_path, monkeypatch
):
    _, cli = _modules()
    worker_repo = _worker_repo(tmp_path, "work/issue-7")

    class FakePaseo:
        def agents_for_dispatch(self, _dispatch):
            return [{"id": "worker-7"}]

        def inspect(self, _agent):
            return {
                "Id": "worker-7",
                "Cwd": str(worker_repo),
                "Worktree": None,
            }

    monkeypatch.setattr(
        cli,
        "_runtime_worktrees",
        lambda: [{"path": str(worker_repo), "branch": "work/issue-7"}],
    )
    evidence = cli._verified_dispatch_runtime(
        {
            "id": "dispatch-issue-7-a1",
            "worker_agent_id": "worker-7",
            "workspace_id": "wks-read-back-by-mcp",
            "branch": "work/issue-7",
        },
        FakePaseo(),
        "dev",
    )
    assert evidence["state"] == "present"
    assert evidence["blocker"] is None
    assert evidence["detail"]["Id"] == "worker-7"
    assert evidence["cwd"] == worker_repo.resolve()
    assert evidence["branch"] == "work/issue-7"


def test_cleanup_accepts_host_auto_archive_and_is_idempotent(tmp_path, monkeypatch):
    _, cli = _modules()
    removed_cwd = tmp_path / "already-removed"
    candidate = "a" * 40

    class FakePaseo:
        def agents_for_dispatch(self, dispatch):
            return [
                {
                    "id": "worker-7",
                    "labels": {"orch.dispatch": dispatch},
                }
            ]

        def inspect(self, _agent):
            return {
                "Id": "worker-7",
                "ParentAgentId": "root-a",
                "Status": "closed",
                "Archived": True,
                "Cwd": str(removed_cwd),
                "Worktree": None,
                "Labels": {"orch.dispatch": "dispatch-issue-7-a1"},
            }

        def all_agents(self):
            raise AssertionError(
                "auto-archived cleanup must not rediscover removed cwd"
            )

        def agents_for_labels(self, _labels):
            raise AssertionError(
                "completed host cleanup must not duplicate archive actions"
            )

    class FakeGitHub:
        def __init__(self):
            self.remote_sha = candidate
            self.deleted = []

        def remote_branch_sha(self, _repo, _branch):
            return self.remote_sha

        def delete_remote_branch(self, _repo, branch, expected_sha):
            assert expected_sha == candidate
            self.deleted.append(branch)
            self.remote_sha = None

    github = FakeGitHub()
    monkeypatch.setattr(cli, "Paseo", FakePaseo)
    issue = {
        "number": 7,
        "dispatch": {
            "id": "dispatch-issue-7-a1",
            "worker_agent_id": "worker-7",
            "workspace_id": "workspace-7",
            "branch": "work/issue-7",
            "status": "merged",
            "candidate_sha": candidate,
        },
        "pr": {"number": 17, "head_sha": candidate},
    }
    first = cli._cleanup_after_merge("owner/repo", issue, "root-a", github, "dev")
    assert first["blockers"] == []
    assert first["manual_cleanup"] == []
    assert first["actions"] == [{"type": "delete_branch", "branch": "work/issue-7"}]
    assert first["runtime_evidence"] == "auto_archived"

    second = cli._cleanup_after_merge("owner/repo", issue, "root-a", github, "dev")
    assert second["actions"] == []
    assert second["blockers"] == []
    assert github.deleted == ["work/issue-7"]


def test_auto_archive_evidence_rejects_label_sha_or_remote_drift(tmp_path, monkeypatch):
    _, cli = _modules()
    candidate = "a" * 40

    class FakePaseo:
        label = "wrong-dispatch"

        def agents_for_dispatch(self, _dispatch):
            return [{"id": "worker-7", "labels": {"orch.dispatch": self.label}}]

        def inspect(self, _agent):
            return {
                "Id": "worker-7",
                "Archived": True,
                "Cwd": str(tmp_path / "gone"),
                "Worktree": None,
            }

    paseo = FakePaseo()
    monkeypatch.setattr(cli, "Paseo", lambda: paseo)

    class FakeGitHub:
        remote_sha = "c" * 40

        def remote_branch_sha(self, *_args):
            return self.remote_sha

    issue = {
        "number": 7,
        "dispatch": {
            "id": "dispatch-issue-7-a1",
            "worker_agent_id": "worker-7",
            "workspace_id": "workspace-7",
            "branch": "work/issue-7",
            "status": "merged",
            "candidate_sha": candidate,
        },
        "pr": {"number": 17, "head_sha": candidate},
    }
    mislabeled = cli._cleanup_after_merge(
        "owner/repo", issue, "root-a", FakeGitHub(), "dev"
    )
    assert mislabeled["blockers"] == ["dispatch-identity-mismatch"]

    paseo.label = "dispatch-issue-7-a1"
    issue["dispatch"]["candidate_sha"] = "b" * 40
    sha_drift = cli._cleanup_after_merge(
        "owner/repo", issue, "root-a", FakeGitHub(), "dev"
    )
    assert sha_drift["blockers"] == ["cleanup-candidate-mismatch"]

    issue["dispatch"]["candidate_sha"] = candidate
    remote_drift = cli._cleanup_after_merge(
        "owner/repo", issue, "root-a", FakeGitHub(), "dev"
    )
    assert remote_drift["blockers"] == ["remote-branch-has-unmerged-wip"]


def test_production_integrate_accepts_host_first_archive_without_warning(
    tmp_path, monkeypatch
):
    core, cli = _modules()
    candidate = "a" * 40
    contract = _contract(core)
    issue = {
        "number": 7,
        "state": "ready-to-merge",
        "priority": "P1",
        "dependencies": [],
        "contract": contract,
        "contract_valid": True,
        "dispatch": {
            "id": "dispatch-issue-7-a1",
            "status": "ready-to-merge",
            "accepted_at": "2026-07-20T01:00:00Z",
            "worker_agent_id": "worker-7",
            "workspace_id": "workspace-7",
            "branch": "work/issue-7",
        },
        "pr": {
            "number": 17,
            "head_sha": candidate,
            "base": "dev",
            "merge_state": "CLEAN",
            "checks": "green",
            "review_decision": "APPROVED",
            "delivery_valid": True,
        },
    }
    workspace = {
        "id": "stable-dev",
        "repository": "owner/repo",
        "branch": "dev",
        "relationship": "root",
        "dirty": False,
        "pr_head": False,
        "ephemeral": False,
        "worker": False,
        "agent_cwd_matches": True,
    }
    snapshot = {
        "repository": "owner/repo",
        "issues": [issue],
        "closed_issues": [],
        "coordinator_workspace": workspace,
    }

    class AutoArchivedPaseo:
        def agents_for_dispatch(self, dispatch):
            return [{"id": "worker-7", "labels": {"orch.dispatch": dispatch}}]

        def inspect(self, _agent):
            return {
                "Id": "worker-7",
                "ParentAgentId": "root-a",
                "Status": "closed",
                "Archived": True,
                "Cwd": str(tmp_path / "host-removed"),
                "Worktree": None,
            }

    class FakeGitHub:
        def __init__(self):
            self.closed = False
            self.deleted = []
            self.remote_sha = candidate

        def mark_integrating(self, _repo, managed, pr, sha, _state):
            managed["dispatch"].update(
                {
                    "status": "integrating",
                    "pr_number": pr,
                    "candidate_sha": sha,
                }
            )

        def merge(self, _repo, _pr, _method, expected_sha):
            assert expected_sha == candidate
            return {"state": "MERGED", "mergedAt": "2026-07-20T01:05:00Z"}

        def mark_merged(self, _repo, managed, pr, sha, merged_at):
            managed["dispatch"].update(
                {
                    "status": "merged",
                    "pr_number": pr,
                    "candidate_sha": sha,
                    "merged_at": merged_at,
                }
            )

        def close_issue(self, *_args):
            self.closed = True

        def remote_branch_sha(self, *_args):
            return self.remote_sha

        def delete_remote_branch(self, _repo, branch, expected_sha):
            assert expected_sha == candidate
            self.deleted.append(branch)
            self.remote_sha = None

    github = FakeGitHub()
    config = {
        **core.default_config(),
        "repositories": {"owner/repo": {"integration_branch": "dev"}},
    }
    monkeypatch.setattr(cli, "GitHub", lambda: github)
    monkeypatch.setattr(cli, "Paseo", AutoArchivedPaseo)
    monkeypatch.setattr(cli, "_load_config", lambda *_args, **_kwargs: config)
    monkeypatch.setattr(cli, "_git_common_dir", lambda: tmp_path)
    monkeypatch.setattr(
        cli,
        "_coordinator_preflight",
        lambda *_: (
            {
                "repository": "owner/repo",
                "integration_branch": "dev",
                "merge_method": "squash",
                "worker_slots": 3,
                "max_attempts": 2,
            },
            {"status": "ready"},
        ),
    )
    monkeypatch.setattr(
        cli,
        "_prepare_snapshot",
        lambda *_args, **_kwargs: (snapshot, {"agent_id": "root-a"}),
    )
    args = type(
        "Args",
        (),
        {"snapshot": None, "repo": "owner/repo", "pr": 17, "config": tmp_path},
    )()
    result = cli._integrate(args)
    assert result["status"] == "idle"
    assert result["warnings"] == []
    assert result["summary"]["cleanup"]["runtime_evidence"] == "auto_archived"
    assert github.closed is True
    assert github.deleted == ["work/issue-7"]


def test_cleanup_adapter_preserves_clean_local_or_remote_unmerged_wip(
    tmp_path, monkeypatch
):
    _, cli = _modules()
    worker_repo = _worker_repo(tmp_path, "work/issue-7")
    actual_head = _git(worker_repo, "rev-parse", "HEAD")

    class FakePaseo:
        archived = []

        def agents_for_dispatch(self, _dispatch):
            return [{"id": "worker-7"}]

        def inspect(self, _agent):
            return {
                "Id": "worker-7",
                "ParentAgentId": "root-a",
                "Status": "idle",
                "Archived": False,
                "Cwd": str(worker_repo),
                "Worktree": {"Id": "workspace-7"},
            }

        def archive_agent(self, agent_id):
            self.archived.append(agent_id)

    class FakeGitHub:
        remote_sha = None

        def remote_branch_sha(self, _repo, _branch):
            return self.remote_sha

    fake_paseo = FakePaseo()
    fake_github = FakeGitHub()
    monkeypatch.setattr(cli, "Paseo", lambda: fake_paseo)
    issue = {
        "number": 7,
        "dispatch": {
            "id": "dispatch-issue-7-a1",
            "worker_agent_id": "worker-7",
            "workspace_id": "workspace-7",
            "branch": "work/issue-7",
        },
        "pr": {"head_sha": "a" * 40},
    }
    local = cli._cleanup_after_merge("owner/repo", issue, "root-a", fake_github, "dev")
    assert local["blockers"] == ["local-head-not-merged-candidate"]
    assert fake_paseo.archived == []

    issue["pr"]["head_sha"] = actual_head
    fake_github.remote_sha = "c" * 40
    remote = cli._cleanup_after_merge("owner/repo", issue, "root-a", fake_github, "dev")
    assert remote["blockers"] == ["remote-branch-has-unmerged-wip"]
    assert fake_paseo.archived == []


def test_retire_adapter_rejects_mislabeled_worker_without_side_effect(
    tmp_path, monkeypatch
):
    _, cli = _modules()
    worker_repo = _worker_repo(tmp_path, "work/issue-8")

    class FakePaseo:
        archived = []

        def agents_for_dispatch(self, _dispatch):
            return [{"id": "worker-7"}]

        def inspect(self, _agent):
            return {
                "Id": "worker-7",
                "ParentAgentId": "root-a",
                "Status": "idle",
                "Archived": False,
                "Cwd": str(worker_repo),
                "Worktree": {"Id": "workspace-7"},
            }

        def archive_agent(self, agent_id):
            self.archived.append(agent_id)

    fake = FakePaseo()
    monkeypatch.setattr(cli, "Paseo", lambda: fake)
    result = cli._retire_stopped_dispatch(
        {
            "number": 7,
            "dispatch": {
                "id": "dispatch-issue-7-a1",
                "worker_agent_id": "worker-7",
                "workspace_id": "workspace-7",
                "branch": "work/issue-7",
            },
        },
        "root-a",
        "dev",
    )
    assert result["blockers"] == ["dispatch-identity-mismatch"]
    assert fake.archived == []


def test_integrate_preserves_merge_fact_when_cleanup_fails(tmp_path, monkeypatch):
    core, cli = _modules()
    contract = _contract(core)
    issue = {
        "number": 7,
        "state": "ready-to-merge",
        "priority": "P1",
        "dependencies": [],
        "contract": contract,
        "contract_valid": True,
        "dispatch": {
            "id": "dispatch-issue-7-a1",
            "accepted_at": "2026-07-19T10:00:00Z",
        },
        "pr": {
            "number": 17,
            "head_sha": "b" * 40,
            "base": "dev",
            "merge_state": "CLEAN",
            "checks": "green",
            "review_decision": "APPROVED",
            "delivery_valid": True,
        },
    }
    workspace = {
        "id": "integration-wt",
        "repository": "owner/repo",
        "branch": "dev",
        "relationship": "root",
        "dirty": False,
        "pr_head": False,
        "ephemeral": False,
        "worker": False,
        "agent_cwd_matches": True,
    }
    snapshot = {
        "repository": "owner/repo",
        "issues": [issue],
        "closed_issues": [],
        "coordinator_workspace": workspace,
    }

    class FakeGitHub:
        def __init__(self):
            self.closed = False
            self.integration_states = []

        def mark_integrating(self, _repo, _issue, pr, candidate, pr_state):
            self.integration_states.append(("integrating", pr, candidate, pr_state))

        def merge(self, _repo, _pr, _method, expected_head_sha):
            assert expected_head_sha == "b" * 40
            return {"state": "MERGED", "mergedAt": "2026-07-19T11:00:00Z"}

        def mark_merged(self, _repo, _issue, pr, candidate, merged_at):
            self.integration_states.append(("merged", pr, candidate, merged_at))

        def close_issue(self, _repo, _issue, _pr):
            assert self.integration_states[-1][0] == "merged"
            self.closed = True

    fake = FakeGitHub()
    config = {
        **core.default_config(),
        "repositories": {"owner/repo": {"integration_branch": "dev"}},
    }
    monkeypatch.setattr(cli, "GitHub", lambda: fake)
    monkeypatch.setattr(cli, "_load_config", lambda *_args, **_kwargs: config)
    monkeypatch.setattr(
        cli,
        "_coordinator_preflight",
        lambda _args, _config: (
            {
                "repository": "owner/repo",
                "integration_branch": "dev",
                "merge_method": "squash",
                "worker_slots": 3,
                "max_attempts": 2,
            },
            {"status": "ready"},
        ),
    )
    monkeypatch.setattr(cli, "_git_common_dir", lambda: tmp_path)
    monkeypatch.setattr(
        cli,
        "_prepare_snapshot",
        lambda *_args, **_kwargs: (snapshot, {"agent_id": "root-a"}),
    )
    monkeypatch.setattr(
        cli,
        "_cleanup_after_merge",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("cleanup failed")),
    )
    args = type(
        "Args",
        (),
        {"snapshot": None, "repo": "owner/repo", "pr": 17, "config": tmp_path},
    )()
    result = cli._integrate(args)
    assert result["status"] == "idle"
    assert result["summary"]["merged_at"] == "2026-07-19T11:00:00Z"
    assert result["warnings"][0]["code"] == "cleanup-deferred"
    assert fake.closed is True
    assert [item[0] for item in fake.integration_states] == ["integrating", "merged"]


def test_state_repair_cannot_mutate_before_coordinator_eligibility(monkeypatch):
    core, cli = _modules()
    contract = _contract(core)
    issue = {
        "number": 7,
        "state": "ready",
        "contract": contract,
        "contract_valid": True,
        "dispatch": {
            "id": "dispatch-issue-7-a1",
            "status": "claiming",
            "generation": 1,
            "branch": "work/issue-7",
        },
    }

    class FakeGitHub:
        repairs = []

        def snapshot(self, _repo, _branch):
            return {
                "repository": "owner/repo",
                "issues": [issue],
                "closed_issues": [],
                "pr_heads": [],
                "base_sha": "a" * 40,
            }

        def set_issue_state(self, _repo, number, state):
            self.repairs.append((number, state))

    fake = FakeGitHub()
    monkeypatch.setattr(
        cli, "Paseo", lambda: type("P", (), {"runtime_agents": lambda *_: []})()
    )
    monkeypatch.setattr(cli, "_runtime_worktrees", lambda: [])
    monkeypatch.setattr(
        cli,
        "_paseo_current",
        lambda: (
            {"agent_id": "child"},
            {
                "relationship": "subagent",
                "workspace_id": "wt",
                "archived": False,
                "cwd": "C:/repo",
            },
        ),
    )
    monkeypatch.setattr(
        cli,
        "_workspace",
        lambda *_: {
            "id": "wt",
            "repository": "owner/repo",
            "branch": "dev",
            "relationship": "subagent",
            "dirty": False,
            "pr_head": False,
            "ephemeral": False,
            "worker": False,
            "agent_cwd_matches": True,
        },
    )
    with __import__("pytest").raises(core.PolicyError) as rejected:
        cli._prepare_snapshot(
            fake,
            "owner/repo",
            {
                "integration_branch": "dev",
                "execution_slots": 3,
                "integration_wip_limit": 6,
                "worker_slots": 3,
                "repository": "owner/repo",
            },
            [],
            mutate=True,
        )
    assert rejected.value.code == "COORDINATOR_NOT_ROOT"
    assert fake.repairs == []


@pytest.mark.parametrize(
    "argv, entrypoint",
    [
        (["reconcile", "--repo", "owner/repo"], "_reconcile"),
        (["integrate", "--repo", "owner/repo", "--pr", "17"], "_integrate"),
        (
            ["retire", "--repo", "owner/repo", "--dispatch", "dispatch-issue-7-a1"],
            "_retire",
        ),
        (["project", "sync", "--repo", "owner/repo"], "_project"),
        (
            ["frontier", "admit", "--repo", "owner/repo", "--plan", "plan.json"],
            "_frontier",
        ),
    ],
)
def test_every_mutation_entry_requires_context_before_github_adapter(
    monkeypatch, tmp_path, argv, entrypoint
):
    core, cli = _modules()
    args = cli.parse_args([*argv, "--config", str(tmp_path / "config.json")])
    constructed = []

    class ForbiddenGitHub:
        def __init__(self):
            constructed.append(True)

    monkeypatch.setattr(cli, "GitHub", ForbiddenGitHub)
    with pytest.raises(core.PolicyError) as rejected:
        getattr(cli, entrypoint)(args)
    assert rejected.value.code == "COORDINATOR_CONTEXT_REQUIRED"
    assert constructed == []


@pytest.mark.parametrize(
    "argv, entrypoint",
    [
        (["reconcile", "--repo", "owner/repo"], "_reconcile"),
        (["integrate", "--repo", "owner/repo", "--pr", "17"], "_integrate"),
        (
            ["retire", "--repo", "owner/repo", "--dispatch", "dispatch-issue-7-a1"],
            "_retire",
        ),
        (["project", "sync", "--repo", "owner/repo"], "_project"),
        (
            ["frontier", "admit", "--repo", "owner/repo", "--plan", "plan.json"],
            "_frontier",
        ),
    ],
)
def test_plan_mode_blocks_every_mutation_before_github_adapter(
    monkeypatch, tmp_path, argv, entrypoint
):
    core, cli = _modules()
    workspace = {
        "id": "stable-dev",
        "repository": "owner/repo",
        "branch": "dev",
        "relationship": "root",
        "dirty": False,
        "pr_head": False,
        "ephemeral": False,
        "worker": False,
        "agent_cwd_matches": True,
    }
    context = {
        "schema_version": 1,
        "actor": {
            "id": "root-a",
            "cwd": str(tmp_path),
            "workspace_id": "stable-dev",
            "provider": "codex",
            "settings": {"model": "gpt-5.6", "modeId": "full-access"},
        },
        "current_workspace": workspace,
        "candidate_workspaces": [workspace],
        "mode": {
            "collaboration_mode": "plan",
            "write_capable": True,
            "colorTier": "planning",
        },
        "features": {"plan_mode": True},
        "remote_branches": ["dev"],
        "active_root_agents": [],
        "request": "write",
    }
    context_path = tmp_path / "context.json"
    context_path.write_text(json.dumps(context), encoding="utf-8")
    args = cli.parse_args(
        [
            *argv,
            "--coordinator-context",
            str(context_path),
            "--config",
            str(tmp_path / "config.json"),
        ]
    )
    constructed = []
    monkeypatch.setattr(cli, "GitHub", lambda: constructed.append(True))
    monkeypatch.setattr(
        cli,
        "_paseo_current",
        lambda: (
            {"agent_id": "root-a"},
            {
                "workspace_id": "stable-dev",
                "cwd": str(tmp_path),
                "relationship": "root",
                "archived": False,
            },
        ),
    )
    monkeypatch.setattr(cli, "_tool", lambda *_: "git")
    monkeypatch.setattr(cli, "_remote_repository", lambda: "owner/repo")
    monkeypatch.setattr(
        cli,
        "_run",
        lambda command, **_kwargs: (
            json.loads(context_path.read_text(encoding="utf-8"))["current_workspace"][
                "branch"
            ]
            if "branch" in command
            else str(tmp_path)
        ),
    )
    with pytest.raises(core.PolicyError) as rejected:
        getattr(cli, entrypoint)(args)
    assert rejected.value.code == "COORDINATOR_MODE_READ_ONLY"
    assert constructed == []


def test_cli_exposes_common_coordinator_context_and_lifecycle_flags():
    _, cli = _modules()
    for argv in (
        ["reconcile", "--repo", "owner/repo", "--coordinator-context", "ctx.json"],
        [
            "integrate",
            "--repo",
            "owner/repo",
            "--pr",
            "17",
            "--coordinator-context",
            "ctx.json",
        ],
        [
            "retire",
            "--repo",
            "owner/repo",
            "--dispatch",
            "dispatch-issue-7-a1",
            "--coordinator-context",
            "ctx.json",
        ],
        [
            "project",
            "sync",
            "--repo",
            "owner/repo",
            "--coordinator-context",
            "ctx.json",
        ],
        [
            "frontier",
            "admit",
            "--repo",
            "owner/repo",
            "--plan",
            "plan.json",
            "--coordinator-context",
            "ctx.json",
        ],
    ):
        assert cli.parse_args(argv).coordinator_context == "ctx.json"

    parked = cli.parse_args(
        ["reconcile", "--repo", "owner/repo", "--park", "dispatch-issue-7-a1"]
    )
    resumed = cli.parse_args(
        ["reconcile", "--repo", "owner/repo", "--resume", "dispatch-issue-7-a1"]
    )
    assert parked.park == "dispatch-issue-7-a1"
    assert resumed.resume == "dispatch-issue-7-a1"


def test_coordinator_context_selection_and_forwarding_use_production_preflight(
    monkeypatch, tmp_path
):
    _, cli = _modules()
    workspace = {
        "id": "stable-dev",
        "repository": "owner/repo",
        "branch": "dev",
        "relationship": "root",
        "dirty": False,
        "pr_head": False,
        "ephemeral": False,
        "worker": False,
        "agent_cwd_matches": True,
    }
    context = {
        "schema_version": 1,
        "actor": {
            "id": "root-a",
            "cwd": str(tmp_path),
            "workspace_id": "stable-dev",
            "provider": "codex",
            "settings": {"model": "gpt-5.6", "modeId": "full-access"},
        },
        "current_workspace": workspace,
        "candidate_workspaces": [workspace],
        "mode": {
            "collaboration_mode": "default",
            "write_capable": True,
            "colorTier": "dangerous",
        },
        "features": {"plan_mode": False},
        "remote_branches": ["main", "dev"],
        "active_root_agents": [],
        "request": "continue",
    }
    context_path = tmp_path / "context.json"
    context_path.write_text(json.dumps(context), encoding="utf-8")
    args = type(
        "Args",
        (),
        {"repo": "owner/repo", "coordinator_context": str(context_path)},
    )()
    monkeypatch.setattr(
        cli,
        "_paseo_current",
        lambda: (
            {"agent_id": "root-a"},
            {
                "workspace_id": "stable-dev",
                "cwd": str(tmp_path),
                "relationship": "root",
                "archived": False,
            },
        ),
    )
    monkeypatch.setattr(cli, "_tool", lambda *_: "git")
    monkeypatch.setattr(cli, "_remote_repository", lambda: "owner/repo")
    monkeypatch.setattr(
        cli,
        "_run",
        lambda command, **_kwargs: (
            json.loads(context_path.read_text(encoding="utf-8"))["current_workspace"][
                "branch"
            ]
            if "branch" in command
            else str(tmp_path)
        ),
    )

    repo_config, entry = cli._coordinator_preflight(args, cli.core.default_config())
    assert repo_config["integration_branch"] == "dev"
    assert entry["status"] == "ready"

    monkeypatch.setattr(
        cli,
        "_paseo_current",
        lambda: (
            {"agent_id": "root-a"},
            {
                "workspace_id": "stable-dev",
                "cwd": str(tmp_path),
                "relationship": "subagent",
                "archived": False,
            },
        ),
    )
    with pytest.raises(cli.core.PolicyError) as relationship_drift:
        cli._coordinator_preflight(args, cli.core.default_config())
    assert relationship_drift.value.code == "COORDINATOR_RELATIONSHIP_MISMATCH"

    feature = {
        **workspace,
        "id": "feature-17",
        "branch": "work/issue-17",
        "worker": True,
    }
    context["actor"]["workspace_id"] = "feature-17"
    context["current_workspace"] = feature
    context_path.write_text(json.dumps(context), encoding="utf-8")
    monkeypatch.setattr(
        cli,
        "_paseo_current",
        lambda: (
            {"agent_id": "root-a"},
            {
                "workspace_id": "feature-17",
                "cwd": str(tmp_path),
                "relationship": "root",
                "archived": False,
            },
        ),
    )
    _, entry = cli._coordinator_preflight(args, cli.core.default_config())
    assert entry["status"] == "forwarded"
    assert entry["actions"][0]["type"] == "create_root_agent"

    monkeypatch.setattr(
        cli,
        "_run",
        lambda command, **_kwargs: "dev" if "branch" in command else str(tmp_path),
    )
    with pytest.raises(cli.core.PolicyError) as stale:
        cli._coordinator_preflight(args, cli.core.default_config())
    assert stale.value.code == "COORDINATOR_GIT_MISMATCH"


def test_reconcile_park_and_resume_persist_before_returning_paseo_actions(
    monkeypatch, tmp_path
):
    core, cli = _modules()
    contract = _contract(core)
    snapshot = {
        "repository": "owner/repo",
        "base_sha": "a" * 40,
        "worker_slots": 3,
        "closed_issues": [],
        "issues": [
            {
                "number": 7,
                "state": "active",
                "contract": contract,
                "contract_valid": True,
                "hotset": contract["hotset"],
                "dispatch": {
                    "id": "dispatch-issue-7-a1",
                    "attempt": 1,
                    "status": "running",
                    "parked": False,
                    "worker_agent_id": "worker-7",
                    "workspace_id": "workspace-7",
                    "branch": "work/issue-7",
                    "base_sha": "a" * 40,
                    "contract_sha256": contract["sha256"],
                },
            }
        ],
        "runtime_agents": [
            {
                "id": "worker-7",
                "workspace_id": "workspace-7",
                "branch": "work/issue-7",
                "labels": {"orch.dispatch": "dispatch-issue-7-a1"},
                "state": "running",
            }
        ],
    }

    class FakeGitHub:
        records = []
        states = []

        def update_record(self, _repo, issue):
            self.records.append(issue["dispatch"]["status"])

        def set_issue_state(self, _repo, _issue, state):
            self.states.append(state)

    github = FakeGitHub()
    monkeypatch.setattr(cli, "GitHub", lambda: github)
    monkeypatch.setattr(
        cli,
        "_coordinator_preflight",
        lambda *_: (
            {
                "repository": "owner/repo",
                "integration_branch": "dev",
                "worker_slots": 3,
                "max_attempts": 2,
                "merge_method": "squash",
            },
            {"status": "ready"},
        ),
    )
    monkeypatch.setattr(
        cli, "_load_config", lambda *_args, **_kwargs: core.default_config()
    )
    monkeypatch.setattr(cli, "_git_common_dir", lambda: tmp_path)
    snapshot_holder = {"value": snapshot}
    monkeypatch.setattr(
        cli,
        "_prepare_snapshot",
        lambda *_args, **_kwargs: (snapshot_holder["value"], {}),
    )
    args = type(
        "Args",
        (),
        {
            "repo": "owner/repo",
            "observations": None,
            "snapshot": None,
            "read_only": False,
            "park": "dispatch-issue-7-a1",
            "resume": None,
            "config": tmp_path / "config.json",
        },
    )()
    result = cli._reconcile(args)
    assert github.records == ["parking"]
    assert github.states == []
    assert result["actions"][0]["action_id"] == "park-dispatch-issue-7-a1-g1"

    parked_snapshot = core.apply_observations(
        snapshot,
        [
            {
                "action_id": "park-dispatch-issue-7-a1-g1",
                "status": "succeeded",
                "agent_id": "worker-7",
                "workspace_id": "workspace-7",
                "branch": "work/issue-7",
                "agent_state": "idle",
            }
        ],
    )
    parked_snapshot["issues"][0]["state"] = "blocked"
    snapshot_holder["value"] = parked_snapshot
    args.park = None
    args.resume = "dispatch-issue-7-a1"
    resumed = cli._reconcile(args)
    assert github.records == ["parking", "resuming"]
    assert github.states == ["active"]
    assert resumed["actions"][0]["action_id"] == "resume-dispatch-issue-7-a1-g2"


def test_dependency_states_are_read_in_one_followup_graphql_batch():
    core, cli = _modules()
    client = object.__new__(cli.GitHub)
    calls = []

    def fake_run(args):
        calls.append(args)
        return json.dumps(
            {
                "data": {
                    "repository": {
                        "d3": {"number": 3, "state": "CLOSED"},
                        "d4": {"number": 4, "state": "OPEN"},
                    }
                }
            }
        )

    client.run = fake_run
    states = client.dependency_states("owner/repo", [4, 3, 3])
    assert states == {3: "CLOSED", 4: "OPEN"}
    assert len(calls) == 1
    assert "d3:issue(number:3)" in next(
        value for value in calls[0] if value.startswith("query=")
    )


def test_missing_remote_base_is_fetched_and_read_back_before_dispatch(monkeypatch):
    core, cli = _modules()
    sha = "a" * 40
    checks = iter(
        [
            subprocess.CompletedProcess([], 1, "", "missing"),
            subprocess.CompletedProcess([], 0, "", ""),
        ]
    )
    monkeypatch.setattr(cli.subprocess, "run", lambda *_args, **_kwargs: next(checks))
    commands = []
    monkeypatch.setattr(cli, "_run", lambda args: commands.append(args) or "")

    fetched = cli._ensure_local_base(sha, "dev")

    assert fetched is True
    assert commands == [
        [cli._tool("git", "ORCH_GIT_PATH"), "fetch", "--no-tags", "origin", "dev"]
    ]


def test_one_materialization_failure_does_not_block_the_rest_of_the_wave(monkeypatch):
    core, cli = _modules()
    actions = [
        {"action_id": "bad", "issue": 1},
        {"action_id": "good", "issue": 2},
    ]
    issues = {1: {"number": 1}, 2: {"number": 2}}

    def materialize(action, *_args, **_kwargs):
        if action["action_id"] == "bad":
            raise core.PolicyError("RUNTIME_MODEL_INVALID", "unsupported")
        return {**action, "materialized": True}

    monkeypatch.setattr(cli.core, "materialize_worker_action", materialize)

    class FakeGitHub:
        claims = []

        def claim(self, _repo, issue, action):
            self.claims.append((issue["number"], action["action_id"]))

    github = FakeGitHub()
    materialized, warnings = cli._materialize_worker_wave(
        actions,
        planned_action_ids={"bad", "good"},
        issues_by_number=issues,
        repository="owner/repo",
        base_sha="a" * 40,
        config={},
        runtime={},
        github=github,
    )

    assert [action["action_id"] for action in materialized] == ["good"]
    assert github.claims == [(2, "good")]
    assert warnings == [
        {"code": "RUNTIME_MODEL_INVALID", "issue": 1, "detail": "unsupported"}
    ]


def test_one_claim_failure_does_not_block_the_rest_of_the_wave(monkeypatch):
    _core, cli = _modules()
    actions = [
        {"action_id": "claim-fails", "issue": 1},
        {"action_id": "good", "issue": 2},
    ]
    monkeypatch.setattr(
        cli.core,
        "materialize_worker_action",
        lambda action, *_args, **_kwargs: {**action, "materialized": True},
    )

    class FakeGitHub:
        claims = []

        def claim(self, _repo, issue, action):
            self.claims.append((issue["number"], action["action_id"]))
            if action["action_id"] == "claim-fails":
                raise cli.CommandError("claim readback failed")

    github = FakeGitHub()
    materialized, warnings = cli._materialize_worker_wave(
        actions,
        planned_action_ids={"claim-fails", "good"},
        issues_by_number={1: {"number": 1}, 2: {"number": 2}},
        repository="owner/repo",
        base_sha="a" * 40,
        config={},
        runtime={},
        github=github,
    )

    assert [action["action_id"] for action in materialized] == ["good"]
    assert github.claims == [(1, "claim-fails"), (2, "good")]
    assert warnings == [
        {"code": "COMMAND_FAILED", "issue": 1, "detail": "claim readback failed"}
    ]


def _contract_v2(core, *, path="src/api", dispatch_after=None, merge_after=None):
    contract = {
        "design": ["Implement the admitted change."],
        "acceptance": ["The regression is covered."],
        "change_claims": {"paths": [path], "resources": []},
        "done_when": ["python -m pytest -q"],
        "dependencies": {
            "dispatch_after": list(dispatch_after or []),
            "merge_after": list(merge_after or []),
        },
        "priority": "P1",
        "difficulty": "standard",
        "risk": "standard",
        "unresolved_decisions": [],
    }
    contract["sha256"] = core.contract_hash(contract)
    return contract


def test_frontier_scan_uses_candidate_and_scheduler_production_adapters(monkeypatch):
    core, cli = _modules()
    events = []

    class FakeGitHub:
        def frontier_candidates(self, repo, limit, labels):
            events.append(("candidates", repo, limit, labels))
            return [
                {
                    "number": 23,
                    "title": "Parallel candidate",
                    "body": "Useful details",
                    "labels": [{"name": "ready-for-agent"}],
                    "comments": [],
                }
            ]

        def snapshot(self, repo, branch):
            events.append(("snapshot", repo, branch))
            return {
                "schema_version": 1,
                "repository": repo,
                "issues": [],
                "closed_issues": [],
            }

    config = {
        **core.default_config(),
        "repositories": {
            "owner/repo": {
                "integration_branch": "dev",
                "intake": {
                    "include_labels": ["ready-for-agent"],
                    "candidate_limit": 40,
                    "ready_reserve_target": 6,
                },
            }
        },
    }
    monkeypatch.setattr(cli, "GitHub", FakeGitHub)
    monkeypatch.setattr(cli, "_load_config", lambda *_args, **_kwargs: config)

    args = cli.parse_args(["frontier", "scan", "--repo", "owner/repo"])
    result = cli._frontier(args)

    assert events == [
        (
            "candidates",
            "owner/repo",
            40,
            [
                "ready-for-agent",
                "ready-for-human",
                "needs-info",
                "orch:ready",
                "orch:active",
                "orch:blocked",
            ],
        ),
        ("snapshot", "owner/repo", "dev"),
    ]
    assert result["summary"]["candidate_assessments"] == [
        {"issue": 23, "disposition": "design", "reason": "candidate-label-match"}
    ]
    assert result["summary"]["reserve_gap"] == 6


def test_frontier_admit_validates_the_entire_plan_before_any_github_write(
    tmp_path, monkeypatch
):
    core, cli = _modules()
    writes = []
    valid = _contract_v2(core, path="src/a")
    invalid = _contract_v2(core, path="src/b")
    invalid["sha256"] = "0" * 64
    plan = tmp_path / "admission.json"
    plan.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "repository": "owner/repo",
                "admissions": [
                    {"issue": 1, "contract": valid},
                    {"issue": 2, "contract": invalid},
                ],
            }
        ),
        encoding="utf-8",
    )

    class FakeGitHub:
        def frontier_candidates(self, _repo, _limit, _labels):
            return [
                {"number": 1, "labels": ["ready-for-agent"], "comments": []},
                {"number": 2, "labels": ["ready-for-agent"], "comments": []},
            ]

        def snapshot(self, repo, _branch):
            return {"repository": repo, "issues": [], "closed_issues": []}

        def issues_by_number(self, _repo, numbers):
            return [
                {
                    "number": number,
                    "labels": ["ready-for-agent"],
                    "comments": [],
                }
                for number in numbers
            ]

        def admit(self, repo, candidate, contract):
            writes.append((repo, candidate["number"], contract["sha256"]))

    repo_config = {
        "repository": "owner/repo",
        "integration_branch": "dev",
        "execution_slots": 3,
        "integration_wip_limit": 6,
        "worker_slots": 3,
        "intake": {},
    }
    monkeypatch.setattr(cli, "GitHub", FakeGitHub)
    monkeypatch.setattr(
        cli, "_load_config", lambda *_args, **_kwargs: core.default_config()
    )
    monkeypatch.setattr(
        cli,
        "_coordinator_preflight",
        lambda *_args: (repo_config, {"status": "stable"}),
    )
    args = cli.parse_args(
        [
            "frontier",
            "admit",
            "--repo",
            "owner/repo",
            "--plan",
            str(plan),
            "--coordinator-context",
            "context.json",
        ]
    )

    with pytest.raises(core.PolicyError) as error:
        cli._frontier(args)

    assert error.value.code == "CONTRACT_HASH_MISMATCH"
    assert writes == []


def test_v61_stop_fence_blocks_admission_before_the_first_github_write(
    tmp_path, monkeypatch
):
    core, cli = _modules()
    writes = []
    contract = _contract_v2(core)
    plan = tmp_path / "admission.json"
    plan.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "repository": "owner/repo",
                "admissions": [{"issue": 1, "contract": contract}],
            }
        ),
        encoding="utf-8",
    )

    class FakeGitHub:
        def frontier_candidates(self, _repo, _limit, _labels):
            return [
                {"number": 1, "labels": ["ready-for-agent"], "comments": []}
            ]

        def snapshot(self, repo, _branch):
            return {"repository": repo, "issues": [], "closed_issues": []}

        def issues_by_number(self, _repo, _numbers):
            return [
                {"number": 1, "labels": ["ready-for-agent"], "comments": []}
            ]

        def admit(self, *_args):
            writes.append("admit")

    repo_config = {
        "repository": "owner/repo",
        "integration_branch": "dev",
        "execution_slots": 3,
        "integration_wip_limit": 6,
        "worker_slots": 3,
        "intake": {},
    }
    monkeypatch.setattr(cli, "GitHub", FakeGitHub)
    monkeypatch.setattr(
        cli, "_load_config", lambda *_args, **_kwargs: core.default_config()
    )
    monkeypatch.setattr(
        cli,
        "_coordinator_preflight",
        lambda *_args: (repo_config, {"status": "stable"}),
    )
    monkeypatch.setattr(
        cli,
        "_legacy_writer_stopped",
        lambda repository: repository == "owner/repo",
        raising=False,
    )
    monkeypatch.setattr(
        cli,
        "_resolved_read_only_config",
        lambda *_args: repo_config,
    )
    args = cli.parse_args(
        [
            "frontier",
            "admit",
            "--repo",
            "owner/repo",
            "--plan",
            str(plan),
            "--coordinator-context",
            "context.json",
        ]
    )

    with pytest.raises(core.PolicyError) as error:
        cli._frontier(args)

    assert error.value.code == "V61_WRITER_STOPPED"
    assert writes == []
    read_only = cli._frontier(
        cli.parse_args(["frontier", "scan", "--repo", "owner/repo"])
    )
    assert read_only["status"] == "needs-admission"
    assert writes == []

    def unavailable(_repository):
        raise ValueError("contradictory durable fence")

    monkeypatch.setattr(cli, "_legacy_writer_stopped", unavailable)
    with pytest.raises(core.PolicyError) as unavailable_error:
        cli._frontier(args)
    assert unavailable_error.value.code == "V61_WRITER_FENCE_UNAVAILABLE"
    assert writes == []


def test_production_legacy_writer_readback_uses_github_and_paseo_authority(
    monkeypatch,
):
    _, cli = _modules()

    class FakeContentClient:
        def read(self, _repository, _branch, _path):
            return None

    class FakeGitHub:
        def snapshot(self, repo, branch):
            assert (repo, branch) == ("owner/repo", "dev")
            return {
                "repository": repo,
                "issues": [
                    {
                        "dispatch": {
                            "id": "dispatch-issue-7-a1",
                            "status": "claiming",
                        }
                    },
                    {
                        "dispatch": {
                            "id": "dispatch-issue-8-a1",
                            "status": "retired",
                        }
                    },
                ],
            }

    class FakePaseo:
        def agents_for_labels(self, labels):
            assert labels == {
                "orch.repository": "owner/repo",
                "orch.role": "worker",
            }
            return [
                {"id": "agent-7", "status": "idle", "archivedAt": None},
                {
                    "id": "agent-old",
                    "status": "closed",
                    "archivedAt": "2026-07-24T00:00:00Z",
                },
            ]

    monkeypatch.setattr(cli, "GitHub", FakeGitHub)
    monkeypatch.setattr(cli, "Paseo", FakePaseo)
    monkeypatch.setattr(
        cli,
        "GitHubCliContentClient",
        lambda **_kwargs: FakeContentClient(),
    )

    control = cli.production_legacy_writer_control(
        {"integration_branch": "dev"}
    )
    readback = control.readback("owner/repo")

    assert readback.active_dispatches == ("dispatch-issue-7-a1",)
    assert readback.integration_lease is False
    assert readback.active_workers == ("agent-7",)


def test_github_admit_writes_a_v2_record_then_reads_back_ready_state():
    core, cli = _modules()
    contract = _contract_v2(core)
    calls = []
    client = object.__new__(cli.GitHub)

    def run(args):
        calls.append(args)
        if args[:3] == ["api", "--method", "POST"]:
            return json.dumps({"id": 91, "body": args[-1][5:]})
        if args[:2] == ["issue", "view"]:
            return json.dumps({"labels": [{"name": "orch:ready"}]})
        return ""

    client.run = run
    candidate = {"number": 7, "labels": [], "comments": []}

    result = client.admit("owner/repo", candidate, contract)

    assert result == {"issue": 7, "comment_id": 91, "state": "ready"}
    rendered = next(
        arg[5:] for call in calls for arg in call if arg.startswith("body=")
    )
    assert core.ISSUE_MARKER_V2 in rendered
    assert [call[:2] for call in calls] == [
        ["api", "--method"],
        ["issue", "edit"],
        ["issue", "view"],
    ]


def test_admission_rejects_a_dependency_cycle_through_existing_managed_work():
    core, cli = _modules()
    contract = _contract_v2(core, merge_after=[1])
    plan = {
        "schema_version": 1,
        "repository": "owner/repo",
        "admissions": [{"issue": 2, "contract": contract}],
    }

    with pytest.raises(core.PolicyError) as error:
        cli._validate_admission_plan(
            plan,
            repository="owner/repo",
            candidates=[
                {"number": 2, "labels": ["ready-for-agent"], "comments": []}
            ],
            managed_issues=[
                {
                    "number": 1,
                    "dispatch_after": [],
                    "merge_after": [2],
                }
            ],
        )

    assert error.value.code == "CONTRACT_DEPENDENCY_CYCLE"


@pytest.mark.parametrize(
    "labels",
    [
        [],
        ["needs-triage"],
        ["needs-info"],
        ["ready-for-human"],
        ["wontfix"],
        ["ready-for-agent", "needs-info"],
    ],
)
def test_admission_requires_an_unambiguous_ready_for_agent_label(labels):
    core, cli = _modules()
    contract = _contract_v2(core)
    plan = {
        "schema_version": 1,
        "repository": "owner/repo",
        "admissions": [{"issue": 2, "contract": contract}],
    }

    with pytest.raises(core.PolicyError) as error:
        cli._validate_admission_plan(
            plan,
            repository="owner/repo",
            candidates=[{"number": 2, "labels": labels, "comments": []}],
        )

    assert error.value.code == "ADMISSION_ISSUE_NOT_READY"


def test_admission_preflight_rejects_a_partial_record_with_other_contract():
    core, cli = _modules()
    desired = _contract_v2(core, path="src/desired")
    existing = _contract_v2(core, path="src/existing")
    plan = {
        "schema_version": 1,
        "repository": "owner/repo",
        "admissions": [{"issue": 2, "contract": desired}],
    }
    candidate = {
        "number": 2,
        "labels": ["ready-for-agent"],
        "comments": [
            {
                "id": 90,
                "body": core.render_issue_record(
                    {"contract": existing, "dispatch": None}
                ),
            }
        ],
    }

    with pytest.raises(core.PolicyError) as error:
        cli._validate_admission_plan(
            plan, repository="owner/repo", candidates=[candidate]
        )

    assert error.value.code == "ISSUE_ALREADY_MANAGED"


def test_legacy_worker_slots_gain_the_v61_integration_default_without_rewrite():
    core, cli = _modules()
    legacy = {
        "schema_version": 1,
        "global": {
            "default_tier": "standard",
            "worker_slots": 3,
            "max_attempts": 2,
        },
        "tiers": {},
        "reviewer_tiers": {"standard": "standard", "strict": "heavy"},
        "repositories": {"owner/repo": {"integration_branch": "dev"}},
    }

    assert core.validate_config(legacy) == legacy
    resolved = cli._repository_config(legacy, "owner/repo")
    assert resolved["execution_slots"] == 3
    assert resolved["integration_wip_limit"] == 6
    assert resolved["worker_slots"] == 3


def test_frontier_candidate_adapter_scopes_each_intake_label_and_deduplicates():
    _core, cli = _modules()
    client = object.__new__(cli.GitHub)
    calls = []
    node = {
        "number": 7,
        "title": "Candidate",
        "body": "details",
        "labels": {"nodes": [{"name": "ready-for-agent"}]},
        "assignees": {"nodes": []},
        "comments": {"pageInfo": {"hasNextPage": False}, "nodes": []},
    }

    def run(args):
        calls.append(args)
        return json.dumps(
            {
                "data": {
                    "repository": {
                        "l0": {
                            "pageInfo": {"hasNextPage": False},
                            "nodes": [node],
                        },
                        "l1": {
                            "pageInfo": {"hasNextPage": False},
                            "nodes": [node],
                        },
                    }
                }
            }
        )

    client.run = run
    candidates = client.frontier_candidates(
        "owner/repo", 40, ["ready-for-agent", "needs-info"]
    )

    assert [candidate["number"] for candidate in candidates] == [7]
    query = next(value for value in calls[0] if value.startswith("query="))
    assert "l0:issues" in query and "labels:[$label0]" in query
    assert "l1:issues" in query and "labels:[$label1]" in query
    assert "comments(" not in query
    assert "label0=ready-for-agent" in calls[0]
    assert "label1=needs-info" in calls[0]


def test_admission_detail_adapter_fetches_comments_only_for_target_issues():
    _core, cli = _modules()
    client = object.__new__(cli.GitHub)
    calls = []
    node = {
        "number": 7,
        "title": "Candidate",
        "body": "details",
        "labels": {"nodes": []},
        "assignees": {"nodes": []},
        "comments": {
            "pageInfo": {"hasNextPage": False},
            "nodes": [{"databaseId": 91, "body": "plain context"}],
        },
    }

    def run(args):
        calls.append(args)
        return json.dumps({"data": {"repository": {"i7": node}}})

    client.run = run
    issues = client.issues_by_number("owner/repo", [7])

    assert issues[0]["comments"] == [{"id": 91, **node["comments"]["nodes"][0]}]
    query = next(value for value in calls[0] if value.startswith("query="))
    assert "i7:issue(number:7)" in query
    assert "comments(first:100)" in query


def test_issue_adapter_fails_closed_on_label_pagination():
    core, cli = _modules()
    node = {
        "number": 7,
        "labels": {"pageInfo": {"hasNextPage": True}, "nodes": []},
        "comments": {"pageInfo": {"hasNextPage": False}, "nodes": []},
    }
    with pytest.raises(core.PolicyError) as error:
        cli.GitHub._issue(node)
    assert error.value.code == "SNAPSHOT_PAGINATION_REQUIRED"


def test_frontier_candidates_fail_closed_on_pagination_and_combined_limit():
    core, cli = _modules()
    paginated = {
        "data": {"repository": {"l0": {"pageInfo": {"hasNextPage": True}, "nodes": []}}}
    }
    client = object.__new__(cli.GitHub)
    client.run = lambda _args: json.dumps(paginated)
    with pytest.raises(core.PolicyError) as pagination:
        client.frontier_candidates("owner/repo", 10, ["bug"])
    assert pagination.value.code == "FRONTIER_PAGINATION_REQUIRED"

    over_limit = {
        "data": {
            "repository": {
                "l0": {
                    "pageInfo": {"hasNextPage": False},
                    "nodes": [{"number": number} for number in (1, 2, 3)],
                }
            }
        }
    }
    client.run = lambda _args: json.dumps(over_limit)
    with pytest.raises(core.PolicyError) as limit:
        client.frontier_candidates("owner/repo", 2, ["bug"])
    assert limit.value.code == "FRONTIER_LIMIT_REQUIRED"


def test_frontier_admit_requires_a_plan_before_any_mutation(tmp_path):
    core, cli = _modules()
    args = cli.parse_args(
        [
            "frontier",
            "admit",
            "--repo",
            "owner/repo",
            "--config",
            str(tmp_path / "config.json"),
        ]
    )
    with pytest.raises(core.PolicyError) as error:
        cli._frontier(args)
    assert error.value.code == "ADMISSION_PLAN_REQUIRED"
