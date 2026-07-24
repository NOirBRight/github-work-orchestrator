"""Run the live GitHub boundary smoke for the dedicated GWO V8 canary."""

from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills" / "orchestrator" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from gwo_v8 import (  # noqa: E402
    ActivationError,
    EvidenceVerifier,
    GitHubCliContentClient,
    GitHubCliDeliveryControl,
    GitHubDurablePlanControl,
    InMemoryRuntimeAdapter,
    Kernel,
    LocalPlanPublication,
    PlanCompiler,
)


DEFAULT_REPOSITORY = "NOirBRight/gwo-v8-canary"
DEFAULT_REPOSITORY_PATH = Path(
    os.environ.get(
        "GWO_V8_CANARY_PATH",
        str(ROOT.parent / "gwo-v8-canary"),
    )
)


def _git(repository_path: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repository_path), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if result.returncode != 0:
        raise RuntimeError(
            result.stderr.strip() or result.stdout.strip() or "git failed"
        )
    return result.stdout.strip()


def build_smoke_plan(
    *,
    repository: str,
    run_id: str,
    policy: dict[str, Any],
):
    path = "canary_nodes/alpha.py"
    content = f'VALUE = "smoke-{run_id}"\n'
    checks = [
        {
            "check_id": definition["check_id"],
            "command": list(definition["command"]),
        }
        for definition in policy["check_definitions"]
    ]
    return PlanCompiler().compile(
        {
            "parent_plan_digest": None,
            "goals": [
                {
                    "goal_key": f"goal:canary-smoke:{run_id}",
                    "objective": "Prove the live GitHub boundary on one exact SHA.",
                    "acceptance": [
                        "The exact candidate passes hosted CI and becomes main."
                    ],
                }
            ],
            "nodes": [
                {
                    "goal_key": f"goal:canary-smoke:{run_id}",
                    "work_item_key": f"canary-smoke:{run_id}",
                    "kind": "work",
                    "inputs": {
                        "file_changes": [{"path": path, "content": content}]
                    },
                    "output_contract": {
                        "required_evidence": [
                            {"kind": "candidate"},
                            {
                                "kind": "check",
                                "check_id": "canary-affected",
                            },
                            {
                                "kind": "check",
                                "check_id": "canary-repository",
                            },
                        ],
                        "checks": checks,
                    },
                    "effect_contract": {
                        "write_scopes": [path],
                        "external_effects": [],
                    },
                    "resource_claims": ["canary:alpha"],
                    "runtime_requirements": {
                        "capabilities": ["git", "local_check"]
                    },
                    "difficulty": "routine",
                    "risk": "low",
                    "recovery_policy": {
                        "semantic_attempts": 2,
                        "repair_rounds": 1,
                    },
                    "skill_reference": None,
                }
            ],
            "edges": [],
        },
        {
            "repository": repository,
            "work_items": [
                {
                    "work_item_key": f"canary-smoke:{run_id}",
                    "tracker_state": "ready-for-agent",
                    "source_ref": f"github://{repository}/canary/{run_id}",
                    "title": "V8 live boundary smoke",
                    "outcome_contract": {
                        "path": path,
                        "content": content,
                    },
                }
            ],
        },
        policy,
    )


