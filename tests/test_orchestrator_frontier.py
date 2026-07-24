from __future__ import annotations

from pathlib import Path

import pytest

from conftest import load_core, load_frontier


ROOT = Path(__file__).resolve().parents[1]


def _v2_contract(core):
    contract = {
        "design": ["Change the API and keep compatibility."],
        "acceptance": ["The regression is covered."],
        "change_claims": {
            "paths": ["src/api"],
            "resources": ["schema:settings"],
        },
        "done_when": ["python -m pytest tests/api -q"],
        "dependencies": {"dispatch_after": [3], "merge_after": [4]},
        "priority": "P1",
        "difficulty": "standard",
        "risk": "standard",
        "unresolved_decisions": [],
    }
    contract["sha256"] = core.contract_hash(contract)
    return contract


def test_conflict_claims_are_scoped_to_the_owning_change_surface():
    frontier = load_frontier()

    assert frontier.claims_overlap(
        {"paths": ["frontend/package.json"], "resources": []},
        {"paths": ["frontend/package-lock.json"], "resources": []},
    )
    assert not frontier.claims_overlap(
        {"paths": ["frontend/package.json"], "resources": []},
        {"paths": ["tools/pyproject.toml"], "resources": []},
    )
    assert frontier.claims_overlap(
        {"paths": ["src/settings"], "resources": ["schema:settings"]},
        {"paths": ["docs/settings"], "resources": ["schema:settings"]},
    )
    assert not frontier.claims_overlap(
        {"paths": ["frontend/proto/client.proto"], "resources": []},
        {"paths": ["tools/src/generated/client.py"], "resources": []},
    )


def test_width_aware_selection_fills_slots_instead_of_taking_a_broad_issue_first():
    frontier = load_frontier()
    snapshot = {
        "execution_slots": 2,
        "integration_wip_limit": 4,
        "closed_issues": [],
        "issues": [
            {
                "number": 1,
                "state": "ready",
                "priority": "P1",
                "change_claims": {"paths": ["src"], "resources": []},
                "dispatch_after": [],
                "contract_valid": True,
            },
            {
                "number": 2,
                "state": "ready",
                "priority": "P1",
                "change_claims": {"paths": ["src/api"], "resources": []},
                "dispatch_after": [],
                "contract_valid": True,
            },
            {
                "number": 3,
                "state": "ready",
                "priority": "P1",
                "change_claims": {"paths": ["src/ui"], "resources": []},
                "dispatch_after": [],
                "contract_valid": True,
            },
        ],
    }

    plan = frontier.select_wave(snapshot)

    assert plan["selected"] == [2, 3]
    assert plan["parallel_width"] == 2
    assert plan["free_execution_slots"] == 2
    assert plan["free_integration_wip"] == 4
    assert plan["deferred"] == {"1": "width-optimized"}


def test_frontier_analysis_prefilters_candidates_and_reports_reserve_starvation():
    frontier = load_frontier()
    snapshot = {
        "execution_slots": 3,
        "integration_wip_limit": 6,
        "closed_issues": [],
        "issues": [
            {
                "number": 6,
                "state": "active",
                "priority": "P1",
                "change_claims": {"paths": ["lib/import"], "resources": []},
                "dispatch": {"status": "running"},
            },
            {
                "number": 22,
                "state": "ready",
                "priority": "P2",
                "change_claims": {"paths": ["docs/saf.md"], "resources": []},
                "dispatch_after": [],
                "contract_valid": True,
            },
        ],
    }
    candidates = [
        {"number": 23, "labels": ["ready-for-agent", "P3"]},
        {"number": 26, "labels": ["ready-for-human", "P2"]},
        {"number": 27, "labels": ["needs-info", "P1"]},
        {"number": 28, "labels": ["bug"]},
    ]
    policy = {
        "include_labels": ["ready-for-agent"],
        "human_labels": ["ready-for-human"],
        "clarify_labels": ["needs-info"],
        "reserve_target": 6,
    }

    plan = frontier.analyze_frontier(candidates, snapshot, policy)

    assert plan["candidate_assessments"] == [
        {"issue": 23, "disposition": "design", "reason": "candidate-label-match"},
        {"issue": 26, "disposition": "human", "reason": "human-label"},
        {"issue": 27, "disposition": "clarify", "reason": "clarify-label"},
        {"issue": 28, "disposition": "defer", "reason": "candidate-label-missing"},
    ]
    assert plan["ready_reserve"] == 1
    assert plan["reserve_target"] == 6
    assert plan["reserve_gap"] == 5
    assert plan["parallel_width_now"] == 1
    assert plan["frontier_starved"] is True


