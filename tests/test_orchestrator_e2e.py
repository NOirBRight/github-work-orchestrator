from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

from conftest import load_core as _core


ROOT = Path(__file__).resolve().parents[1]


def _contract(core, number, difficulty, risk="standard"):
    contract = {
        "design": [f"Implement isolated Issue {number}."],
        "acceptance": [f"Issue {number} regression passes."],
        "hotset": [f"src/module-{number}"],
        "done_when": [f"python -m pytest tests/test_{number}.py -q"],
        "dependencies": [],
        "priority": "P0" if number == 1 else "P1",
        "difficulty": difficulty,
        "risk": risk,
        "unresolved_decisions": [],
    }
    contract["sha256"] = core.contract_hash(contract)
    return contract


def _issue(core, number, contract, state, dispatch=None):
    return {
        "number": number,
        "title": f"Issue {number}",
        "labels": [{"name": f"orch:{state}"}],
        "milestone": None,
        "comments": [
            {
                "id": 1000 + number,
                "body": core.render_issue_record(
                    {"contract": contract, "dispatch": dispatch}
                ),
            }
        ],
    }


def test_rolling_three_worker_wave_runtime_review_merge_and_refill():
    core = _core()
    contracts = {
        1: _contract(core, 1, "standard"),
        2: _contract(core, 2, "heavy", "strict"),
        3: _contract(core, 3, "light", "low"),
        4: _contract(core, 4, "standard"),
    }
    raw = [
        _issue(core, number, contract, "ready")
        for number, contract in contracts.items()
    ]
    frontier = core.normalize_github_snapshot("owner/repo", raw, [])
    frontier.update({"worker_slots": 3, "wave_generation": 0})

    first_wave = core.plan_reconcile(frontier)

    assert first_wave["summary"]["selected"] == [1, 2, 3]
    assert {item["wave_generation"] for item in first_wave["actions"]} == {1}
    coordinator_a = {
        "agent_id": "coordinator-a",
        "provider": "codex",
        "settings": {
            "model": "current",
            "thinkingOptionId": "high",
            "modeId": "full-access",
            "features": {},
        },
    }
    config = {
        **core.default_config(),
        "tiers": {
            "light": {
                "provider": "opencode",
                "settings": {
                    "model": "fast",
                    "thinkingOptionId": "low",
                    "modeId": "build",
                    "features": {},
                },
            },
            "standard": {
                "provider": "codex",
                "settings": {
                    "model": "standard",
                    "thinkingOptionId": "medium",
                    "modeId": "full-access",
                    "features": {},
                },
            },
            "heavy": {
                "provider": "codex",
                "settings": {
                    "model": "heavy",
                    "thinkingOptionId": "max",
                    "modeId": "full-access",
                    "features": {"fast_mode": True},
                },
            },
        },
    }
    materialized = [
        core.materialize_worker_action(
            action,
            next(
                issue
                for issue in frontier["issues"]
                if issue["number"] == action["issue"]
            ),
            repository="owner/repo",
            base_sha="a" * 40,
            config=config,
            coordinator_runtime=coordinator_a,
        )
        for action in first_wave["actions"]
    ]
    assert [
        item["runtime_request"]["settings"]["thinkingOptionId"] for item in materialized
    ] == [
        "medium",
        "max",
        "low",
    ]
    core.resolve_runtime(
        config,
        repository="owner/repo",
        issue={"difficulty": "heavy"},
        coordinator_runtime=coordinator_a,
        capabilities={
            "provider": "codex",
            "models": {"heavy": {"thinking": ["max"]}},
            "modes": ["full-access"],
            "features": ["fast_mode"],
        },
    )

    dispatches = {}
    for action in first_wave["actions"]:
        number = action["issue"]
        dispatches[number] = {
            "id": action["dispatch_id"],
            "attempt": 1,
            "generation": 1,
            "creator_agent_id": "coordinator-a",
            "worker_agent_id": f"worker-{number}",
            "workspace_id": f"workspace-{number}",
            "branch": action["branch"],
            "base_sha": "a" * 40,
            "status": "running",
        }

    candidate_sha = "b" * 40
    delivery = {
        "contract_sha256": contracts[1]["sha256"],
        "candidate_sha": candidate_sha,
        "changed_paths": ["src/module-1/fix.py"],
        "tdd": {"red": "failed", "green": "passed", "refactor": "clean"},
        "verification": ["python -m pytest tests/test_1.py -q"],
        "deviations": [],
        "risks": [],
    }
    review = {
        "candidate_sha": candidate_sha,
        "contract_sha256": contracts[1]["sha256"],
        "axis": "combined",
        "strength": "standard",
        "verdict": "pass",
        "findings": [],
    }
    active_raw = [
        _issue(core, number, contracts[number], "active", dispatches[number])
        for number in (1, 2, 3)
    ] + [_issue(core, 4, contracts[4], "ready")]
    pr = {
        "number": 101,
        "body": core.render_delivery(delivery),
        "headRefName": "work/issue-1",
        "headRefOid": candidate_sha,
        "baseRefName": "dev",
        "isDraft": False,
        "mergeStateStatus": "CLEAN",
        "reviewDecision": None,
        "statusCheckRollup": [{"status": "COMPLETED", "conclusion": "SUCCESS"}],
        "reviews": [
            {
                "body": core.render_review(review),
                "commit": {"oid": candidate_sha},
                "submittedAt": "2026-07-19T10:00:00Z",
            }
        ],
    }
    in_flight = core.normalize_github_snapshot("owner/repo", active_raw, [pr])
    in_flight.update({"worker_slots": 3, "wave_generation": 1})

    assert (
        next(item for item in in_flight["issues"] if item["number"] == 1)["state"]
        == "ready-to-merge"
    )
    assert core.plan_reconcile(in_flight)["summary"]["selected"] == []
    assert [item["number"] for item in core.integration_order(in_flight["issues"])] == [
        1
    ]

    after_merge_raw = [
        _issue(core, number, contracts[number], "active", dispatches[number])
        for number in (2, 3)
    ] + [_issue(core, 4, contracts[4], "ready")]
    after_merge = core.normalize_github_snapshot(
        "owner/repo", after_merge_raw, [], closed_issues=[1]
    )
    after_merge.update({"worker_slots": 3, "wave_generation": 1})
    refill = core.plan_reconcile(after_merge)

    assert refill["summary"]["selected"] == [4]
    assert refill["actions"][0]["wave_generation"] == 2
    coordinator_b = {
        **coordinator_a,
        "agent_id": "coordinator-b",
        "settings": {**coordinator_a["settings"], "thinkingOptionId": "max"},
    }
    fourth = core.materialize_worker_action(
        refill["actions"][0],
        next(issue for issue in after_merge["issues"] if issue["number"] == 4),
        repository="owner/repo",
        base_sha="c" * 40,
        config=config,
        coordinator_runtime=coordinator_b,
    )
    assert fourth["labels"]["orch.creator"] == "coordinator-b"
    assert fourth["relationship"] == "subagent"


