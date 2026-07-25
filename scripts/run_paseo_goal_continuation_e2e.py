#!/usr/bin/env python3
"""Opt-in live E2E for missed Paseo finish notification continuation."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import uuid


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills" / "orchestrator" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from gwo_v8 import (  # noqa: E402
    EvidenceVerifier,
    GoalDriver,
    GoalSnapshot,
    InMemoryCoordinatorRuntime,
    InMemoryDeliveryControl,
    InMemoryDurableGoalControl,
    InMemorySkillCatalog,
    Kernel,
    LocalPlanPublication,
    PaseoCliClient,
    PaseoRuntimeAdapter,
    PlanCompiler,
    RuntimeAdmission,
    RuntimeProfile,
)


def _git(repository: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repository), *args],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return result.stdout.strip()


def _inputs(repository_key: str) -> tuple[dict, dict, dict]:
    check_command = [
        "python",
        "-c",
        (
            "from pathlib import Path; "
            "assert Path('continuation.txt').read_text() "
            "== 'missed finish adopted\\n'"
        ),
    ]
    work_item = {
        "work_item_key": "issue:live-continuation",
        "tracker_state": "ready-for-agent",
        "source_ref": "e2e://paseo-goal-continuation",
        "title": "Prove missed finish continuation",
        "outcome_contract": {
            "path": "continuation.txt",
            "content": "missed finish adopted\n",
        },
    }
    node = {
        "goal_key": "goal:live-continuation",
        "work_item_key": work_item["work_item_key"],
        "kind": "work",
        "inputs": {
            "file_changes": [
                {
                    "path": "continuation.txt",
                    "content": "missed finish adopted\n",
                }
            ],
        },
        "output_contract": {
            "required_evidence": [
                {"kind": "candidate"},
                {"kind": "check", "check_id": "live-continuation"},
            ],
            "checks": [
                {
                    "check_id": "live-continuation",
                    "command": check_command,
                }
            ],
        },
        "effect_contract": {
            "write_scopes": ["continuation.txt"],
            "external_effects": [],
        },
        "resource_claims": [],
        "runtime_requirements": {
            "capabilities": ["git", "local_check", "paseo"],
        },
        "difficulty": "frontier",
        "risk": "low",
        "recovery_policy": {
            "semantic_attempts": 1,
            "repair_rounds": 0,
        },
        "skill_reference": "live-continuation-delay",
    }
    intent = {
        "parent_plan_digest": None,
        "goals": [
            {
                "goal_key": node["goal_key"],
                "objective": "Adopt a detached Worker result after a missed finish.",
                "acceptance": ["The exact Candidate is integrated and retired."],
            }
        ],
        "nodes": [node],
        "edges": [],
    }
    source = {
        "repository": repository_key,
        "work_items": [work_item],
    }
    policy = {
        "version": 3,
        "low_risk_allowlist": ["continuation.txt"],
        "check_definitions": [
            {
                "check_id": "live-continuation",
                "version": 1,
                "command": check_command,
                "hosted_name": None,
                "environment_requirements": ["python"],
                "input_selector": ["continuation.txt"],
                "base_sensitive": False,
                "risk": "low",
                "hosted_only": False,
                "suite": "repository",
            },
            {
                "check_id": "live-continuation-hosted",
                "version": 1,
                "command": ["python", "-c", "raise SystemExit(0)"],
                "hosted_name": "Live continuation CI",
                "environment_requirements": [],
                "input_selector": ["continuation.txt"],
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
    return intent, source, policy


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--live",
        action="store_true",
        help="Confirm that the installed Paseo daemon may create one live Agent.",
    )
    parser.add_argument("--executable", default="paseo")
    parser.add_argument(
        "--provider",
        default=os.environ.get("GWO_E2E_PROVIDER", "codex"),
    )
    parser.add_argument(
        "--model",
        default=os.environ.get("GWO_E2E_MODEL", "gpt-5.6-sol"),
    )
    parser.add_argument(
        "--thinking",
        default=os.environ.get("GWO_E2E_THINKING", "high"),
    )
    parser.add_argument(
        "--mode",
        default=os.environ.get("GWO_E2E_MODE", "full-access"),
    )
    parser.add_argument("--timeout-seconds", type=float, default=300.0)
    return parser.parse_args()


def _wait_for_terminal_result(
    client: PaseoCliClient,
    agent_id: str,
    *,
    deadline: float,
) -> None:
    while time.monotonic() < deadline:
        agent = client.inspect(agent_id)
        output = client.read_output(agent_id) or ""
        if agent.lifecycle in {"idle", "completed", "ready"} and "GWO_RESULT" in output:
            return
        time.sleep(1.0)
    raise TimeoutError("detached Paseo Worker did not return GWO_RESULT in time")


def _wait_until_due(next_check_at: str | None, *, deadline: float) -> None:
    if next_check_at is None:
        return
    due = datetime.fromisoformat(next_check_at.replace("Z", "+00:00"))
    while datetime.now(timezone.utc) < due:
        if time.monotonic() >= deadline:
            raise TimeoutError("Goal Driver next_check_at did not become due in time")
        time.sleep(min(0.5, max(0.0, (due - datetime.now(timezone.utc)).total_seconds())))


def main() -> int:
    args = _arguments()
    if sys.platform != "win32":
        raise SystemExit("this live Paseo CLI E2E is intentionally Windows-only")
    if (
        not args.live
        and os.environ.get("GWO_RUN_PASEO_GOAL_CONTINUATION_E2E") != "1"
    ):
        raise SystemExit(
            "live Paseo Agent creation is opt-in; pass --live or set "
            "GWO_RUN_PASEO_GOAL_CONTINUATION_E2E=1"
        )
    if args.timeout_seconds <= 0:
        raise SystemExit("--timeout-seconds must be positive")

    run_id = uuid.uuid4().hex
    repository_key = f"local/paseo-goal-continuation/{run_id}"
    declared_parent = f"gwo-live-parent-{run_id}"
    profile = RuntimeProfile(
        name="paseo-goal-continuation-e2e",
        provider=args.provider,
        model=args.model,
        thinking=args.thinking,
        mode=args.mode,
        features={},
    )
    coordinator_profile = RuntimeProfile(
        name="coordinator_auto",
        provider="kimi-cli",
        model="kimi-code/k3",
        thinking="max",
        mode="yolo",
        features={},
    )
    client = PaseoCliClient(executable=args.executable)
    created_agent_id: str | None = None
    result: dict[str, object] | None = None

    with tempfile.TemporaryDirectory(
        prefix="gwo-paseo-goal-continuation-",
        ignore_cleanup_errors=True,
    ) as temporary:
        repository = Path(temporary) / "repository"
        subprocess.run(
            ["git", "init", "-b", "main", str(repository)],
            check=True,
            capture_output=True,
        )
        _git(repository, "config", "user.name", "GWO Continuation E2E")
        _git(repository, "config", "user.email", "gwo-e2e@example.test")
        (repository / "README.md").write_text(
            "Paseo Goal continuation E2E\n",
            encoding="utf-8",
        )
        _git(repository, "add", "README.md")
        _git(repository, "commit", "-m", "Paseo Goal continuation E2E base")

        intent, source, policy = _inputs(repository_key)
        compiled = PlanCompiler().compile(intent, source, policy)
        plan = json.loads(compiled.canonical_bytes)
        work_node = next(node for node in plan["nodes"] if node["kind"] == "work")
        store_path = Path(temporary) / "gwo-v8.sqlite3"
        publication = LocalPlanPublication(store_path)
        publication.publish_and_activate(
            compiled,
            expected_active_digest=None,
            writer_generation="paseo-goal-continuation-e2e",
        )
        delivery = InMemoryDeliveryControl(hosted_outcomes=("passed",))

        def new_kernel(active_client: PaseoCliClient) -> Kernel:
            return Kernel(
                store_path=store_path,
                publication=publication,
                runtime=PaseoRuntimeAdapter(active_client),
                verifier=EvidenceVerifier(),
                repository_path=repository,
                integration_branch="main",
                writer_generation="paseo-goal-continuation-e2e",
                runtime_profile=profile,
                frontier_runtime_profile=profile,
                delivery_control=delivery,
                parent_agent_id=declared_parent,
                skill_catalog=InMemorySkillCatalog(
                    {
                        "live-continuation-delay": (
                            "For this timing E2E only, wait 45 seconds before "
                            "editing files. Then implement the frozen Plan Node, "
                            "run its focused check, commit, and return GWO_RESULT."
                        )
                    }
                ),
            )

        snapshot = GoalSnapshot(
            repository=repository_key,
            goal_key="goal:live-continuation",
            objective="Adopt a detached Worker result after a missed finish.",
            acceptance=("The exact Candidate is integrated and retired.",),
            plan_digest=compiled.digest,
            work_items=(("issue:live-continuation", "active"),),
            decision_inputs=(),
        )
        durable = InMemoryDurableGoalControl()
        kernel = new_kernel(client)
        driver = GoalDriver(
            store_path=store_path,
            reconciler=kernel,
            coordinators=InMemoryCoordinatorRuntime(),
            auto_profile=coordinator_profile,
            durable=durable,
        )
        try:
            deadline = time.monotonic() + args.timeout_seconds
            binding = None
            record = None
            prompt = None

            def read_worker():
                records = client.find_by_labels(
                    {
                        "gwo.repository": repository_key,
                        "gwo.node": work_node["node_key"],
                    }
                )
                if len(records) > 1:
                    raise AssertionError("Goal created duplicate Paseo Workers")
                state = kernel._read_state(
                    repository_key,
                    compiled.digest,
                    work_node["node_key"],
                )
                if not records or state is None:
                    return None, None, None
                frozen_prompt = kernel._prompt_from_state(state)
                readback = kernel.runtime.read_binding(
                    RuntimeAdmission(
                        repository=repository_key,
                        plan_digest=compiled.digest,
                        node_key=work_node["node_key"],
                        admission_id=state["admission_id"],
                        repository_path=repository,
                        base_sha=state["base_sha"],
                        runtime_profile=profile,
                        parent_agent_id=declared_parent,
                    ),
                    frozen_prompt,
                )
                if state.get("attempt_id") is None:
                    return records[0], frozen_prompt, None
                return records[0], frozen_prompt, readback

            while True:
                waiting = driver.run_once(snapshot)
                if waiting.kind != "wait":
                    raise AssertionError(
                        "Worker completed before Binding inspection: "
                        f"{waiting.kind}/{waiting.wait_condition}"
                    )
                persisted = driver.read_status(
                    repository_key,
                    snapshot.goal_key,
                )
                if persisted is None:
                    raise AssertionError(
                        "Goal Driver omitted materialization wait state"
                    )
                due = (
                    None
                    if persisted.next_check_at is None
                    else datetime.fromisoformat(
                        persisted.next_check_at.replace("Z", "+00:00")
                    )
                )
                while True:
                    record, prompt, binding = read_worker()
                    if (
                        binding is not None
                        and binding.prompt_accepted
                    ):
                        break
                    if (
                        due is None
                        or datetime.now(timezone.utc) >= due
                        or time.monotonic() >= deadline
                    ):
                        break
                    time.sleep(0.5)
                if (
                    binding is not None
                    and binding.prompt_accepted
                ):
                    break
                _wait_until_due(
                    persisted.next_check_at,
                    deadline=deadline,
                )
                if (
                    binding is None
                    or not binding.prompt_accepted
                ) and time.monotonic() >= deadline:
                    raise TimeoutError(
                        "Paseo Worker Materialization did not converge in time"
                    )
            assert record is not None
            assert prompt is not None
            created_agent_id = record.agent_id
            if binding.parent_agent_id is not None:
                raise AssertionError("detached CLI fabricated ParentAgentId")
            if binding.declared_parent_agent_id != declared_parent:
                raise AssertionError("declared GWO ownership was not preserved")
            if binding.native_finish_notification_supported:
                raise AssertionError("detached CLI claimed native finish notification")

            _wait_for_terminal_result(
                client,
                record.agent_id,
                deadline=deadline,
            )
            prompt_acceptance_count = client.prompt_acceptance_count(
                record.agent_id,
                prompt,
            )
            if prompt_acceptance_count != 1:
                raise AssertionError("Worker Prompt was not accepted exactly once")
            persisted_wait = driver.read_status(
                repository_key,
                snapshot.goal_key,
            )
            if (
                persisted_wait is None
                or persisted_wait.wait_condition is None
                or persisted_wait.last_wake_reference is not None
            ):
                raise AssertionError("missed finish did not leave one durable wait")
            _wait_until_due(
                persisted_wait.next_check_at,
                deadline=deadline,
            )

            client = PaseoCliClient(executable=args.executable)
            restarted_kernel = new_kernel(client)
            restarted_driver = GoalDriver(
                store_path=store_path,
                reconciler=restarted_kernel,
                coordinators=InMemoryCoordinatorRuntime(),
                auto_profile=coordinator_profile,
                durable=durable,
            )
            adopted = restarted_driver.run_once(snapshot)
            adopted_state = restarted_kernel._read_state(
                repository_key,
                compiled.digest,
                work_node["node_key"],
            )
            if adopted_state is None:
                raise AssertionError("due readback omitted the adopted Result")
            candidate_sha = adopted_state.get("candidate_sha")
            result_digest = adopted_state.get("result_digest")
            if (
                not isinstance(candidate_sha, str)
                or not isinstance(result_digest, str)
                or not result_digest
            ):
                raise AssertionError("due readback did not adopt the exact Result")
            if not client.inspect(record.agent_id).archived:
                raise AssertionError("adopted Worker was not retired")

            repeated = restarted_driver.run_once(snapshot)
            repeated_state = restarted_kernel._read_state(
                repository_key,
                compiled.digest,
                work_node["node_key"],
            )
            records = client.find_by_labels({"gwo.repository": repository_key})
            if len(records) != 1:
                raise AssertionError("restart created a duplicate Paseo Worker")
            if (
                repeated_state is None
                or repeated_state.get("candidate_sha") != candidate_sha
                or repeated_state.get("result_digest") != result_digest
            ):
                raise AssertionError("repeated readback changed exact-once adoption")
            result = {
                "agent_id": record.agent_id,
                "candidate_sha": candidate_sha,
                "declared_parent_agent_id": declared_parent,
                "first_due_directive": adopted.kind,
                "native_finish_notification_supported": False,
                "parent_agent_id": None,
                "prompt_acceptance_count": prompt_acceptance_count,
                "repeated_directive": repeated.kind,
                "result_digest": result_digest,
                "status": "passed",
            }
        finally:
            cleanup = PaseoCliClient(executable=args.executable)
            for record in cleanup.find_by_labels(
                {"gwo.repository": repository_key}
            ):
                if not record.archived:
                    if record.lifecycle in {"running", "queued", "active"}:
                        cleanup.stop(record.agent_id)
                    cleanup.archive(record.agent_id)
                if not cleanup.inspect(record.agent_id).archived:
                    raise AssertionError(
                        f"Paseo Agent retirement did not read back: {record.agent_id}"
                    )

    if result is None or created_agent_id is None:
        raise AssertionError("live Paseo continuation E2E produced no Result")
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