def test_contract_v2_has_scoped_claims_and_typed_dependencies_with_v1_fallback():
    core = load_core()
    v2 = _v2_contract(core)

    assert core.validate_contract(v2) == v2
    assert core.contract_change_claims(v2) == {
        "paths": ["src/api"],
        "resources": ["schema:settings"],
    }
    assert core.contract_dispatch_after(v2) == [3]
    assert core.contract_merge_after(v2) == [4]

    v1 = {
        "design": ["Keep the existing change."],
        "acceptance": ["The old contract stays runnable."],
        "hotset": ["src/legacy"],
        "done_when": ["python -m pytest -q"],
        "dependencies": [2],
        "priority": "P2",
        "difficulty": "light",
        "risk": "low",
        "unresolved_decisions": [],
    }
    v1["sha256"] = core.contract_hash(v1)
    assert core.validate_contract(v1) == v1
    assert core.contract_change_claims(v1) == {
        "paths": ["src/legacy"],
        "resources": [],
    }
    assert core.contract_dispatch_after(v1) == [2]
    assert core.contract_merge_after(v1) == [2]


def test_issue_record_v2_round_trip_rejects_mixed_or_duplicate_markers():
    core = load_core()
    record = {"contract": _v2_contract(core), "dispatch": None}

    rendered = core.render_issue_record(record)

    assert core.ISSUE_MARKER_V2 in rendered
    assert core.ISSUE_MARKER_V1 not in rendered
    assert core.parse_issue_record(rendered) == record
    with pytest.raises(core.PolicyError) as mixed:
        core.parse_issue_record(
            f"{rendered}\n{core.ISSUE_MARKER_V1}\n```json\n{{}}\n```"
        )
    assert mixed.value.code == "ISSUE_RECORD_MARKER_INVALID"


def test_github_snapshot_projects_v2_contract_into_scheduler_fields():
    core = load_core()
    contract = _v2_contract(core)
    record = core.render_issue_record({"contract": contract, "dispatch": None})
    issue = {
        "number": 7,
        "title": "Scoped API change",
        "labels": [{"name": "orch:ready"}],
        "comments": [{"id": 91, "body": record}],
    }

    snapshot = core.normalize_github_snapshot("owner/repo", [issue], [])

    normalized = snapshot["issues"][0]
    assert normalized["contract_version"] == 2
    assert normalized["change_claims"] == contract["change_claims"]
    assert normalized["dispatch_after"] == [3]
    assert normalized["merge_after"] == [4]


def test_v2_worker_prompt_and_delivery_use_claims_and_typed_dependencies():
    core = load_core()
    contract = _v2_contract(core)
    action = {
        "action_id": "create-worker-dispatch-issue-7-a1",
        "type": "create_worker",
        "dispatch_id": "dispatch-issue-7-a1",
        "issue": 7,
        "attempt": 1,
        "branch": "work/issue-7",
        "wave_generation": 1,
    }
    coordinator = {
        "agent_id": "root-a",
        "provider": "codex",
        "settings": {"model": "gpt-current", "modeId": "full-access"},
    }

    materialized = core.materialize_worker_action(
        action,
        {"number": 7, "contract": contract, "milestone": None},
        repository="owner/repo",
        base_sha="a" * 40,
        config=core.default_config(),
        coordinator_runtime=coordinator,
    )

    assert (
        'Change claims: {"paths": ["src/api"], "resources": ["schema:settings"]}'
        in materialized["initial_prompt"]
    )
    assert "Dispatch after: [3]" in materialized["initial_prompt"]
    assert "Merge after: [4]" in materialized["initial_prompt"]
    assert materialized["labels"]["orch.version"] == "6.1.0"

    delivery = {
        "contract_sha256": contract["sha256"],
        "candidate_sha": "b" * 40,
        "changed_paths": ["src/api/client.py"],
        "tdd": {"red": "failed", "green": "passed", "refactor": "clean"},
        "verification": ["pytest"],
        "deviations": [],
        "risks": [],
    }
    core.validate_delivery(delivery, contract, "b" * 40)
    with pytest.raises(core.PolicyError) as outside:
        core.validate_delivery(
            {**delivery, "changed_paths": ["src/ui/view.py"]},
            contract,
            "b" * 40,
        )
    assert outside.value.code == "DELIVERY_HOTSET_VIOLATION"