def test_cli_park_resume_crash_recovery_crosses_the_production_command_seam(
    tmp_path,
):
    core = _core()
    contract = _contract(core, 7, "standard")
    dispatch_id = "dispatch-issue-7-a1"
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
                "dependencies": [],
                "hotset": contract["hotset"],
                "dispatch": {
                    "id": dispatch_id,
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
                "labels": {"orch.dispatch": dispatch_id},
                "state": "running",
            }
        ],
    }
    script = ROOT / "skills" / "orchestrator" / "scripts" / "orch.py"
    snapshot_path = tmp_path / "snapshot.json"

    def run(command, value):
        snapshot_path.write_text(json.dumps(value), encoding="utf-8")
        result = subprocess.run(
            [
                sys.executable,
                str(script),
                "reconcile",
                "--repo",
                "owner/repo",
                "--snapshot",
                str(snapshot_path),
                command,
                dispatch_id,
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        assert result.returncode == 0, result.stdout + result.stderr
        return json.loads(result.stdout)

    parking = run("--park", snapshot)
    assert parking["actions"][0]["type"] == "stop_worker"
    parking_dispatch = parking["summary"]["record_updates"][0]["dispatch"]

    stopped = {
        **snapshot,
        "issues": [{**snapshot["issues"][0], "dispatch": parking_dispatch}],
        "runtime_agents": [{**snapshot["runtime_agents"][0], "state": "idle"}],
    }
    parked = run("--park", stopped)
    parked_dispatch = parked["summary"]["record_updates"][0]["dispatch"]
    assert parked_dispatch["parked"] is True

    resumable = {
        **stopped,
        "issues": [
            {
                **stopped["issues"][0],
                "state": "blocked",
                "dispatch": parked_dispatch,
            }
        ],
    }
    resuming = run("--resume", resumable)
    assert resuming["actions"][0]["type"] == "resume_worker"
    resuming_dispatch = resuming["summary"]["record_updates"][0]["dispatch"]

    awake = {
        **resumable,
        "issues": [{**resumable["issues"][0], "dispatch": resuming_dispatch}],
        "runtime_agents": [{**snapshot["runtime_agents"][0], "state": "running"}],
    }
    resumed = run("--resume", awake)
    final_dispatch = resumed["summary"]["record_updates"][0]["dispatch"]
    assert final_dispatch["status"] == "running"
    assert final_dispatch["worker_agent_id"] == "worker-7"


def test_non_snapshot_production_path_persists_park_and_resume(tmp_path):
    core = _core()
    real_git = shutil.which("git")
    assert real_git

    def git(cwd, *args):
        result = subprocess.run(
            [real_git, *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        assert result.returncode == 0, result.stderr
        return result.stdout.strip()

    stable = tmp_path / "stable"
    stable.mkdir()
    git(stable, "init", "-b", "dev")
    git(stable, "config", "user.email", "test@example.invalid")
    git(stable, "config", "user.name", "Test")
    (stable / "tracked.txt").write_text("base\n", encoding="utf-8")
    git(stable, "add", "tracked.txt")
    git(stable, "commit", "-m", "base")
    base_sha = git(stable, "rev-parse", "HEAD")

    worker = tmp_path / "worker"
    worker.mkdir()
    git(worker, "init", "-b", "work/issue-7")
    git(worker, "config", "user.email", "test@example.invalid")
    git(worker, "config", "user.name", "Test")
    (worker / "tracked.txt").write_text("worker\n", encoding="utf-8")
    git(worker, "add", "tracked.txt")
    git(worker, "commit", "-m", "worker")

    contract = _contract(core, 7, "standard")
    dispatch_id = "dispatch-issue-7-a1"
    record = {
        "contract": contract,
        "dispatch": {
            "id": dispatch_id,
            "attempt": 1,
            "status": "running",
            "parked": False,
            "worker_agent_id": "worker-7",
            "workspace_id": "workspace-7",
            "branch": "work/issue-7",
            "base_sha": base_sha,
            "contract_sha256": contract["sha256"],
        },
    }
    state_path = tmp_path / "github-state.json"
    state_path.write_text(
        json.dumps(
            {
                "base_sha": base_sha,
                "label": "active",
                "record_body": core.render_issue_record(record),
                "operations": [],
            }
        ),
        encoding="utf-8",
    )
    worker_state = tmp_path / "worker-state.txt"
    worker_state.write_text("running", encoding="utf-8")
    config = {
        **core.default_config(),
        "repositories": {
            "owner/repo": {
                "integration_branch": "dev",
                "workspace_id": "stable-dev",
                "merge_method": "squash",
            }
        },
    }
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")
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
    context_path = tmp_path / "coordinator-context.json"
    context_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "actor": {
                    "id": "root-a",
                    "cwd": str(stable.resolve()),
                    "workspace_id": "stable-dev",
                    "provider": "codex",
                    "settings": {"model": "gpt-5.6", "modeId": "full-access"},
                },
                "current_workspace": workspace,
                "candidate_workspaces": [workspace],
                "mode": {
                    "collaboration_mode": "Default",
                    "write_capable": True,
                    "colorTier": "dangerous",
                },
                "features": {"plan_mode": False},
                "remote_branches": ["dev"],
                "active_root_agents": [{"id": "root-a", "workspace_id": "stable-dev"}],
                "request": "park and resume",
            }
        ),
        encoding="utf-8",
    )

    fixture = ROOT / "tests" / "fixtures" / "fake_orchestrator_runtime.py"

    for command, tool in (
        ("api", "gh"),
        ("issue", "gh"),
        ("inspect", "paseo"),
        ("ls", "paseo"),
    ):
        (stable / command).write_text(
            "import runpy, sys\n"
            f"sys.argv = [{str(fixture)!r}, {tool!r}, {command!r}, *sys.argv[1:]]\n"
            f"runpy.run_path({str(fixture)!r}, run_name='__main__')\n",
            encoding="utf-8",
        )

    def shim(name):
        if os.name == "nt":
            path = tmp_path / f"{name}.cmd"
            path.write_text(
                f'@echo off\r\n"{sys.executable}" "{fixture}" {name} %*\r\n',
                encoding="utf-8",
            )
        else:
            path = tmp_path / name
            path.write_text(
                f'#!/bin/sh\nexec "{sys.executable}" "{fixture}" {name} "$@"\n',
                encoding="utf-8",
            )
            path.chmod(0o755)
        return path

    env = {
        **os.environ,
        "PASEO_AGENT_ID": "root-a",
        "ORCH_GH_PATH": sys.executable,
        "ORCH_PASEO_PATH": sys.executable,
        "ORCH_GIT_PATH": str(shim("git")),
        "ORCH_E2E_STATE": str(state_path),
        "ORCH_E2E_ROOT_CWD": str(stable.resolve()),
        "ORCH_E2E_WORKER_CWD": str(worker.resolve()),
        "ORCH_E2E_WORKER_STATE": str(worker_state),
        "ORCH_E2E_BASE_SHA": base_sha,
        "ORCH_E2E_REAL_GIT": real_git,
    }
    script = ROOT / "skills" / "orchestrator" / "scripts" / "orch.py"

    def reconcile(flag):
        result = subprocess.run(
            [
                sys.executable,
                str(script),
                "reconcile",
                "--repo",
                "owner/repo",
                "--coordinator-context",
                str(context_path),
                "--config",
                str(config_path),
                flag,
                dispatch_id,
            ],
            cwd=stable,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        assert result.returncode == 0, result.stdout + result.stderr
        return json.loads(result.stdout)

    parking = reconcile("--park")
    assert parking["actions"][0]["type"] == "stop_worker"
    worker_state.write_text("idle", encoding="utf-8")
    parked = reconcile("--park")
    assert parked["actions"] == []

    resuming = reconcile("--resume")
    assert resuming["actions"][0]["type"] == "resume_worker"
    worker_state.write_text("running", encoding="utf-8")
    resumed = reconcile("--resume")
    assert resumed["actions"] == []

    final_state = json.loads(state_path.read_text(encoding="utf-8"))
    final_record = core.parse_issue_record(final_state["record_body"])
    assert final_record["dispatch"]["status"] == "running"
    assert final_record["dispatch"]["worker_agent_id"] == "worker-7"
    assert final_state["label"] == "active"
    assert final_state["operations"] == [
        "update_record",
        "update_record",
        "set_state:blocked",
        "update_record",
        "set_state:active",
        "update_record",
    ]


def test_frontier_scan_admit_and_ready_wave_cross_the_production_cli(tmp_path):
    core = _core()
    real_git = shutil.which("git")
    assert real_git

    def git(cwd, *args):
        result = subprocess.run(
            [real_git, *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        assert result.returncode == 0, result.stderr
        return result.stdout.strip()

    stable = tmp_path / "stable"
    stable.mkdir()
    git(stable, "init", "-b", "dev")
    git(stable, "config", "user.email", "test@example.invalid")
    git(stable, "config", "user.name", "Test")
    (stable / "tracked.txt").write_text("base\n", encoding="utf-8")
    git(stable, "add", "tracked.txt")
    git(stable, "commit", "-m", "base")
    base_sha = git(stable, "rev-parse", "HEAD")

    state_path = tmp_path / "github-state.json"
    state_path.write_text(
        json.dumps(
            {
                "scenario": "frontier",
                "base_sha": base_sha,
                "label": None,
                "record_body": None,
                "operations": [],
            }
        ),
        encoding="utf-8",
    )
    config = {
        **core.default_config(),
        "repositories": {
            "owner/repo": {
                "integration_branch": "dev",
                "workspace_id": "stable-dev",
                "merge_method": "squash",
            }
        },
    }
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")
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
    context_path = tmp_path / "coordinator-context.json"
    context_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "actor": {
                    "id": "root-a",
                    "cwd": str(stable.resolve()),
                    "workspace_id": "stable-dev",
                    "provider": "codex",
                    "settings": {"model": "gpt-5.6", "modeId": "full-access"},
                },
                "current_workspace": workspace,
                "candidate_workspaces": [workspace],
                "mode": {
                    "collaboration_mode": "Default",
                    "write_capable": True,
                    "colorTier": "dangerous",
                },
                "features": {"plan_mode": False},
                "remote_branches": ["dev"],
                "active_root_agents": [{"id": "root-a", "workspace_id": "stable-dev"}],
                "request": "admit the parallel frontier",
            }
        ),
        encoding="utf-8",
    )
    contract = {
        "design": ["Implement the isolated API candidate."],
        "acceptance": ["The candidate regression passes."],
        "change_claims": {"paths": ["src/api"], "resources": []},
        "done_when": ["python -m pytest tests/api -q"],
        "dependencies": {"dispatch_after": [], "merge_after": []},
        "priority": "P1",
        "difficulty": "standard",
        "risk": "standard",
        "unresolved_decisions": [],
    }
    contract["sha256"] = core.contract_hash(contract)
    plan_path = tmp_path / "admission.json"
    plan_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "repository": "owner/repo",
                "admissions": [{"issue": 23, "contract": contract}],
            }
        ),
        encoding="utf-8",
    )

    fixture = ROOT / "tests" / "fixtures" / "fake_orchestrator_runtime.py"
    for command, tool in (("api", "gh"), ("issue", "gh"), ("inspect", "paseo")):
        (stable / command).write_text(
            "import runpy, sys\n"
            f"sys.argv = [{str(fixture)!r}, {tool!r}, {command!r}, *sys.argv[1:]]\n"
            f"runpy.run_path({str(fixture)!r}, run_name='__main__')\n",
            encoding="utf-8",
        )

    if os.name == "nt":
        git_shim = tmp_path / "git.cmd"
        git_shim.write_text(
            f'@echo off\r\n"{sys.executable}" "{fixture}" git %*\r\n',
            encoding="utf-8",
        )
    else:
        git_shim = tmp_path / "git"
        git_shim.write_text(
            f'#!/bin/sh\nexec "{sys.executable}" "{fixture}" git "$@"\n',
            encoding="utf-8",
        )
        git_shim.chmod(0o755)
    env = {
        **os.environ,
        "PASEO_AGENT_ID": "root-a",
        "ORCH_GH_PATH": sys.executable,
        "ORCH_PASEO_PATH": sys.executable,
        "ORCH_GIT_PATH": str(git_shim),
        "ORCH_E2E_STATE": str(state_path),
        "ORCH_E2E_ROOT_CWD": str(stable.resolve()),
        "ORCH_E2E_BASE_SHA": base_sha,
        "ORCH_E2E_REAL_GIT": real_git,
    }
    script = ROOT / "skills" / "orchestrator" / "scripts" / "orch.py"

    def run(*arguments):
        result = subprocess.run(
            [sys.executable, str(script), *arguments, "--config", str(config_path)],
            cwd=stable,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        assert result.returncode == 0, result.stdout + result.stderr
        return json.loads(result.stdout)

    scanned = run("frontier", "scan", "--repo", "owner/repo")
    assert scanned["status"] == "needs-admission"
    assert scanned["summary"]["candidate_assessments"] == [
        {"issue": 23, "disposition": "design", "reason": "candidate-label-match"}
    ]

    admitted = run(
        "frontier",
        "admit",
        "--repo",
        "owner/repo",
        "--plan",
        str(plan_path),
        "--coordinator-context",
        str(context_path),
    )
    assert admitted["summary"]["admitted"] == [
        {"issue": 23, "comment_id": 91, "state": "ready"}
    ]
    durable = json.loads(state_path.read_text(encoding="utf-8"))
    assert durable["label"] == "ready"
    assert durable["operations"] == ["admit_record", "set_state:ready"]
    assert core.ISSUE_MARKER_V2 in durable["record_body"]

    ready = run("frontier", "scan", "--repo", "owner/repo")
    assert ready["summary"]["candidate_assessments"][0]["disposition"] == "managed"
    assert ready["summary"]["ready_reserve"] == 1
    assert ready["summary"]["wave"]["selected"] == [23]
