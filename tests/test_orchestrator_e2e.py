from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CORE = ROOT / "skills" / "orchestrator" / "scripts" / "orch_core.py"


def _core():
    spec = importlib.util.spec_from_file_location("orch_core_e2e", CORE)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


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