def test_merge_after_orders_integration_without_serializing_dispatch():
    core = load_core()
    issues = [
        {
            "number": 1,
            "state": "ready-to-merge",
            "priority": "P0",
            "dispatch_after": [],
            "merge_after": [2],
            "dispatch": {"accepted_at": "2026-07-20T10:00:00Z"},
        },
        {
            "number": 2,
            "state": "ready-to-merge",
            "priority": "P2",
            "dispatch_after": [],
            "merge_after": [],
            "dispatch": {"accepted_at": "2026-07-20T11:00:00Z"},
        },
    ]

    assert [issue["number"] for issue in core.integration_order(issues)] == [2, 1]


def test_resume_reacquires_execution_but_review_only_uses_integration_wip():
    core = load_core()
    contract = _v2_contract(core)
    parked = {
        "number": 7,
        "state": "blocked",
        "contract": contract,
        "contract_valid": True,
        "change_claims": contract["change_claims"],
        "dispatch": {
            "id": "dispatch-issue-7-a1",
            "status": "blocked",
            "parked": True,
            "generation": 1,
            "worker_agent_id": "worker-7",
            "workspace_id": "workspace-7",
            "branch": "work/issue-7",
            "base_sha": "a" * 40,
            "contract_sha256": contract["sha256"],
        },
    }
    reviewing = {
        "number": 8,
        "state": "review",
        "change_claims": {"paths": ["src/ui"], "resources": []},
        # Durable Dispatch may still say running after GitHub delivery readback
        # advances the compiled lifecycle state to review.
        "dispatch": {"status": "running", "parked": False},
    }
    snapshot = {
        "base_sha": "a" * 40,
        "execution_slots": 1,
        "integration_wip_limit": 2,
        "worker_slots": 1,
        "closed_issues": [3],
        "issues": [parked, reviewing],
        "runtime_agents": [
            {
                "id": "worker-7",
                "workspace_id": "workspace-7",
                "branch": "work/issue-7",
                "labels": {"orch.dispatch": "dispatch-issue-7-a1"},
                "state": "stopped",
            }
        ],
    }

    result = core.plan_lifecycle_command(snapshot, "dispatch-issue-7-a1", "resume")

    assert result["actions"][0]["type"] == "resume_worker"


def test_default_config_separates_execution_from_integration_capacity():
    core = load_core()
    config = core.default_config()

    assert config["global"]["execution_slots"] == 3
    assert config["global"]["integration_wip_limit"] == 6
    assert config["global"]["intake"]["ready_reserve_target"] == 6
    assert core.validate_config(config) == config

    invalid = {
        **config,
        "global": {**config["global"], "integration_wip_limit": 2},
    }
    with pytest.raises(core.PolicyError) as error:
        core.validate_config(invalid)
    assert error.value.code == "INTEGRATION_WIP_LIMIT_INVALID"