def run_smoke(
    *,
    repository: str,
    repository_path: Path,
    timeout_seconds: int,
    poll_seconds: int,
) -> dict[str, Any]:
    repository_path = repository_path.resolve()
    if _git(repository_path, "status", "--porcelain=v1", "--untracked-files=all"):
        raise RuntimeError("canary repository must be clean")
    if _git(repository_path, "branch", "--show-current") != "main":
        raise RuntimeError("canary smoke must run from local main")
    _git(repository_path, "pull", "--ff-only", "origin", "main")
    run_id = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    policy = json.loads(
        (repository_path / ".gwo" / "policy.json").read_text(encoding="utf-8")
    )
    compiled = build_smoke_plan(
        repository=repository,
        run_id=run_id,
        policy=policy,
    )
    state_root = repository_path / ".gwo-v8-local"
    state_root.mkdir(parents=True, exist_ok=True)
    store_path = state_root / "store.sqlite3"
    durable = GitHubDurablePlanControl(
        GitHubCliContentClient(),
        branch="gwo-control",
    )
    publication = LocalPlanPublication(store_path, durable=durable)
    previous = durable.read_current_activation(repository)
    activation = publication.publish_and_activate(
        compiled,
        expected_active_digest=(
            None if previous is None else previous.plan_digest
        ),
        writer_generation="v8-canary-smoke",
    )
    runtime = InMemoryRuntimeAdapter(state_root / "worktrees")
    kernel = Kernel(
        store_path=store_path,
        publication=publication,
        runtime=runtime,
        verifier=EvidenceVerifier(),
        repository_path=repository_path,
        integration_branch="main",
        writer_generation="v8-canary-smoke",
        runtime_config={
            "active_turn_pools": {"workers": 1, "coordinators": 1},
            "repositories": {},
        },
        delivery_control=GitHubCliDeliveryControl(
            repository_path=repository_path,
        ),
    )
    started = time.monotonic()
    history = []
    while True:
        try:
            outcome = kernel.reconcile_once(repository)
        except ActivationError as error:
            if error.code != "DURABLE_READ_FAILED":
                raise
            if time.monotonic() - started >= timeout_seconds:
                raise TimeoutError(
                    f"canary durable read exceeded {timeout_seconds} seconds"
                ) from error
            time.sleep(poll_seconds)
            continue
        history.append(asdict(outcome))
        if outcome.status == "complete":
            break
        if outcome.status in {"failed", "blocked", "superseded"}:
            raise RuntimeError(
                f"canary smoke stopped in {outcome.status}: {outcome}"
            )
        if time.monotonic() - started >= timeout_seconds:
            raise TimeoutError(
                f"canary smoke exceeded {timeout_seconds} seconds"
            )
        time.sleep(poll_seconds)
    head = _git(repository_path, "rev-parse", "HEAD")
    remote = _git(repository_path, "ls-remote", "origin", "refs/heads/main")
    remote_head = remote.split(maxsplit=1)[0] if remote else ""
    if head != remote_head or head != outcome.candidate_sha:
        raise RuntimeError("local, remote, and Kernel Integration readback differ")
    result = {
        "schema_version": 1,
        "repository": repository,
        "run_id": run_id,
        "plan_digest": compiled.digest,
        "activation_id": activation.activation_id,
        "candidate_sha": outcome.candidate_sha,
        "integrated_sha": head,
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "history": history,
    }
    (state_root / "last-smoke.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return result


def recover_smoke_report(
    *,
    repository: str,
    repository_path: Path,
) -> dict[str, Any]:
    repository_path = repository_path.resolve()
    state_root = repository_path / ".gwo-v8-local"
    store_path = state_root / "store.sqlite3"
    with sqlite3.connect(store_path) as connection:
        row = connection.execute(
            """
            SELECT active.plan_digest, active.activation_id, state.state_json
            FROM v8_active_plans AS active
            JOIN v8_node_execution_state AS state
              ON state.repository = active.repository
             AND state.plan_digest = active.plan_digest
            WHERE active.repository = ?
            ORDER BY state.node_key
            """,
            (repository,),
        ).fetchone()
    if row is None:
        raise RuntimeError("active canary execution state is missing")
    state = json.loads(row[2])
    if state.get("status") != "complete" or not state.get("candidate_sha"):
        raise RuntimeError("active canary execution is not complete")
    head = _git(repository_path, "rev-parse", "HEAD")
    remote = _git(repository_path, "ls-remote", "origin", "refs/heads/main")
    remote_head = remote.split(maxsplit=1)[0] if remote else ""
    if head != remote_head or head != state["candidate_sha"]:
        raise RuntimeError("completed Store and Git Integration readback differ")
    result = {
        "schema_version": 1,
        "repository": repository,
        "run_id": str(state["goal_key"]).rsplit(":", 1)[-1],
        "plan_digest": str(row[0]),
        "activation_id": str(row[1]),
        "candidate_sha": state["candidate_sha"],
        "integrated_sha": head,
        "elapsed_seconds": None,
        "recovered_report": True,
        "history": [state],
    }
    (state_root / "last-smoke.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repository",
        default=DEFAULT_REPOSITORY,
    )
    parser.add_argument(
        "--repository-path",
        type=Path,
        default=DEFAULT_REPOSITORY_PATH,
    )
    parser.add_argument("--timeout-seconds", type=int, default=360)
    parser.add_argument("--poll-seconds", type=int, default=10)
    parser.add_argument("--recover-report", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.recover_report:
        result = recover_smoke_report(
            repository=args.repository,
            repository_path=args.repository_path,
        )
    else:
        result = run_smoke(
            repository=args.repository,
            repository_path=args.repository_path,
            timeout_seconds=args.timeout_seconds,
            poll_seconds=args.poll_seconds,
        )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
