from __future__ import annotations

import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "run_v8_canary.py"
SPEC = importlib.util.spec_from_file_location("run_v8_canary", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_smoke_plan_compiles_exact_hosted_boundary_contract():
    command = ["python", "-m", "pytest", "-q"]
    policy = {
        "version": 3,
        "low_risk_allowlist": ["canary_nodes/*.py"],
        "check_definitions": [
            {
                "check_id": "canary-affected",
                "version": 1,
                "command": command,
                "hosted_name": None,
                "environment_requirements": ["python"],
                "input_selector": ["canary_nodes/*.py"],
                "base_sensitive": False,
                "risk": "low",
                "hosted_only": False,
                "suite": "repository",
            },
            {
                "check_id": "canary-repository",
                "version": 1,
                "command": command,
                "hosted_name": None,
                "environment_requirements": ["python"],
                "input_selector": ["canary_nodes/*.py"],
                "base_sensitive": False,
                "risk": "low",
                "hosted_only": False,
                "suite": "repository",
            },
            {
                "check_id": "canary-hosted",
                "version": 1,
                "command": command,
                "hosted_name": "GWO Canary CI",
                "environment_requirements": [],
                "input_selector": ["canary_nodes/*.py"],
                "base_sensitive": False,
                "risk": "low",
                "hosted_only": True,
                "suite": "hosted",
            },
        ],
        "strict_review": {
            "specialist_requirements": [],
            "human_decision_required": True,
        },
    }

    compiled = MODULE.build_smoke_plan(
        repository="NOirBRight/gwo-v8-canary",
        run_id="unit",
        policy=policy,
    )

    plan = json.loads(compiled.canonical_bytes)
    work = next(node for node in plan["nodes"] if node["kind"] == "work")
    checks = work["output_contract"]["checks"]
    assert work["risk"] == "low"
    assert work["output_contract"]["delivery_required"] is True
    assert [item["check_id"] for item in checks] == [
        "canary-affected",
        "canary-hosted",
        "canary-repository",
    ]
    assert [item["hosted_name"] for item in checks if item["hosted_only"]] == [
        "GWO Canary CI"
    ]