def test_width_aware_selection_is_bounded_for_a_full_candidate_frontier():
    frontier = load_frontier()
    snapshot = {
        "execution_slots": 5,
        "integration_wip_limit": 10,
        "closed_issues": [],
        "issues": [
            {
                "number": number,
                "state": "ready",
                "priority": "P1",
                "change_claims": {
                    "paths": [f"components/component-{number}"],
                    "resources": [],
                },
                "dispatch_after": [],
                "contract_valid": True,
            }
            for number in range(1, 101)
        ],
    }

    plan = frontier.select_wave(snapshot)

    assert plan["selected"] == [1, 2, 3, 4, 5]
    assert plan["parallel_width"] == 5
    assert plan["search_exhausted"] is False


def test_width_search_returns_best_found_wave_when_proving_more_width_is_costly():
    frontier = load_frontier()
    snapshot = {
        "execution_slots": 5,
        "integration_wip_limit": 10,
        "closed_issues": [],
        "issues": [
            {
                "number": number,
                "state": "ready",
                "priority": "P1",
                "change_claims": {
                    "paths": [f"items/{number}"],
                    "resources": [f"exclusive-group:{number % 4}"],
                },
                "dispatch_after": [],
                "contract_valid": True,
            }
            for number in range(1, 101)
        ],
    }

    plan = frontier.select_wave(snapshot)

    assert plan["selected"] == [1, 2, 3, 4]
    assert plan["parallel_width"] == 4
    assert plan["search_exhausted"] is True


def test_bounded_search_seeds_from_low_conflict_candidates_before_proof_search():
    frontier = load_frontier()
    issues = []
    for number in range(1, 101):
        path = "src" if number <= 95 else f"src/module-{number}"
        issues.append(
            {
                "number": number,
                "state": "ready",
                "priority": "P1",
                "change_claims": {"paths": [path], "resources": []},
                "dispatch_after": [],
                "contract_valid": True,
            }
        )

    plan = frontier.select_wave(
        {
            "execution_slots": 5,
            "integration_wip_limit": 10,
            "closed_issues": [],
            "issues": issues,
        }
    )

    assert plan["selected"] == [96, 97, 98, 99, 100]
    assert plan["parallel_width"] == 5


def test_claims_overlap_normalizes_case_and_whitespace():
    frontier = load_frontier()
    right = {"paths": ["src/api"], "resources": []}
    assert frontier.claims_overlap({"paths": ["Src/Api"], "resources": []}, right)
    assert frontier.claims_overlap(
        {"paths": [" src/api/deep "], "resources": []}, right
    )
    assert not frontier.claims_overlap({"paths": ["src/api2"], "resources": []}, right)


def test_milestone_due_does_not_steer_wave_selection():
    frontier = load_frontier()
    snapshot = {
        "execution_slots": 1,
        "integration_wip_limit": 2,
        "closed_issues": [],
        "issues": [
            {
                "number": 1,
                "state": "ready",
                "priority": "P1",
                "milestone_due": "2027-01-01",
                "change_claims": {"paths": ["src/a"], "resources": []},
                "dispatch_after": [],
                "contract_valid": True,
            },
            {
                "number": 2,
                "state": "ready",
                "priority": "P1",
                "milestone_due": "2026-01-01",
                "change_claims": {"paths": ["src/b"], "resources": []},
                "dispatch_after": [],
                "contract_valid": True,
            },
        ],
    }
    assert frontier.select_wave(snapshot)["selected"] == [1]


def test_config_validation_rejects_invalid_slots_attempts_and_intake():
    core = load_core()
    config = core.default_config()
    cases = [
        ({"execution_slots": 0}, "EXECUTION_SLOTS_INVALID"),
        ({"execution_slots": 6}, "EXECUTION_SLOTS_INVALID"),
        (
            {"execution_slots": 3, "worker_slots": 4},
            "EXECUTION_SLOTS_INVALID",
        ),
        ({"max_attempts": 0}, "ATTEMPTS_INVALID"),
        ({"intake": ["bug"]}, "INTAKE_CONFIG_INVALID"),
        ({"intake": {"include_labels": ["Bug", "bug"]}}, "INTAKE_CONFIG_INVALID"),
    ]
    for override, code in cases:
        invalid = {**config, "global": {**config["global"], **override}}
        with pytest.raises(core.PolicyError) as error:
            core.validate_config(invalid)
        assert error.value.code == code
