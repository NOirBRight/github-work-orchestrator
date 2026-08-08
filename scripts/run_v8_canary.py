"""Run the live GWO V8 smoke or three-node Batch acceptance canary."""

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

from gwo_v8.activation import (  # noqa: E402
    ActivationError,
    GitHubCliContentClient,
    GitHubDurablePlanControl,
    LocalPlanPublication,
)
from gwo_v8.compiler import PlanCompiler  # noqa: E402
from gwo_v8.evidence import EvidenceVerifier  # noqa: E402
from gwo_v8.kernel import GitHubCliDeliveryControl, Kernel  # noqa: E402
from gwo_v8.runtime import (  # noqa: E402
    InMemoryRuntimeAdapter,
    PaseoCliClient,
    PaseoRuntimeAdapter,
)
from gwo_v8.runtime_profile import RuntimeProfile  # noqa: E402
import orch_core  # noqa: E402


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


def build_full_plan(
    *,
    repository: str,
    run_id: str,
    policy: dict[str, Any],
):
    goal_key = f"goal:canary-full:{run_id}"
    checks = [
        {
            "check_id": definition["check_id"],
            "command": list(definition["command"]),
        }
        for definition in policy["check_definitions"]
    ]
    nodes = []
    work_items = []
    for name in ("alpha", "beta", "gamma"):
        path = f"canary_nodes/{name}.py"
        content = f'VALUE = "batch-{run_id}-{name}"\n'
        work_item_key = f"canary-full:{run_id}:{name}"
        nodes.append(
            {
                "goal_key": goal_key,
                "work_item_key": work_item_key,
                "kind": "work",
                "inputs": {
                    "file_changes": [{"path": path, "content": content}]
                },
                "output_contract": {
                    "required_evidence": [
                        {"kind": "candidate"},
                        {"kind": "check", "check_id": "canary-affected"},
                        {"kind": "check", "check_id": "canary-repository"},
                    ],
                    "checks": checks,
                },
                "effect_contract": {
                    "write_scopes": [path],
                    "external_effects": [],
                },
                "resource_claims": [f"canary:{name}"],
                "runtime_requirements": {
                    "capabilities": ["git", "local_check"]
                },
                "difficulty": "standard",
                "risk": "standard",
                "recovery_policy": {
                    "semantic_attempts": 2,
                    "repair_rounds": 1,
                },
                "skill_reference": None,
            }
        )
        work_items.append(
            {
                "work_item_key": work_item_key,
                "tracker_state": "ready-for-agent",
                "source_ref": f"github://{repository}/canary/{run_id}/{name}",
                "title": f"V8 Batch canary {name}",
                "outcome_contract": {"path": path, "content": content},
            }
        )
    return PlanCompiler().compile(
        {
            "parent_plan_digest": None,
            "goals": [
                {
                    "goal_key": goal_key,
                    "objective": (
                        "Prove three parallel reviewed Candidates cross one "
                        "Integration Batch boundary."
                    ),
                    "acceptance": [
                        "All three exact module values are present.",
                        "Each Candidate has Standards and Spec Review Evidence.",
                        "One Batch SHA passes hosted CI and becomes main.",
                    ],
                }
            ],
            "nodes": nodes,
            "edges": [],
        },
        {"repository": repository, "work_items": work_items},
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
            print(
                f"transient durable read failure: {error}",
                file=sys.stderr,
                flush=True,
            )
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


def run_full(
    *,
    repository: str,
    repository_path: Path,
    timeout_seconds: int,
    poll_seconds: int,
    worker_thinking_override: str | None = None,
) -> dict[str, Any]:
    repository_path = repository_path.resolve()
    def progress(message: str) -> None:
        print(message, file=sys.stderr, flush=True)

    progress("full canary: preflight")
    if _git(repository_path, "status", "--porcelain=v1", "--untracked-files=all"):
        raise RuntimeError("canary repository must be clean")
    if _git(repository_path, "branch", "--show-current") != "main":
        raise RuntimeError("full canary must run from local main")
    _git(repository_path, "pull", "--ff-only", "origin", "main")
    run_id = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    policy = json.loads(
        (repository_path / ".gwo" / "policy.json").read_text(encoding="utf-8")
    )
    compiled = build_full_plan(
        repository=repository,
        run_id=run_id,
        policy=policy,
    )
    state_root = repository_path / ".gwo-v8-local"
    state_root.mkdir(parents=True, exist_ok=True)
    store_path = state_root / "store.sqlite3"
    if store_path.exists():
        Kernel.drain_store_ownership(
            store_path,
            repository=repository,
            source_ref=f"canary://full-run-reset/{run_id}",
        )
        progress("full canary: prior local ownership drained")
    durable = GitHubDurablePlanControl(
        GitHubCliContentClient(),
        branch="gwo-control",
    )
    publication = LocalPlanPublication(store_path, durable=durable)
    previous = durable.read_current_activation(repository)
    progress("full canary: durable activation read")
    writer_generation = (
        "v8-canary-batch"
        if previous is None
        else previous.writer_generation
    )
    activation = publication.publish_and_activate(
        compiled,
        expected_active_digest=(None if previous is None else previous.plan_digest),
        writer_generation=writer_generation,
    )
    progress(f"full canary: activated {compiled.digest[:12]}")
    runtime_config = orch_core.default_config()
    runtime_config["active_turn_pools"] = {"workers": 3, "coordinators": 1}
    runtime_config["repositories"] = {}
    worker_mapping = runtime_config["tiers"]["standard"]
    worker_settings = worker_mapping["settings"]
    worker_thinking = worker_settings["thinkingOptionId"]
    if worker_thinking_override not in {None, worker_thinking}:
        raise ValueError(
            "--worker-thinking-override is retained for compatibility and "
            f"must equal the configured value {worker_thinking!r}"
        )
    progress(f"full canary: configured Kimi thinking -> {worker_thinking}")
    runtime = PaseoRuntimeAdapter(PaseoCliClient())
    kernel = Kernel(
        store_path=store_path,
        publication=publication,
        runtime=runtime,
        verifier=EvidenceVerifier(),
        repository_path=repository_path,
        integration_branch="main",
        writer_generation=writer_generation,
        runtime_profile=RuntimeProfile(
            name="standard",
            provider=worker_mapping["provider"],
            model=worker_settings["model"],
            thinking=worker_thinking,
            mode=worker_settings["modeId"],
            features=dict(worker_settings.get("features") or {}),
        ),
        runtime_config=runtime_config,
        delivery_control=GitHubCliDeliveryControl(
            repository_path=repository_path,
        ),
        parent_agent_id=None,
    )
    started = time.monotonic()
    history = []
    while True:
        progress("full canary: reconcile")
        try:
            outcome = kernel.reconcile_once(repository)
        except ActivationError as error:
            if error.code != "DURABLE_READ_FAILED":
                raise
            print(
                f"transient durable read failure: {error}",
                file=sys.stderr,
                flush=True,
            )
            if time.monotonic() - started >= timeout_seconds:
                raise TimeoutError(
                    f"full canary durable read exceeded {timeout_seconds} seconds"
                ) from error
            time.sleep(poll_seconds)
            continue
        progress(
            "full canary: "
            f"{outcome.status}/{outcome.wait_condition or 'runnable'}"
        )
        history.append(asdict(outcome))
        if outcome.status == "complete":
            break
        if outcome.status in {"failed", "blocked", "superseded"}:
            raise RuntimeError(
                f"full canary stopped in {outcome.status}: {outcome}"
            )
        if time.monotonic() - started >= timeout_seconds:
            raise TimeoutError(
                f"full canary exceeded {timeout_seconds} seconds"
            )
        time.sleep(poll_seconds)

    states = kernel._read_states(repository, compiled.digest)
    if len(states) != 3 or any(state.get("status") != "complete" for state in states):
        raise RuntimeError("full canary did not complete exactly three Work Items")
    candidate_shas = tuple(sorted(str(state["candidate_sha"]) for state in states))
    if len(set(candidate_shas)) != 3:
        raise RuntimeError("full canary did not produce three distinct Candidates")
    batch_ids = {str(state.get("integration_batch_id")) for state in states}
    batch_shas = {str(state.get("integration_batch_sha")) for state in states}
    if len(batch_ids) != 1 or len(batch_shas) != 1:
        raise RuntimeError("full canary did not converge through one Integration Batch")
    batch_id = next(iter(batch_ids))
    batch_sha = next(iter(batch_shas))
    review_axes: dict[str, list[str]] = {}
    for state in states:
        review = next(
            (
                item
                for item in state["candidate_observation"]["evidence"]
                if item["kind"] == "review"
            ),
            None,
        )
        axes = [] if review is None else [
            str(axis["axis"]) for axis in review["payload"]["axes"]
        ]
        if set(axes) != {"standards", "spec"}:
            raise RuntimeError(
                f"Candidate lacks dual-axis Review Evidence: {state['node_key']}"
            )
        review_axes[str(state["candidate_sha"])] = sorted(axes)
    if any(
        node.get("wait_condition") == "integration_refresh"
        for turn in history
        for node in turn.get("node_outcomes") or ()
    ):
        raise RuntimeError("full canary unexpectedly entered Integration refresh")
    head = _git(repository_path, "rev-parse", "HEAD")
    remote = _git(repository_path, "ls-remote", "origin", "refs/heads/main")
    remote_head = remote.split(maxsplit=1)[0] if remote else ""
    if head != remote_head or head != batch_sha:
        raise RuntimeError("local, remote, and Batch Integration readback differ")
    with sqlite3.connect(store_path) as connection:
        batch_row = connection.execute(
            """
            SELECT state_json FROM v8_integration_batches
            WHERE repository = ? AND plan_digest = ? AND batch_id = ?
            """,
            (repository, compiled.digest, batch_id),
        ).fetchone()
    if batch_row is None:
        raise RuntimeError("durable local Integration Batch state is missing")
    batch_state = json.loads(batch_row[0])
    if batch_state.get("state") != "integrated":
        raise RuntimeError("Integration Batch did not reach integrated readback")
    result = {
        "schema_version": 1,
        "repository": repository,
        "run_id": run_id,
        "plan_digest": compiled.digest,
        "activation_id": activation.activation_id,
        "candidate_shas": list(candidate_shas),
        "integration_batch_id": batch_id,
        "integration_batch_sha": batch_sha,
        "integrated_sha": head,
        "review_axes": review_axes,
        "hosted_check_state": batch_state.get("hosted_check_state"),
        "hosted_retry_count": batch_state.get("hosted_retry_count"),
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "history": history,
    }
    (state_root / "last-full.json").write_text(
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
    parser.add_argument(
        "--full",
        action="store_true",
        help="run three Paseo Workers with dual-axis Review and one Batch CI",
    )
    parser.add_argument(
        "--worker-thinking-override",
        help=(
            "deprecated compatibility option; when supplied it must match the "
            "configured Kimi thinking value"
        ),
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.recover_report:
        result = recover_smoke_report(
            repository=args.repository,
            repository_path=args.repository_path,
        )
    elif args.full:
        result = run_full(
            repository=args.repository,
            repository_path=args.repository_path,
            timeout_seconds=args.timeout_seconds,
            poll_seconds=args.poll_seconds,
            worker_thinking_override=args.worker_thinking_override,
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
