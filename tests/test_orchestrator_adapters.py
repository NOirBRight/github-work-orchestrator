from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills" / "orchestrator" / "scripts"


def _modules():
    core_spec = importlib.util.spec_from_file_location(
        "orch_core", SCRIPTS / "orch_core.py"
    )
    core = importlib.util.module_from_spec(core_spec)
    assert core_spec.loader is not None
    sys.modules["orch_core"] = core
    core_spec.loader.exec_module(core)
    cli_spec = importlib.util.spec_from_file_location(
        "orch_adapter_test", SCRIPTS / "orch.py"
    )
    cli = importlib.util.module_from_spec(cli_spec)
    assert cli_spec.loader is not None
    cli_spec.loader.exec_module(cli)
    return core, cli


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
                "issues": {
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
                "issues": {"pageInfo": {"hasNextPage": False}, "nodes": [issue]},
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
                "issues": {"pageInfo": {"hasNextPage": True}, "nodes": []},
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
    detail, cwd, branch, blocker = cli._verified_dispatch_runtime(
        {
            "id": "dispatch-issue-7-a1",
            "worker_agent_id": "worker-7",
            "workspace_id": "wks-read-back-by-mcp",
            "branch": "work/issue-7",
        },
        FakePaseo(),
        "dev",
    )
    assert blocker is None
    assert detail["Id"] == "worker-7"
    assert cwd == worker_repo.resolve()
    assert branch == "work/issue-7"


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
                "worker_slots": 3,
                "repository": "owner/repo",
            },
            [],
            mutate=True,
        )
    assert rejected.value.code == "COORDINATOR_NOT_ROOT"
    assert fake.repairs == []


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
