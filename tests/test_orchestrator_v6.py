from __future__ import annotations

import importlib.util
import json
import multiprocessing
from pathlib import Path
import subprocess
import sys
import time

import pytest


ROOT = Path(__file__).resolve().parents[1]
CORE_PATH = ROOT / "skills" / "orchestrator" / "scripts" / "orch_core.py"


def load_core():
    spec = importlib.util.spec_from_file_location("orch_core_v6", CORE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_reconcile_fills_free_slots_with_ranked_disjoint_issues():
    core = load_core()
    snapshot = {
        "schema_version": 1,
        "repository": "owner/repo",
        "worker_slots": 3,
        "wave_generation": 4,
        "issues": [
            {
                "number": 9,
                "state": "active",
                "priority": "P1",
                "hotset": ["src/auth"],
                "dispatch": {"status": "running"},
            },
            {
                "number": 12,
                "state": "ready",
                "priority": "P0",
                "hotset": ["docs"],
                "dependencies": [],
                "contract_valid": True,
                "attempt": 1,
            },
            {
                "number": 15,
                "state": "ready",
                "priority": "P1",
                "hotset": ["src/api"],
                "dependencies": [],
                "contract_valid": True,
                "attempt": 1,
            },
            {
                "number": 18,
                "state": "ready",
                "priority": "P1",
                "hotset": ["src/auth/token.py"],
                "dependencies": [],
                "contract_valid": True,
                "attempt": 1,
            },
            {
                "number": 20,
                "state": "ready",
                "priority": "P0",
                "hotset": ["src/payments"],
                "dependencies": [99],
                "contract_valid": True,
                "attempt": 1,
            },
        ],
        "closed_issues": [],
    }

    result = core.plan_reconcile(snapshot)

    assert result == {
        "schema_version": 1,
        "status": "actions",
        "actions": [
            {
                "action_id": "create-worker-dispatch-issue-12-a1",
                "type": "create_worker",
                "dispatch_id": "dispatch-issue-12-a1",
                "issue": 12,
                "attempt": 1,
                "branch": "work/issue-12",
                "wave_generation": 5,
            },
            {
                "action_id": "create-worker-dispatch-issue-15-a1",
                "type": "create_worker",
                "dispatch_id": "dispatch-issue-15-a1",
                "issue": 15,
                "attempt": 1,
                "branch": "work/issue-15",
                "wave_generation": 5,
            },
        ],
        "warnings": [],
        "summary": {
            "worker_slots": 3,
            "wip": 1,
            "free_slots": 2,
            "selected": [12, 15],
            "deferred": {
                "18": "hotset-conflict",
                "20": "open-dependencies",
            },
        },
    }


def test_unknown_hotset_runs_exclusively():
    core = load_core()
    snapshot = {
        "schema_version": 1,
        "repository": "owner/repo",
        "worker_slots": 3,
        "wave_generation": 0,
        "issues": [
            {
                "number": 1,
                "state": "ready",
                "priority": "P0",
                "hotset": [],
                "dependencies": [],
                "contract_valid": True,
                "attempt": 1,
            },
            {
                "number": 2,
                "state": "ready",
                "priority": "P1",
                "hotset": ["docs"],
                "dependencies": [],
                "contract_valid": True,
                "attempt": 1,
            },
        ],
        "closed_issues": [],
    }

    result = core.plan_reconcile(snapshot)

    assert result["summary"]["selected"] == [1]
    assert result["summary"]["deferred"] == {"2": "exclusive-hotset"}


def test_runtime_resolution_uses_issue_tier_and_validates_thinking_mode_features():
    core = load_core()
    config = {
        "schema_version": 1,
        "global": {"default_tier": "standard"},
        "tiers": {
            "standard": {
                "provider": "opencode",
                "settings": {"model": "m-standard", "thinkingOptionId": "low"},
            },
            "heavy": {
                "provider": "opencode",
                "settings": {
                    "model": "m-heavy",
                    "thinkingOptionId": "high",
                    "modeId": "build",
                    "features": {"auto_accept": True},
                },
            },
        },
        "repositories": {"owner/repo": {"default_tier": "standard"}},
    }
    capabilities = {
        "provider": "opencode",
        "models": {
            "m-heavy": {"thinking": ["low", "high"]},
            "m-standard": {"thinking": ["low"]},
        },
        "modes": ["build"],
        "features": ["auto_accept"],
    }
    coordinator_runtime = {
        "provider": "codex",
        "settings": {"model": "coordinator-model", "modeId": "full-access"},
    }

    result = core.resolve_runtime(
        config,
        repository="owner/repo",
        issue={"difficulty": "heavy"},
        coordinator_runtime=coordinator_runtime,
        capabilities=capabilities,
    )

    assert result == {
        "tier": "heavy",
        "provider": "opencode",
        "settings": {
            "model": "m-heavy",
            "thinkingOptionId": "high",
            "modeId": "build",
            "features": {"auto_accept": True},
        },
    }


def test_runtime_resolution_fails_closed_when_thinking_or_features_are_ambiguous():
    core = load_core()
    config = {
        "schema_version": 1,
        "global": {"default_tier": "standard"},
        "tiers": {
            "standard": {
                "provider": "opencode",
                "settings": {"model": "m-standard", "modeId": "build"},
            }
        },
        "repositories": {},
    }
    capabilities = {
        "provider": "opencode",
        "models": {"m-standard": {"thinking": ["low", "high"]}},
        "modes": ["build"],
        "features": ["draft"],
    }
    coordinator = {
        "provider": "codex",
        "settings": {
            "model": "current",
            "thinkingOptionId": "max",
            "modeId": "full-access",
        },
    }

    with __import__("pytest").raises(core.PolicyError) as missing_thinking:
        core.resolve_runtime(
            config,
            repository="owner/repo",
            issue={},
            coordinator_runtime=coordinator,
            capabilities=capabilities,
        )
    assert missing_thinking.value.code == "RUNTIME_THINKING_MISSING"

    config["tiers"]["standard"]["settings"]["thinkingOptionId"] = "low"
    with __import__("pytest").raises(core.PolicyError) as missing_features:
        core.resolve_runtime(
            config,
            repository="owner/repo",
            issue={},
            coordinator_runtime=coordinator,
            capabilities=capabilities,
        )
    assert missing_features.value.code == "RUNTIME_FEATURES_AMBIGUOUS"


def test_runtime_resolution_inherits_complete_same_provider_runtime_only():
    core = load_core()
    config = {
        "schema_version": 1,
        "global": {"default_tier": "standard"},
        "tiers": {
            "standard": {
                "provider": "opencode",
                "settings": {"model": "m-standard"},
            }
        },
        "repositories": {},
    }
    capabilities = {
        "provider": "opencode",
        "models": {"m-standard": {"thinking": ["low", "high"]}},
        "modes": ["build", "plan"],
        "features": ["draft"],
    }
    coordinator = {
        "provider": "opencode",
        "settings": {
            "model": "m-standard",
            "thinkingOptionId": "high",
            "modeId": "build",
            "features": {"draft": False},
        },
    }

    resolved = core.resolve_runtime(
        config,
        repository="owner/repo",
        issue={},
        coordinator_runtime=coordinator,
        capabilities=capabilities,
    )
    assert resolved["settings"] == coordinator["settings"]


def test_issue_record_round_trips_without_local_paths_or_private_prompt():
    core = load_core()
    record = {
        "contract": {
            "sha256": "a" * 64,
            "priority": "P1",
            "difficulty": "standard",
            "risk": "standard",
            "hotset": ["src/api"],
            "done_when": ["python -m pytest tests/api -q"],
            "dependencies": [],
            "design": ["Preserve the public API.", "Add a regression test first."],
        },
        "dispatch": {
            "id": "dispatch-issue-15-a1",
            "attempt": 1,
            "generation": 5,
            "creator_agent_id": "agent-a",
            "worker_agent_id": None,
            "branch": "work/issue-15",
            "base_sha": "b" * 40,
            "status": "claiming",
        },
    }

    rendered = core.render_issue_record(record)

    assert rendered.startswith("<!-- orchestrator:issue:v1 -->\n```json\n")
    assert core.parse_issue_record(rendered) == record
    with __import__("pytest").raises(core.PolicyError, match="local absolute path"):
        core.render_issue_record({**record, "worktree_path": "C:\\private\\repo"})
    with __import__("pytest").raises(core.PolicyError, match="private prompt"):
        core.render_issue_record({**record, "private_prompt": "secret"})
    for unsafe in (
        "prefix C:\\Users\\name\\repo suffix",
        "see file:///home/name/private",
        "share \\\\server\\private",
        "run /home/name/private/tool",
        "token ghp_abcdefghijklmnopqrstuvwxyz123456",
    ):
        with __import__("pytest").raises(core.PolicyError):
            core.render_issue_record({**record, "note": unsafe})


def test_workspace_qualification_allows_dirty_planning_but_guards_mutation():
    core = load_core()
    workspace = {
        "repository": "owner/repo",
        "branch": "dev",
        "relationship": "root",
        "dirty": True,
        "pr_head": False,
        "ephemeral": False,
        "worker": False,
    }
    config = {"repository": "owner/repo", "integration_branch": "dev"}

    assert core.qualify_workspace(workspace, config, operation="reconcile-read") == {
        "eligible": True,
        "merge_allowed": False,
    }
    with __import__("pytest").raises(core.PolicyError) as dirty:
        core.qualify_workspace(workspace, config, operation="integrate")
    assert dirty.value.code == "WORKSPACE_DIRTY"

    with __import__("pytest").raises(core.PolicyError) as feature:
        core.qualify_workspace(
            {**workspace, "dirty": False, "branch": "work/issue-7"},
            config,
            operation="reconcile-write",
        )
    assert feature.value.code == "WORKSPACE_NOT_INTEGRATION"

    with __import__("pytest").raises(core.PolicyError) as child:
        core.qualify_workspace(
            {**workspace, "dirty": False, "relationship": "subagent"},
            config,
            operation="reconcile-write",
        )
    assert child.value.code == "COORDINATOR_NOT_ROOT"
    with __import__("pytest").raises(core.PolicyError) as cwd:
        core.qualify_workspace(
            {**workspace, "dirty": False, "agent_cwd_matches": False},
            config,
            operation="reconcile-write",
        )
    assert cwd.value.code == "WORKSPACE_AGENT_CWD_MISMATCH"


def test_worker_slots_are_bounded_and_blocked_only_releases_when_parked():
    core = load_core()
    base = {
        "schema_version": 1,
        "repository": "owner/repo",
        "wave_generation": 2,
        "issues": [
            {
                "number": 1,
                "state": "blocked",
                "hotset": ["src/a"],
                "dispatch": {"parked": False},
            },
            {
                "number": 2,
                "state": "blocked",
                "hotset": ["src/b"],
                "dispatch": {"parked": True},
            },
            {
                "number": 3,
                "state": "review",
                "hotset": ["src/c"],
                "dispatch": {},
            },
            {
                "number": 4,
                "state": "ready-to-merge",
                "hotset": ["src/d"],
                "dispatch": {},
            },
            {
                "number": 5,
                "state": "ready",
                "priority": "P0",
                "hotset": ["src/e"],
                "dependencies": [],
                "contract_valid": True,
                "attempt": 1,
            },
        ],
        "closed_issues": [],
    }

    result = core.plan_reconcile({**base, "worker_slots": 4})

    assert result["summary"]["wip"] == 3
    assert result["summary"]["selected"] == [5]
    for invalid in (0, 6):
        with __import__("pytest").raises(core.PolicyError) as error:
            core.plan_reconcile({**base, "worker_slots": invalid})
        assert error.value.code == "WORKER_SLOTS_INVALID"


def _lifecycle_snapshot(core, *, status="running", parked=False):
    contract = {
        "design": ["Keep the existing behavior."],
        "acceptance": ["The managed change remains verified."],
        "hotset": ["src/worker"],
        "done_when": ["python -m pytest -q"],
        "dependencies": [3],
        "priority": "P1",
        "difficulty": "standard",
        "risk": "standard",
        "unresolved_decisions": [],
    }
    contract["sha256"] = core.contract_hash(contract)
    dispatch = {
        "id": "dispatch-issue-7-a1",
        "attempt": 1,
        "status": status,
        "parked": parked,
        "worker_agent_id": "worker-7",
        "workspace_id": "workspace-7",
        "branch": "work/issue-7",
        "base_sha": "a" * 40,
        "contract_sha256": contract["sha256"],
    }
    return {
        "schema_version": 1,
        "repository": "owner/repo",
        "base_sha": "a" * 40,
        "worker_slots": 3,
        "closed_issues": [3],
        "issues": [
            {
                "number": 7,
                "state": "blocked" if parked else "active",
                "contract": contract,
                "contract_valid": True,
                "dependencies": [3],
                "hotset": ["src/worker"],
                "dispatch": dispatch,
            }
        ],
        "runtime_agents": [
            {
                "id": "worker-7",
                "workspace_id": "workspace-7",
                "branch": "work/issue-7",
                "labels": {"orch.dispatch": "dispatch-issue-7-a1"},
                "state": "idle" if parked else "running",
            }
        ],
    }


def test_park_is_two_phase_and_releases_capacity_only_after_stop_readback():
    core = load_core()
    snapshot = _lifecycle_snapshot(core)
    planned = core.plan_lifecycle_command(snapshot, "dispatch-issue-7-a1", "park")
    update = planned["record_updates"][0]["dispatch"]
    assert update["status"] == "parking"
    assert update["parked"] is False
    assert planned["actions"] == [
        {
            "action_id": "park-dispatch-issue-7-a1-g1",
            "type": "stop_worker",
            "dispatch_id": "dispatch-issue-7-a1",
            "agent_id": "worker-7",
        }
    ]
    in_transition = {
        **snapshot,
        "issues": [{**snapshot["issues"][0], "dispatch": update}],
    }
    assert core.plan_reconcile(in_transition)["summary"]["wip"] == 1

    stopped = core.apply_observations(
        in_transition,
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
    parked = stopped["issues"][0]["dispatch"]
    assert parked["status"] == "blocked"
    assert parked["parked"] is True
    assert core.plan_reconcile(stopped)["summary"]["wip"] == 0
    duplicate = core.apply_observations(
        stopped,
        [{"action_id": "park-dispatch-issue-7-a1-g1", "status": "succeeded"}],
    )
    assert duplicate["issues"][0]["dispatch"] == parked


def test_park_crash_recovery_reuses_action_and_accepts_stopped_agent_readback():
    core = load_core()
    snapshot = _lifecycle_snapshot(core)
    initial = core.plan_lifecycle_command(snapshot, "dispatch-issue-7-a1", "park")
    transition = {
        **snapshot,
        "issues": [
            {
                **snapshot["issues"][0],
                "dispatch": initial["record_updates"][0]["dispatch"],
            }
        ],
    }
    repeated = core.plan_lifecycle_transitions(transition)
    assert repeated["actions"] == initial["actions"]

    transition["runtime_agents"] = [{**snapshot["runtime_agents"][0], "state": "idle"}]
    recovered = core.plan_lifecycle_transitions(transition)
    assert recovered["actions"] == []
    assert recovered["record_updates"][0]["dispatch"]["parked"] is True


def test_resume_revalidates_then_wakes_the_same_worker_in_two_phases():
    core = load_core()
    snapshot = _lifecycle_snapshot(core, status="blocked", parked=True)
    planned = core.plan_lifecycle_command(snapshot, "dispatch-issue-7-a1", "resume")
    update = planned["record_updates"][0]["dispatch"]
    assert update["status"] == "resuming"
    assert update["parked"] is False
    assert planned["actions"] == [
        {
            "action_id": "resume-dispatch-issue-7-a1-g1",
            "type": "resume_worker",
            "dispatch_id": "dispatch-issue-7-a1",
            "agent_id": "worker-7",
            "message": "Resume Dispatch dispatch-issue-7-a1 from its unchanged contract and preserved WIP.",
        }
    ]
    transition = {
        **snapshot,
        "issues": [{**snapshot["issues"][0], "dispatch": update}],
    }
    assert core.plan_reconcile(transition)["summary"]["wip"] == 1
    with pytest.raises(core.PolicyError) as drifted_recovery:
        core.plan_lifecycle_transitions({**transition, "base_sha": "b" * 40})
    assert drifted_recovery.value.code == "RESUME_BASE_DRIFT"
    failed = core.apply_observations(
        transition,
        [
            {
                "action_id": "resume-dispatch-issue-7-a1-g1",
                "status": "failed",
                "error": "wake readback unavailable",
            }
        ],
    )
    failed_dispatch = failed["issues"][0]["dispatch"]
    assert failed_dispatch["status"] == "resuming"
    assert failed_dispatch["parked"] is False
    assert failed_dispatch["lifecycle_action_id"] == ("resume-dispatch-issue-7-a1-g1")
    assert core.plan_lifecycle_transitions(failed)["actions"] == planned["actions"]
    running = core.apply_observations(
        transition,
        [
            {
                "action_id": "resume-dispatch-issue-7-a1-g1",
                "status": "succeeded",
                "agent_id": "worker-7",
                "workspace_id": "workspace-7",
                "branch": "work/issue-7",
                "agent_state": "running",
            }
        ],
    )["issues"][0]["dispatch"]
    assert running["status"] == "running"
    assert running["parked"] is False
    assert running["worker_agent_id"] == "worker-7"
    duplicate = core.apply_observations(
        {**transition, "issues": [{**transition["issues"][0], "dispatch": running}]},
        [
            {
                "action_id": "resume-dispatch-issue-7-a1-g1",
                "status": "succeeded",
            }
        ],
    )
    assert duplicate["issues"][0]["dispatch"] == running


def test_lifecycle_success_requires_exact_worker_state_readback():
    core = load_core()
    snapshot = _lifecycle_snapshot(core)
    update = core.plan_lifecycle_command(snapshot, "dispatch-issue-7-a1", "park")[
        "record_updates"
    ][0]["dispatch"]
    transition = {
        **snapshot,
        "issues": [{**snapshot["issues"][0], "dispatch": update}],
    }
    for observation in (
        {"action_id": "park-dispatch-issue-7-a1-g1", "status": "succeeded"},
        {
            "action_id": "park-dispatch-issue-7-a1-g1",
            "status": "succeeded",
            "agent_id": "worker-7",
            "workspace_id": "workspace-7",
            "branch": "work/issue-7",
            "agent_state": "running",
        },
    ):
        with pytest.raises(core.PolicyError):
            core.apply_observations(transition, [observation])


@pytest.mark.parametrize(
    ("mutation", "code"),
    [
        ("base", "RESUME_BASE_DRIFT"),
        ("contract", "RESUME_CONTRACT_INVALID"),
        ("dependency", "RESUME_DEPENDENCY_BLOCKED"),
        ("hotset", "RESUME_HOTSET_CONFLICT"),
    ],
)
def test_resume_fails_closed_on_drift_or_conflict(mutation, code):
    core = load_core()
    snapshot = _lifecycle_snapshot(core, status="blocked", parked=True)
    if mutation == "base":
        snapshot["base_sha"] = "b" * 40
    elif mutation == "contract":
        snapshot["issues"][0]["contract"]["design"] = ["Changed while parked."]
    elif mutation == "dependency":
        snapshot["closed_issues"] = []
    else:
        snapshot["issues"].append(
            {
                "number": 8,
                "state": "active",
                "hotset": ["src/worker/api"],
                "dispatch": {"status": "running", "parked": False},
            }
        )
    with pytest.raises(core.PolicyError) as rejected:
        core.plan_lifecycle_command(snapshot, "dispatch-issue-7-a1", "resume")
    assert rejected.value.code == code


def test_p0_at_full_capacity_is_advisory_only():
    core = load_core()
    result = core.plan_reconcile(
        {
            "repository": "owner/repo",
            "worker_slots": 1,
            "issues": [
                {"number": 1, "state": "active", "hotset": ["src/a"]},
                {
                    "number": 2,
                    "state": "ready",
                    "priority": "P0",
                    "hotset": ["src/b"],
                    "dependencies": [],
                    "contract_valid": True,
                },
            ],
        }
    )

    assert result["actions"] == []
    assert result["warnings"] == [
        {"code": "P0_CAPACITY_FULL", "issues": [2], "preemption": "manual-only"}
    ]


def test_manifests_lockfiles_and_generated_inputs_conflict_implicitly():
    core = load_core()
    assert core.hotsets_overlap(["package.json"], ["package-lock.json"])
    assert core.hotsets_overlap(["db/schema.sql"], ["db/migrations/001.sql"])
    assert core.hotsets_overlap(["proto/service.proto"], ["src/generated/service.py"])
    assert not core.hotsets_overlap(["src/a"], ["src/b"])


def test_claiming_dispatch_waits_then_reemits_same_action_without_new_attempt():
    core = load_core()
    snapshot = {
        "now": "2026-07-19T10:03:01Z",
        "claim_grace_seconds": 120,
        "issues": [
            {
                "number": 7,
                "state": "active",
                "hotset": ["src/a"],
                "dispatch": {
                    "id": "dispatch-issue-7-a1",
                    "attempt": 1,
                    "status": "claiming",
                    "claimed_at": "2026-07-19T10:00:00Z",
                    "branch": "work/issue-7",
                    "worker_agent_id": None,
                },
            }
        ],
        "runtime_agents": [],
        "runtime_worktrees": [],
    }

    result = core.plan_partial_dispatch(snapshot)

    assert result["actions"] == [
        {
            "action_id": "create-worker-dispatch-issue-7-a1",
            "type": "create_worker",
            "dispatch_id": "dispatch-issue-7-a1",
            "issue": 7,
            "attempt": 1,
            "branch": "work/issue-7",
            "reuse_workspace_id": None,
        }
    ]
    snapshot["now"] = "2026-07-19T10:01:00Z"
    assert core.plan_partial_dispatch(snapshot)["actions"] == []


def test_worktree_only_partial_success_is_reused_by_same_dispatch_action():
    core = load_core()
    result = core.plan_partial_dispatch(
        {
            "now": "2026-07-19T10:03:00Z",
            "issues": [
                {
                    "number": 7,
                    "dispatch": {
                        "id": "dispatch-issue-7-a1",
                        "attempt": 1,
                        "status": "claiming",
                        "claimed_at": "2026-07-19T10:00:00Z",
                        "branch": "work/issue-7",
                    },
                }
            ],
            "runtime_agents": [],
            "runtime_worktrees": [
                {"workspace_id": "workspace-7", "branch": "work/issue-7"}
            ],
        }
    )
    assert result["actions"][0]["reuse_workspace_id"] == "workspace-7"
    assert result["record_updates"][0]["dispatch"]["workspace_id"] == "workspace-7"

    path_only = core.plan_partial_dispatch(
        {
            "now": "2026-07-19T10:03:00Z",
            "issues": [
                {
                    "number": 7,
                    "dispatch": {
                        "id": "dispatch-issue-7-a1",
                        "attempt": 1,
                        "status": "claiming",
                        "claimed_at": "2026-07-19T10:00:00Z",
                        "branch": "work/issue-7",
                    },
                }
            ],
            "runtime_agents": [],
            "runtime_worktrees": [
                {"path": "C:/safe/worker-7", "branch": "work/issue-7"}
            ],
        }
    )
    assert path_only["actions"][0]["reuse_workspace_path"] == "C:/safe/worker-7"
    assert path_only["record_updates"] == []


def test_claiming_dispatch_is_wip_even_if_github_label_repair_is_pending():
    core = load_core()
    issue = {
        "number": 7,
        "state": "ready",
        "priority": "P0",
        "contract_valid": True,
        "dependencies": [],
        "hotset": ["src/a"],
        "dispatch": {
            "id": "dispatch-issue-7-a1",
            "attempt": 1,
            "status": "claiming",
            "claimed_at": "2026-07-19T10:00:00Z",
            "branch": "work/issue-7",
        },
    }
    scheduled = core.plan_reconcile(
        {"repository": "owner/repo", "worker_slots": 3, "issues": [issue]}
    )
    partial = core.plan_partial_dispatch(
        {
            "now": "2026-07-19T10:03:00Z",
            "issues": [issue],
            "runtime_agents": [],
        }
    )
    assert scheduled["actions"] == []
    assert scheduled["summary"]["wip"] == 1
    assert [item["action_id"] for item in partial["actions"]] == [
        "create-worker-dispatch-issue-7-a1"
    ]
    assert core.plan_issue_state_repairs([issue]) == [{"issue": 7, "state": "active"}]


def test_observation_or_runtime_readback_completes_partial_dispatch_idempotently():
    core = load_core()
    record = {
        "id": "dispatch-issue-7-a1",
        "attempt": 1,
        "status": "claiming",
        "branch": "work/issue-7",
        "worker_agent_id": None,
    }
    observation = {
        "action_id": "create-worker-dispatch-issue-7-a1",
        "status": "succeeded",
        "agent_id": "agent-7",
        "workspace_id": "workspace-7",
        "branch": "work/issue-7",
        "error": None,
    }

    updated = core.apply_dispatch_observation(record, observation)

    assert updated["status"] == "running"
    assert updated["worker_agent_id"] == "agent-7"
    assert updated["workspace_id"] == "workspace-7"
    assert core.apply_dispatch_observation(updated, observation) == updated
    with __import__("pytest").raises(core.PolicyError) as mismatch:
        core.apply_dispatch_observation(
            updated, {**observation, "agent_id": "different-agent"}
        )
    assert mismatch.value.code == "OBSERVATION_IDENTITY_CONFLICT"


def test_reviewer_observation_is_accepted_without_becoming_worker_state():
    core = load_core()
    contract = _contract(core, risk="standard")
    candidate = "b" * 40
    snapshot = {
        "issues": [
            {
                "number": 7,
                "state": "review",
                "contract": contract,
                "labels": [],
                "reviews": [],
                "pr": {"number": 17, "head_sha": candidate},
            }
        ],
        "runtime_agents": [],
    }
    action_id = f"create-reviewer-pr-17-{candidate[:12]}-combined"
    updated = core.apply_observations(
        snapshot,
        [
            {
                "action_id": action_id,
                "status": "succeeded",
                "agent_id": "reviewer-17",
                "workspace_id": "integration-wt",
                "branch": "dev",
                "error": None,
            }
        ],
    )
    reviewer = updated["runtime_agents"][0]
    assert reviewer["id"] == "reviewer-17"
    assert reviewer["labels"] == {"orch.action": action_id, "orch.role": "reviewer"}
    assert updated["issues"][0].get("dispatch") is None


def test_existing_agent_readback_prevents_duplicate_creation():
    core = load_core()
    result = core.plan_partial_dispatch(
        {
            "now": "2026-07-19T10:10:00Z",
            "issues": [
                {
                    "number": 7,
                    "state": "active",
                    "dispatch": {
                        "id": "dispatch-issue-7-a1",
                        "attempt": 1,
                        "status": "claiming",
                        "claimed_at": "2026-07-19T10:00:00Z",
                        "branch": "work/issue-7",
                    },
                }
            ],
            "runtime_agents": [
                {
                    "id": "agent-7",
                    "labels": {"gwo.dispatch": "dispatch-issue-7-a1"},
                    "workspace_id": "workspace-7",
                    "branch": "work/issue-7",
                    "state": "running",
                }
            ],
            "runtime_worktrees": [],
        }
    )

    assert result["actions"] == []
    assert result["record_updates"][0]["dispatch"]["worker_agent_id"] == "agent-7"

    pending_workspace = core.plan_partial_dispatch(
        {
            "now": "2026-07-19T10:10:00Z",
            "issues": [
                {
                    "number": 7,
                    "dispatch": {
                        "id": "dispatch-issue-7-a1",
                        "attempt": 1,
                        "status": "claiming",
                        "claimed_at": "2026-07-19T10:00:00Z",
                        "branch": "work/issue-7",
                    },
                }
            ],
            "runtime_agents": [
                {
                    "id": "agent-7",
                    "labels": {"orch.dispatch": "dispatch-issue-7-a1"},
                    "workspace_id": None,
                    "branch": "work/issue-7",
                    "state": "running",
                }
            ],
        }
    )
    assert pending_workspace["actions"] == []
    assert pending_workspace["record_updates"] == []
    assert pending_workspace["warnings"] == [
        {
            "code": "WORKSPACE_IDENTITY_PENDING",
            "dispatch": "dispatch-issue-7-a1",
        }
    ]


def test_recovery_attempts_reuse_workspace_and_second_failure_blocks():
    core = load_core()
    first = core.plan_worker_recovery(
        {
            "dispatch": {
                "id": "dispatch-issue-7-a1",
                "attempt": 1,
                "status": "running",
                "worker_agent_id": "agent-7",
                "workspace_id": "workspace-7",
                "branch": "work/issue-7",
                "recovery_prompt_sent": False,
            },
            "agent": {"id": "agent-7", "state": "idle"},
            "max_attempts": 2,
        }
    )
    assert first["actions"][0]["type"] == "send_prompt"
    assert first["actions"][0]["prompt_kind"] == "recover-once"

    replacement = core.plan_worker_recovery(
        {
            "dispatch": {
                **first["dispatch_update"],
                "status": "error",
            },
            "agent": {"id": "agent-7", "state": "error"},
            "max_attempts": 2,
        }
    )
    assert replacement["actions"] == [
        {
            "action_id": "create-worker-dispatch-issue-7-a2",
            "type": "create_worker",
            "dispatch_id": "dispatch-issue-7-a2",
            "issue": 7,
            "attempt": 2,
            "branch": "work/issue-7",
            "reuse_workspace_id": "workspace-7",
        }
    ]

    exhausted = core.plan_worker_recovery(
        {
            "dispatch": {
                "id": "dispatch-issue-7-a2",
                "attempt": 2,
                "status": "error",
                "worker_agent_id": "agent-8",
                "workspace_id": "workspace-7",
                "branch": "work/issue-7",
                "recovery_prompt_sent": True,
            },
            "agent": {"id": "agent-8", "state": "error"},
            "max_attempts": 2,
        }
    )
    assert exhausted["actions"] == []
    assert exhausted["next_issue_state"] == "blocked"


def test_review_plan_is_graded_and_commit_bound():
    core = load_core()
    low = core.plan_review(
        {"level": "low", "candidate_sha": "a" * 40, "checks": "green"}
    )
    assert low["reviewers"] == []
    assert low["coordinator_review_required"] is True

    standard = core.plan_review(
        {"level": "standard", "candidate_sha": "b" * 40, "checks": "none"}
    )
    assert [item["axis"] for item in standard["reviewers"]] == ["combined"]
    assert standard["local_verification_required"] is True

    strict = core.plan_review(
        {
            "level": "strict",
            "candidate_sha": "c" * 40,
            "checks": "none",
            "substitute_evidence_defined": False,
        }
    )
    assert strict["human_gate_required"] is True
    assert strict["reviewers"][0]["strength"] == "heavy"

    dual = core.plan_review(
        {
            "level": "standard",
            "candidate_sha": "d" * 40,
            "checks": "green",
            "dual": True,
        }
    )
    assert [item["axis"] for item in dual["reviewers"]] == ["spec", "quality"]
    assert all(item["candidate_sha"] == "d" * 40 for item in dual["reviewers"])


def test_review_verdict_must_match_current_commit():
    core = load_core()
    with __import__("pytest").raises(core.PolicyError) as stale:
        core.validate_review_verdict(
            {"candidate_sha": "a" * 40, "verdict": "pass"}, "b" * 40
        )
    assert stale.value.code == "REVIEW_SHA_STALE"


def test_integration_plan_waits_for_update_branch_and_never_bypasses_gates():
    core = load_core()
    common = {
        "pr": 31,
        "head_sha": "a" * 40,
        "base": "dev",
        "integration_branch": "dev",
        "workspace": {"dirty": False},
        "checks": "green",
        "review": "accepted",
        "contract_valid": True,
    }
    behind = core.plan_integration({**common, "behind": True})
    assert behind["status"] == "waiting"
    assert behind["actions"] == [{"type": "update_branch", "pr": 31}]

    for field in ("required_approval", "merge_queue", "deployment_gate"):
        blocked = core.plan_integration({**common, "behind": False, field: True})
        assert blocked["status"] == "waiting"
        assert blocked["actions"] == []

    ready = core.plan_integration({**common, "behind": False})
    assert ready["actions"] == [{"type": "merge", "pr": 31}]


def test_cleanup_never_targets_self_root_or_foreign_parent():
    core = load_core()
    protected = core.plan_cleanup(
        {
            "dispatch": "dispatch-issue-7-a1",
            "merged": True,
            "actor_agent_id": "root-a",
            "identity_verified": True,
            "worker": {
                "agent_id": "root-a",
                "relationship": "root",
                "parent_id": None,
                "state": "idle",
            },
            "worktree": {
                "workspace_id": "workspace-7",
                "branch": "work/issue-7",
                "dirty": False,
                "bound_agent_ids": [],
                "stable": False,
                "shared": False,
            },
            "integration_branch": "dev",
        }
    )
    assert protected["actions"] == []
    assert "self-or-root-protected" in protected["blockers"]

    foreign = core.plan_cleanup(
        {
            "dispatch": "dispatch-issue-7-a1",
            "merged": True,
            "actor_agent_id": "root-a",
            "identity_verified": True,
            "worker": {
                "agent_id": "worker-7",
                "relationship": "subagent",
                "parent_id": "root-b",
                "state": "idle",
            },
            "worktree": {
                "workspace_id": "workspace-7",
                "branch": "work/issue-7",
                "dirty": False,
                "bound_agent_ids": ["worker-7"],
                "stable": False,
                "shared": False,
            },
            "integration_branch": "dev",
        }
    )
    assert foreign["actions"] == []
    assert foreign["manual_cleanup"][0]["agent_id"] == "worker-7"

    ambiguous = core.plan_cleanup(
        {
            "dispatch": "dispatch-issue-7-a1",
            "merged": True,
            "actor_agent_id": "root-a",
            "identity_verified": False,
            "worker": {
                "agent_id": "worker-7",
                "relationship": "subagent",
                "parent_id": "root-a",
                "state": "idle",
            },
            "worktree": {
                "workspace_id": "wrong-workspace",
                "branch": "work/issue-8",
                "dirty": False,
                "bound_agent_ids": ["worker-7"],
                "stable": False,
                "shared": False,
            },
            "integration_branch": "dev",
        }
    )
    assert ambiguous["actions"] == []
    assert ambiguous["blockers"] == ["dispatch-identity-mismatch"]

    missing_identity = core.plan_cleanup(
        {
            "dispatch": "dispatch-issue-7-a1",
            "merged": True,
            "actor_agent_id": "root-a",
            "worker": {
                "agent_id": "worker-7",
                "relationship": "subagent",
                "parent_id": "root-a",
                "state": "idle",
            },
            "worktree": {},
            "integration_branch": "dev",
        }
    )
    assert missing_identity["actions"] == []
    assert missing_identity["blockers"] == ["dispatch-identity-mismatch"]


def test_cleanup_is_two_phase_and_worktree_requires_merged_clean_unbound():
    core = load_core()
    first = core.plan_cleanup(
        {
            "dispatch": "dispatch-issue-7-a1",
            "merged": True,
            "actor_agent_id": "root-a",
            "identity_verified": True,
            "worker": {
                "agent_id": "worker-7",
                "relationship": "subagent",
                "parent_id": "root-a",
                "state": "idle",
                "archived": False,
            },
            "worktree": {
                "workspace_id": "workspace-7",
                "branch": "work/issue-7",
                "dirty": False,
                "bound_agent_ids": ["worker-7"],
                "stable": False,
                "shared": False,
            },
            "integration_branch": "dev",
        }
    )
    assert first["actions"] == [{"type": "archive_agent", "agent_id": "worker-7"}]

    second = core.plan_cleanup(
        {
            "dispatch": "dispatch-issue-7-a1",
            "merged": True,
            "actor_agent_id": "root-a",
            "identity_verified": True,
            "worker": {
                "agent_id": "worker-7",
                "relationship": "subagent",
                "parent_id": "root-a",
                "state": "archived",
                "archived": True,
            },
            "worktree": {
                "workspace_id": "workspace-7",
                "branch": "work/issue-7",
                "dirty": False,
                "bound_agent_ids": [],
                "stable": False,
                "shared": False,
            },
            "integration_branch": "dev",
        }
    )
    assert second["actions"] == [
        {"type": "archive_worktree", "workspace_id": "workspace-7"},
        {"type": "delete_branch", "branch": "work/issue-7"},
    ]


def test_retire_requires_terminal_dispatch_and_preserves_unmerged_remote_branch():
    core = load_core()
    with __import__("pytest").raises(core.PolicyError) as live:
        core.plan_retirement(
            {"status": "running", "merged": False, "remote_branch": True}
        )
    assert live.value.code == "RETIRE_NOT_TERMINAL"
    result = core.plan_retirement(
        {
            "status": "abandoned",
            "merged": False,
            "remote_branch": True,
            "worktree_dirty": False,
        }
    )
    assert "delete_remote_branch" not in [item["type"] for item in result["actions"]]

    wave = core.plan_reconcile(
        {
            "worker_slots": 1,
            "issues": [
                {
                    "number": 7,
                    "state": "active",
                    "hotset": ["src/a"],
                    "dispatch": {
                        "id": "dispatch-issue-7-a1",
                        "status": "retired",
                        "parked": True,
                    },
                },
                {
                    "number": 8,
                    "state": "ready",
                    "priority": "P1",
                    "contract_valid": True,
                    "dependencies": [],
                    "hotset": ["src/a"],
                },
            ],
        }
    )
    assert wave["summary"]["wip"] == 0
    assert wave["summary"]["selected"] == [8]
    assert core.plan_issue_state_repairs(
        [
            {
                "number": 7,
                "state": "active",
                "dispatch": {"status": "retired", "parked": True},
            }
        ]
    ) == [{"issue": 7, "state": "blocked"}]


def _hold_mutex(lock_path: str, ready_path: str) -> None:
    core = load_core()
    with core.coordination_mutex(Path(lock_path), timeout_seconds=1):
        Path(ready_path).write_text("ready", encoding="utf-8")
        time.sleep(0.8)


def _crash_with_mutex(lock_path: str, ready_path: str) -> None:
    core = load_core()
    with core.coordination_mutex(Path(lock_path), timeout_seconds=1):
        Path(ready_path).write_text("ready", encoding="utf-8")
        __import__("os")._exit(0)


def _claim_once(lock_path: str, state_path: str, claims_path: str) -> None:
    core = load_core()
    with core.coordination_mutex(Path(lock_path), timeout_seconds=2):
        state = Path(state_path)
        if state.read_text(encoding="utf-8") == "ready":
            state.write_text("active", encoding="utf-8")
            with Path(claims_path).open("a", encoding="utf-8") as handle:
                handle.write("claim\n")


def test_coordination_mutex_is_process_scoped_and_released(tmp_path):
    core = load_core()
    lock = tmp_path / "orchestrator.lock"
    ready = tmp_path / "ready"
    process = multiprocessing.Process(target=_hold_mutex, args=(str(lock), str(ready)))
    process.start()
    deadline = time.monotonic() + 2
    while not ready.exists() and time.monotonic() < deadline:
        time.sleep(0.02)
    assert ready.exists()
    with __import__("pytest").raises(core.PolicyError) as busy:
        with core.coordination_mutex(lock, timeout_seconds=0.05):
            pass
    assert busy.value.code == "coordination-busy"
    process.join(3)
    assert process.exitcode == 0
    with core.coordination_mutex(lock, timeout_seconds=0.2):
        pass


def test_coordination_mutex_releases_after_process_crash(tmp_path):
    core = load_core()
    lock = tmp_path / "orchestrator.lock"
    ready = tmp_path / "crash-ready"
    process = multiprocessing.Process(
        target=_crash_with_mutex, args=(str(lock), str(ready))
    )
    process.start()
    process.join(3)
    assert process.exitcode == 0
    assert ready.exists()
    with core.coordination_mutex(lock, timeout_seconds=0.2):
        pass


def test_two_concurrent_coordinators_claim_once(tmp_path):
    lock = tmp_path / "orchestrator.lock"
    state = tmp_path / "github-state"
    claims = tmp_path / "claims"
    state.write_text("ready", encoding="utf-8")
    workers = [
        multiprocessing.Process(
            target=_claim_once, args=(str(lock), str(state), str(claims))
        )
        for _ in range(2)
    ]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(4)
        assert worker.exitcode == 0
    assert state.read_text(encoding="utf-8") == "active"
    assert claims.read_text(encoding="utf-8").splitlines() == ["claim"]


def test_cli_read_only_reconcile_has_stable_envelope(tmp_path):
    snapshot = tmp_path / "snapshot.json"
    snapshot.write_text(
        json.dumps(
            {
                "repository": "owner/repo",
                "worker_slots": 3,
                "issues": [],
                "closed_issues": [],
            }
        ),
        encoding="utf-8",
    )
    script = ROOT / "skills" / "orchestrator" / "scripts" / "orch.py"
    result = subprocess.run(
        [
            sys.executable,
            str(script),
            "reconcile",
            "--repo",
            "owner/repo",
            "--read-only",
            "--snapshot",
            str(snapshot),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload == {
        "schema_version": 1,
        "status": "idle",
        "actions": [],
        "warnings": [],
        "summary": {
            "worker_slots": 3,
            "wip": 0,
            "free_slots": 3,
            "selected": [],
            "deferred": {},
        },
    }


def _contract(
    core, *, hotset=None, dependencies=None, difficulty="standard", risk="standard"
):
    value = {
        "design": ["Change the API without changing unrelated behavior."],
        "acceptance": ["The regression is covered."],
        "hotset": hotset if hotset is not None else ["src/api"],
        "done_when": ["python -m pytest tests/api -q"],
        "dependencies": dependencies if dependencies is not None else [],
        "priority": "P1",
        "difficulty": difficulty,
        "risk": risk,
        "unresolved_decisions": [],
    }
    value["sha256"] = core.contract_hash(value)
    return value


def test_contract_validation_is_hash_bound_and_risk_proportional():
    core = load_core()
    contract = _contract(core)
    assert core.validate_contract(contract) == contract
    with __import__("pytest").raises(core.PolicyError) as changed:
        core.validate_contract({**contract, "priority": "P0"})
    assert changed.value.code == "CONTRACT_HASH_MISMATCH"
    with __import__("pytest").raises(core.PolicyError) as unresolved:
        core.validate_contract(
            {**_contract(core), "unresolved_decisions": ["Which API?"]}
        )
    assert unresolved.value.code == "CONTRACT_DECISION_OPEN"


def test_github_snapshot_derives_review_state_from_pr_and_three_labels_only():
    core = load_core()
    contract = _contract(core)
    record = {
        "contract": contract,
        "dispatch": {
            "id": "dispatch-issue-15-a1",
            "attempt": 1,
            "generation": 2,
            "creator_agent_id": "root-a",
            "worker_agent_id": "worker-15",
            "workspace_id": "wt-15",
            "branch": "work/issue-15",
            "base_sha": "a" * 40,
            "status": "running",
        },
    }
    issue = {
        "number": 15,
        "title": "API fix",
        "body": "untrusted reporter text",
        "labels": [{"name": "orch:active"}, {"name": "bug"}],
        "milestone": {"title": "M1", "dueOn": "2026-08-01T00:00:00Z"},
        "assignees": [],
        "comments": [
            {"id": 900, "body": core.render_issue_record(record), "author": "owner"}
        ],
    }
    delivery = {
        "contract_sha256": contract["sha256"],
        "candidate_sha": "b" * 40,
        "changed_paths": ["src/api/client.py"],
        "tdd": {"red": "failed first", "green": "passed", "refactor": "clean"},
        "verification": ["python -m pytest tests/api -q"],
        "deviations": [],
        "risks": [],
    }
    pr = {
        "number": 31,
        "body": core.render_delivery(delivery),
        "headRefName": "work/issue-15",
        "headRefOid": "b" * 40,
        "baseRefName": "dev",
        "isDraft": False,
        "mergeStateStatus": "CLEAN",
        "reviewDecision": "APPROVED",
        "statusCheckRollup": [{"conclusion": "SUCCESS", "status": "COMPLETED"}],
        "reviews": [
            {
                "state": "APPROVED",
                "submittedAt": "2026-07-19T11:00:00Z",
                "commit": {"oid": "b" * 40},
                "body": core.render_review(
                    {
                        "candidate_sha": "b" * 40,
                        "contract_sha256": contract["sha256"],
                        "axis": "combined",
                        "strength": "standard",
                        "verdict": "pass",
                        "findings": [],
                    }
                ),
            }
        ],
        "url": "https://github.test/owner/repo/pull/31",
    }

    snapshot = core.normalize_github_snapshot("owner/repo", [issue], [pr])

    normalized = snapshot["issues"][0]
    assert normalized["state"] == "ready-to-merge"
    assert normalized["contract_valid"] is True
    assert normalized["pr"]["number"] == 31
    assert "untrusted reporter text" not in json.dumps(normalized["contract"])
    assert snapshot["closed_issues"] == []

    stale = {**pr, "reviews": [{**pr["reviews"][0], "commit": {"oid": "a" * 40}}]}
    stale_snapshot = core.normalize_github_snapshot("owner/repo", [issue], [stale])
    assert stale_snapshot["issues"][0]["state"] == "review"

    requested_changes = {
        **pr,
        "reviewDecision": "CHANGES_REQUESTED",
        "reviews": [
            *pr["reviews"],
            {
                "state": "CHANGES_REQUESTED",
                "submittedAt": "2026-07-19T12:00:00Z",
                "commit": {"oid": "b" * 40},
                "body": "Please address the regression.",
                "author": {"login": "maintainer"},
            },
        ],
    }
    blocked_snapshot = core.normalize_github_snapshot(
        "owner/repo", [issue], [requested_changes]
    )
    assert blocked_snapshot["issues"][0]["state"] == "review"


def test_duplicate_managed_record_or_multiple_core_labels_fail_closed():
    core = load_core()
    record = {"contract": _contract(core), "dispatch": None}
    comment = {"id": 1, "body": core.render_issue_record(record)}
    issue = {
        "number": 1,
        "title": "x",
        "labels": [{"name": "orch:ready"}, {"name": "orch:active"}],
        "comments": [comment, {**comment, "id": 2}],
    }
    with __import__("pytest").raises(core.PolicyError) as duplicate:
        core.normalize_github_snapshot("owner/repo", [issue], [])
    assert duplicate.value.code == "ISSUE_RECORD_DUPLICATE"

    issue["comments"] = [comment]
    with __import__("pytest").raises(core.PolicyError) as labels:
        core.normalize_github_snapshot("owner/repo", [issue], [])
    assert labels.value.code == "ISSUE_LABEL_STATE_CONFLICT"


def test_delivery_is_commit_contract_and_hotset_bound():
    core = load_core()
    contract = _contract(core)
    delivery = {
        "contract_sha256": contract["sha256"],
        "candidate_sha": "b" * 40,
        "changed_paths": ["src/api/client.py"],
        "tdd": {"red": "failed", "green": "passed", "refactor": "clean"},
        "verification": ["pytest"],
        "deviations": [],
        "risks": [],
    }
    body = core.render_delivery(delivery)
    assert core.parse_delivery(body) == delivery
    core.validate_delivery(delivery, contract, "b" * 40)
    with __import__("pytest").raises(core.PolicyError) as scope:
        core.validate_delivery(
            {**delivery, "changed_paths": ["src/other.py"]}, contract, "b" * 40
        )
    assert scope.value.code == "DELIVERY_HOTSET_VIOLATION"
    with __import__("pytest").raises(core.PolicyError) as stale:
        core.validate_delivery(delivery, contract, "c" * 40)
    assert stale.value.code == "DELIVERY_SHA_STALE"
    with __import__("pytest").raises(core.PolicyError) as readback:
        core.validate_delivery(
            delivery, contract, "b" * 40, actual_paths=["src/api/other.py"]
        )
    assert readback.value.code == "DELIVERY_PATH_READBACK_MISMATCH"

    malformed = [
        {key: value for key, value in delivery.items() if key != "risks"},
        {**delivery, "extra": "not allowed"},
        {**delivery, "tdd": {"red": "failed", "green": "passed"}},
        {**delivery, "deviations": [7]},
    ]
    for candidate in malformed:
        with pytest.raises(core.PolicyError):
            core.validate_delivery(candidate, contract, "b" * 40)


def test_unknown_hotset_is_repository_exclusive_but_delivery_can_complete():
    core = load_core()
    contract = _contract(core, hotset=[])
    delivery = {
        "contract_sha256": contract["sha256"],
        "candidate_sha": "b" * 40,
        "changed_paths": ["src/anywhere.py", "tests/test_anywhere.py"],
        "tdd": {"red": "failed", "green": "passed", "refactor": "clean"},
        "verification": ["pytest"],
        "deviations": [],
        "risks": [],
    }
    scheduled = core.plan_reconcile(
        {
            "repository": "owner/repo",
            "worker_slots": 3,
            "issues": [
                {
                    "number": 7,
                    "state": "ready",
                    "priority": "P1",
                    "hotset": [],
                    "dependencies": [],
                    "contract_valid": True,
                }
            ],
        }
    )
    assert scheduled["summary"]["selected"] == [7]
    core.validate_delivery(
        delivery,
        contract,
        "b" * 40,
        actual_paths=["src/anywhere.py", "tests/test_anywhere.py"],
    )
    with __import__("pytest").raises(core.PolicyError) as tdd:
        core.validate_delivery(
            {**delivery, "tdd": {"green": "passed"}}, contract, "b" * 40
        )
    assert tdd.value.code == "DELIVERY_TDD_MISSING"


def test_wake_is_once_per_candidate_and_has_no_ack_protocol():
    core = load_core()
    snapshot = {
        "issue": 15,
        "pr": 31,
        "candidate_sha": "a" * 40,
        "creator_agent_id": "root-a",
        "wake_sent_for_sha": None,
    }
    first = core.plan_completion_wake(snapshot)
    assert first["actions"] == [
        {
            "action_id": "wake-issue-15-aaaaaaaaaaaa",
            "type": "send_prompt",
            "agent_id": "root-a",
            "message": "Issue #15 delivered PR #31",
        }
    ]
    assert "ack" not in json.dumps(first).lower()
    assert (
        core.plan_completion_wake({**snapshot, "wake_sent_for_sha": "a" * 40})[
            "actions"
        ]
        == []
    )


def test_reviewers_do_not_consume_worker_slots_and_are_one_shot():
    core = load_core()
    result = core.plan_review_actions(
        {
            "issues": [
                {
                    "number": 7,
                    "state": "review",
                    "contract": {"risk": "standard"},
                    "pr": {"number": 17, "head_sha": "a" * 40},
                    "reviews": [],
                }
            ]
        }
    )
    assert result["actions"] == [
        {
            "action_id": "create-reviewer-pr-17-aaaaaaaaaaaa-combined",
            "type": "create_reviewer",
            "issue": 7,
            "pr": 17,
            "axis": "combined",
            "strength": "standard",
            "candidate_sha": "a" * 40,
        }
    ]
    assert result["summary"]["worker_slots_consumed"] == 0


def test_workspace_selection_precedence_and_nonstable_entry():
    core = load_core()
    current = {
        "id": "current",
        "repository": "owner/repo",
        "branch": "dev",
        "relationship": "root",
        "dirty": False,
        "pr_head": False,
        "ephemeral": False,
        "worker": False,
    }
    other = {**current, "id": "configured"}
    repo = {
        "repository": "owner/repo",
        "integration_branch": "dev",
        "workspace_id": "configured",
    }
    assert core.select_workspace(current, [other], repo)["id"] == "current"
    assert core.select_workspace(None, [other], repo)["id"] == "configured"
    with __import__("pytest").raises(core.PolicyError) as ambiguous:
        core.select_workspace(
            None,
            [{**other, "id": "a"}, {**other, "id": "b"}],
            {**repo, "workspace_id": None},
        )
    assert ambiguous.value.code == "WORKSPACE_SELECTION_REQUIRED"

    inherited = {
        "provider": "codex",
        "settings": {
            "model": "gpt-5.6",
            "thinkingOptionId": "high",
            "modeId": "full-access",
            "features": {},
        },
    }
    entry = core.plan_nonstable_entry(
        {
            "repository": "owner/repo",
            "request": "run orchestration",
            "target_workspace_id": "configured",
            "active_root_agents": [],
            "caller_runtime": inherited,
        }
    )
    assert entry["actions"] == [
        {
            "type": "create_root_agent",
            "relationship": "detached",
            "workspace_id": "configured",
            "runtime": inherited,
            "prompt": "run orchestration",
        }
    ]


def _coordinator_context(*, current=True, collaboration_mode="default"):
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
    return {
        "schema_version": 1,
        "actor": {
            "id": "root-a",
            "cwd": "C:/repo",
            "workspace_id": "stable-dev" if current else "feature-17",
            "provider": "codex",
            "settings": {"model": "gpt-5.6", "modeId": "full-access"},
        },
        "current_workspace": workspace
        if current
        else {
            **workspace,
            "id": "feature-17",
            "branch": "work/issue-17",
            "worker": True,
        },
        "candidate_workspaces": [workspace],
        "mode": {
            "collaboration_mode": collaboration_mode,
            "write_capable": True,
            "colorTier": "dangerous",
        },
        "features": {"plan_mode": False},
        "remote_branches": ["dev", "main"],
        "active_root_agents": [],
        "request": "continue the managed wave",
    }


@pytest.mark.parametrize(
    ("mutation", "code"),
    [
        ({"mode": {"collaboration_mode": "Plan"}}, "COORDINATOR_MODE_READ_ONLY"),
        ({"features": {"plan_mode": True}}, "COORDINATOR_MODE_READ_ONLY"),
        ({"mode": {"colorTier": "Planning"}}, "COORDINATOR_MODE_READ_ONLY"),
        ({"mode": {"write_capable": None}}, "COORDINATOR_CAPABILITY_UNKNOWN"),
    ],
)
def test_coordinator_entry_fails_closed_when_mode_cannot_write(mutation, code):
    core = load_core()
    context = _coordinator_context()
    for key, value in mutation.items():
        context[key] = {**context.get(key, {}), **value}
    with pytest.raises(core.PolicyError) as rejected:
        core.plan_coordinator_entry(
            context,
            {"repository": "owner/repo", "integration_branch": "dev"},
            expected_actor_id="root-a",
            expected_cwd="C:/repo",
        )
    assert rejected.value.code == code


def test_coordinator_entry_resolves_dev_only_from_unambiguous_live_readback():
    core = load_core()
    context = _coordinator_context()
    ready = core.plan_coordinator_entry(
        context,
        {"repository": "owner/repo"},
        expected_actor_id="root-a",
        expected_cwd="C:/repo",
    )
    assert ready["status"] == "ready"
    assert ready["repository_config"]["integration_branch"] == "dev"

    for broken in (
        {**context, "remote_branches": ["main"]},
        {
            **context,
            "candidate_workspaces": [
                *context["candidate_workspaces"],
                {**context["candidate_workspaces"][0], "id": "another-dev"},
            ],
        },
    ):
        with pytest.raises(core.PolicyError) as rejected:
            core.plan_coordinator_entry(
                broken,
                {"repository": "owner/repo"},
                expected_actor_id="root-a",
                expected_cwd="C:/repo",
            )
        assert rejected.value.code == "INTEGRATION_BRANCH_REQUIRED"


def test_nonstable_coordinator_entry_routes_request_without_persisting_it():
    core = load_core()
    context = _coordinator_context(current=False)
    routed = core.plan_coordinator_entry(
        context,
        {
            "repository": "owner/repo",
            "integration_branch": "dev",
            "workspace_id": "stable-dev",
        },
        expected_actor_id="root-a",
        expected_cwd="C:/repo",
    )
    assert routed["status"] == "forwarded"
    assert routed["actions"] == [
        {
            "type": "create_root_agent",
            "relationship": "detached",
            "workspace_id": "stable-dev",
            "runtime": {
                "provider": "codex",
                "settings": {"model": "gpt-5.6", "modeId": "full-access"},
            },
            "prompt": "continue the managed wave",
        }
    ]
    assert "request" not in routed
    assert "prompt" not in routed["repository_config"]


def test_config_resolution_order_and_v5_migration_preserve_thinking():
    core = load_core()
    old = {
        "tiers": {
            "light": {"provider": "opencode", "model": "kimi/k3"},
            "standard": {
                "provider": "codex",
                "model": "gpt-5.6-sol",
                "thinking": "low",
            },
            "heavy": {
                "provider": "codex",
                "model": "gpt-5.6-sol",
                "thinking": "high",
            },
        },
        "roles": {"coordinator": "heavy", "reviewer": "standard"},
    }
    migrated = core.migrate_v5_config(old)
    assert migrated["schema_version"] == 1
    assert "roles" not in migrated
    assert migrated["tiers"]["heavy"]["settings"]["thinkingOptionId"] == "high"
    assert "modeId" not in migrated["tiers"]["heavy"]["settings"]
    assert migrated["global"]["worker_slots"] == 3
    assert migrated["global"]["max_attempts"] == 2

    migrated["repositories"] = {
        "owner/repo": {
            "default_tier": "light",
            "milestone_tiers": {"M1": "heavy"},
            "tiers": {
                "heavy": {
                    "provider": "codex",
                    "settings": {
                        "model": "repo-heavy",
                        "thinkingOptionId": "high",
                        "modeId": "full-access",
                        "features": {},
                    },
                }
            },
        }
    }
    capabilities = {
        "provider": "codex",
        "models": {"repo-heavy": {"thinking": ["high"]}},
        "modes": ["full-access"],
        "features": [],
    }
    binding = core.resolve_runtime(
        migrated,
        repository="owner/repo",
        issue={"milestone": "M1"},
        coordinator_runtime={
            "provider": "codex",
            "settings": {"model": "current", "modeId": "full-access"},
        },
        capabilities=capabilities,
    )
    assert binding["tier"] == "heavy"
    assert binding["settings"]["model"] == "repo-heavy"


def test_config_file_migration_is_atomic_and_keeps_v5_backup(tmp_path):
    core = load_core()
    old_path = tmp_path / "providers.json"
    new_path = tmp_path / "config.json"
    old_path.write_text(
        json.dumps(
            {
                "tiers": {
                    tier: {"provider": "p", "model": f"m-{tier}"}
                    for tier in ("light", "standard", "heavy")
                }
            }
        ),
        encoding="utf-8",
    )
    result = core.migrate_config_file(old_path, new_path)
    assert result == json.loads(new_path.read_text(encoding="utf-8"))
    assert (tmp_path / "providers.v5.backup.json").read_bytes() == old_path.read_bytes()
    assert not (tmp_path / "config.json.tmp").exists()


def test_read_only_config_load_does_not_write_migration(tmp_path):
    core = load_core()
    old = tmp_path / "providers.json"
    new = tmp_path / "config.json"
    old.write_text(
        json.dumps(
            {
                "tiers": {
                    tier: {"provider": "p", "model": f"m-{tier}"}
                    for tier in ("light", "standard", "heavy")
                }
            }
        ),
        encoding="utf-8",
    )
    loaded = core.load_or_migrate_config(new, old, write_migration=False)
    assert loaded["schema_version"] == 1
    assert not new.exists()
    assert not (tmp_path / "providers.v5.backup.json").exists()


def test_optional_project_failure_is_degraded_not_core_blocker():
    core = load_core()
    result = core.project_result(permission=False, drift=["Status"])
    assert result["status"] == "waiting"
    assert result["warnings"][0]["code"] == "project-sync-degraded"
    assert result["summary"]["core_blocked"] is False


def test_native_review_record_is_commit_bound_and_satisfies_graded_policy():
    core = load_core()
    record = {
        "candidate_sha": "a" * 40,
        "contract_sha256": "b" * 64,
        "axis": "combined",
        "strength": "standard",
        "verdict": "pass",
        "findings": [],
    }
    body = core.render_review(record)
    assert core.parse_review(body) == record
    with pytest.raises(core.PolicyError) as extra:
        core.parse_review(core.render_review({**record, "unexpected": True}))
    assert extra.value.code == "REVIEW_SCHEMA_INVALID"
    with pytest.raises(core.PolicyError) as findings:
        core.parse_review(core.render_review({**record, "findings": [7]}))
    assert findings.value.code == "REVIEW_FINDINGS_INVALID"
    assert core.review_complete(
        risk="standard",
        candidate_sha="a" * 40,
        reviews=[record],
        dual=False,
        human_approved=False,
    )
    assert not core.review_complete(
        risk="standard",
        candidate_sha="a" * 40,
        reviews=[record, {**record, "verdict": "fail", "findings": ["regression"]}],
        dual=False,
        human_approved=False,
    )
    assert not core.review_complete(
        risk="standard",
        candidate_sha="c" * 40,
        reviews=[record],
        dual=False,
        human_approved=False,
    )
    dual = [
        {**record, "axis": axis, "candidate_sha": "d" * 40}
        for axis in ("spec", "quality")
    ]
    assert core.review_complete(
        risk="standard",
        candidate_sha="d" * 40,
        reviews=dual,
        dual=True,
        human_approved=False,
    )
    assert not core.review_complete(
        risk="standard",
        candidate_sha="d" * 40,
        reviews=[],
        dual=True,
        human_approved=True,
    )
    assert not core.review_complete(
        risk="strict",
        candidate_sha="a" * 40,
        reviews=[],
        dual=False,
        human_approved=True,
    )


def test_strict_without_checks_requires_human_or_contract_substitute():
    core = load_core()
    assert not core.integration_evidence_complete(
        risk="strict",
        checks="none",
        review_complete=True,
        human_approved=False,
        substitute_evidence_defined=False,
    )
    assert core.integration_evidence_complete(
        risk="strict",
        checks="none",
        review_complete=True,
        human_approved=True,
        substitute_evidence_defined=False,
    )
    assert core.integration_evidence_complete(
        risk="strict",
        checks="none",
        review_complete=True,
        human_approved=False,
        substitute_evidence_defined=True,
    )


def test_integration_order_is_dependency_priority_acceptance_then_issue():
    core = load_core()
    issues = [
        {
            "number": 20,
            "state": "ready-to-merge",
            "priority": "P0",
            "dependencies": [10],
            "dispatch": {"accepted_at": "2026-07-19T10:00:00Z"},
        },
        {
            "number": 10,
            "state": "ready-to-merge",
            "priority": "P1",
            "dependencies": [],
            "dispatch": {"accepted_at": "2026-07-19T11:00:00Z"},
        },
        {
            "number": 30,
            "state": "ready-to-merge",
            "priority": "P1",
            "dependencies": [],
            "dispatch": {"accepted_at": "2026-07-19T09:00:00Z"},
        },
    ]
    assert [item["number"] for item in core.integration_order(issues)] == [30, 10, 20]
    assert (
        core.integration_order(
            [
                issues[0],
                {"number": 10, "state": "active", "dependencies": []},
            ],
            closed_issues=[],
        )
        == []
    )


def test_materialized_worker_action_is_atomic_direct_and_short():
    core = load_core()
    contract = _contract(core)
    action = {
        "action_id": "create-worker-dispatch-issue-15-a1",
        "type": "create_worker",
        "dispatch_id": "dispatch-issue-15-a1",
        "issue": 15,
        "attempt": 1,
        "branch": "work/issue-15",
        "wave_generation": 3,
    }
    coordinator = {
        "agent_id": "root-a",
        "provider": "codex",
        "settings": {
            "model": "gpt-current",
            "thinkingOptionId": "high",
            "modeId": "full-access",
            "features": {},
        },
    }
    result = core.materialize_worker_action(
        action,
        {"number": 15, "contract": contract, "milestone": None},
        repository="owner/repo",
        base_sha="a" * 40,
        config=core.default_config(),
        coordinator_runtime=coordinator,
    )
    assert result["relationship"] == "subagent"
    assert result["name"] == "Worker - #15 - a1"
    assert result["notify_on_finish"] is True
    assert result["workspace"] == {
        "kind": "create-worktree",
        "workspace_id": None,
        "branch": "work/issue-15",
        "base_sha": "a" * 40,
    }
    assert result["runtime_request"]["settings"]["thinkingOptionId"] == "high"
    assert result["labels"]["orch.creator"] == "root-a"
    assert len(result["initial_prompt"].splitlines()) <= 60
    assert "orchestrator:delivery:v1" in result["initial_prompt"]
    assert '"contract_sha256": "<64-hex exactly above>"' in result["initial_prompt"]
    assert '"candidate_sha": "<40-hex current PR head>"' in result["initial_prompt"]
    assert (
        '"tdd": {"red": "...", "green": "...", "refactor": "..."}'
        in result["initial_prompt"]
    )
    assert '"verification": ["command: result"]' in result["initial_prompt"]
    assert "Do not rename keys or nest this record" in result["initial_prompt"]
    assert "one best-effort wake" in result["initial_prompt"]
    assert "runtime auto-renamed" in result["initial_prompt"]
    assert "create Agent" in result["initial_prompt"]


def test_materialized_reviewer_is_one_shot_in_candidate_workspace_and_short():
    core = load_core()
    contract = _contract(core, risk="strict")
    action = {
        "action_id": "create-reviewer-pr-31-aaaaaaaaaaaa-combined",
        "type": "create_reviewer",
        "issue": 15,
        "pr": 31,
        "axis": "combined",
        "strength": "heavy",
        "candidate_sha": "a" * 40,
    }
    coordinator = {
        "agent_id": "root-a",
        "provider": "codex",
        "settings": {
            "model": "gpt-current",
            "thinkingOptionId": "high",
            "modeId": "full-access",
            "features": {},
        },
    }
    result = core.materialize_reviewer_action(
        action,
        {
            "number": 15,
            "contract": contract,
            "milestone": None,
            "dispatch": {"workspace_id": "worker-wt"},
        },
        repository="owner/repo",
        config=core.default_config(),
        coordinator_runtime=coordinator,
    )
    assert result["relationship"] == "subagent"
    assert result["name"] == "Reviewer - PR #31 - combined"
    assert result["workspace"] == {
        "kind": "existing",
        "workspace_id": "worker-wt",
    }
    assert result["labels"]["orch.role"] == "reviewer"
    assert len(result["initial_prompt"].splitlines()) <= 40
    assert "orchestrator:review:v1" in result["initial_prompt"]
    assert f'"candidate_sha": "{"a" * 40}"' in result["initial_prompt"]
    assert f'"contract_sha256": "{contract["sha256"]}"' in result["initial_prompt"]
    assert '"axis": "combined"' in result["initial_prompt"]
    assert '"strength": "heavy"' in result["initial_prompt"]
    assert '"verdict": "pass|fail"' in result["initial_prompt"]
    assert "Do not rename keys or nest this record" in result["initial_prompt"]
    assert (
        "Verify this attached Workspace HEAD equals Candidate SHA"
        in result["initial_prompt"]
    )
    assert "do not modify" in result["initial_prompt"].lower()


def test_reviewer_requires_the_read_back_candidate_workspace():
    core = load_core()
    contract = _contract(core, risk="standard")
    with pytest.raises(core.PolicyError) as rejected:
        core.materialize_reviewer_action(
            {
                "action_id": "create-reviewer-pr-31-aaaaaaaaaaaa-combined",
                "type": "create_reviewer",
                "issue": 15,
                "pr": 31,
                "axis": "combined",
                "strength": "standard",
                "candidate_sha": "a" * 40,
            },
            {"number": 15, "contract": contract, "dispatch": {}},
            repository="owner/repo",
            config=core.default_config(),
            coordinator_runtime={
                "agent_id": "root-a",
                "provider": "codex",
                "settings": {"model": "gpt-current", "modeId": "full-access"},
            },
        )
    assert rejected.value.code == "REVIEW_WORKSPACE_ID_MISSING"


def test_project_projection_contains_only_four_derived_fields():
    core = load_core()
    assert core.project_projection(
        {
            "number": 7,
            "state": "ready-to-merge",
            "priority": "P0",
            "risk": "strict",
            "dispatch": {"generation": 4},
        }
    ) == {
        "Status": "Ready to merge",
        "Priority": "P0",
        "Wave": "4",
        "Risk": "strict",
    }
