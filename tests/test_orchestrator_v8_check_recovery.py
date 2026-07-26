"""Issue #78: local Check failures are recoverable and diagnostically readable.

A non-zero exit of a Candidate affected Check or a Batch repository Check is
recorded as a typed recoverable condition with bounded, secret-redacted
stdout/stderr excerpts; the same Attempt receives the one bounded Repair
Round before the Recovery Ladder may exhaust into a Failed Plan Node.
"""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import pytest


SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "orchestrator" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from gwo_v8 import (  # noqa: E402
    EvidenceVerifier,
    InMemoryDeliveryControl,
    InMemoryRuntimeAdapter,
    Kernel,
    LocalPlanPublication,
    PlanCompiler,
    ReviewAxisBinding,
    ReviewAxisObservation,
    bounded_check_diagnostics,
    check_diagnostics_valid,
    redact_secrets,
)
from gwo_v8.evidence import (  # noqa: E402
    CHECK_DIAGNOSTIC_MAX_STREAM_CHARACTERS,
    _CHECK_DIAGNOSTIC_TRUNCATION_PREFIX,
)


REPOSITORY = "local/check-recovery"
SECRET = "ghp_" + "a" * 32


def test_redact_secrets_masks_common_credentials():
    text = "\n".join(
        [
            f"token={SECRET}",
            "Authorization: Bearer abcdef1234567890",
            "aws key AKIAIOSFODNN7EXAMPLE",
            "password: hunter2",
            "github_pat_" + "b" * 40,
            "ordinary output stays",
        ]
    )

    redacted = redact_secrets(text)

    for leaked in (
        SECRET,
        "abcdef1234567890",
        "AKIAIOSFODNN7EXAMPLE",
        "hunter2",
        "github_pat_",
    ):
        assert leaked not in redacted
    assert "[redacted]" in redacted
    assert "ordinary output stays" in redacted


def test_bounded_check_diagnostics_truncates_and_marks_long_output():
    long_stdout = "y" * (CHECK_DIAGNOSTIC_MAX_STREAM_CHARACTERS + 100)

    diagnostics = bounded_check_diagnostics(long_stdout, "short stderr")

    assert diagnostics["stdout_tail"].startswith(
        _CHECK_DIAGNOSTIC_TRUNCATION_PREFIX
    )
    assert len(diagnostics["stdout_tail"]) <= (
        CHECK_DIAGNOSTIC_MAX_STREAM_CHARACTERS
        + len(_CHECK_DIAGNOSTIC_TRUNCATION_PREFIX)
    )
    assert diagnostics["stdout_tail"].endswith("y" * 100)
    assert diagnostics["stderr_tail"] == "short stderr"
    assert check_diagnostics_valid(diagnostics)


def test_check_diagnostics_validation_rejects_invalid_shapes():
    assert not check_diagnostics_valid(None)
    assert not check_diagnostics_valid("stdout")
    assert not check_diagnostics_valid({"stdout_tail": "x"})
    assert not check_diagnostics_valid({"stdout_tail": "x", "stderr_tail": 1})
    assert not check_diagnostics_valid(
        {
            "stdout_tail": "x" * (CHECK_DIAGNOSTIC_MAX_STREAM_CHARACTERS + 100),
            "stderr_tail": "",
        }
    )
    assert not check_diagnostics_valid(
        {"stdout_tail": "", "stderr_tail": "", "extra": ""}
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


def _temporary_repository(tmp_path: Path) -> Path:
    repository = tmp_path / "repository"
    repository.mkdir()
    subprocess.run(
        ["git", "init", "-b", "main", str(repository)],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    _git(repository, "config", "user.name", "Check Recovery")
    _git(repository, "config", "user.email", "check-recovery@example.invalid")
    (repository / "README.md").write_text("base\n", encoding="utf-8")
    _git(repository, "add", "README.md")
    _git(repository, "commit", "-m", "base")
    return repository


def _noisy_affected_command() -> list:
    return [
        "python",
        "-c",
        (
            "import sys; "
            "from pathlib import Path; "
            "sys.stderr.write('x' * 3000 + '\\n'); "
            f"sys.stderr.write('token={SECRET}\\n'); "
            "assert Path('module-1.txt').read_text() == 'module 1\\n'"
        ),
    ]


def _quiet_repository_command() -> list:
    return [
        "python",
        "-c",
        (
            "from pathlib import Path; "
            "assert Path('module-1.txt').read_text() == 'module 1\\n'"
        ),
    ]


def _inputs() -> tuple[dict, dict, dict]:
    path = "module-1.txt"
    work_item_key = "issue:78"
    intent = {
        "parent_plan_digest": None,
        "goals": [
            {
                "goal_key": "goal:check-recovery",
                "objective": "Recover one red local Check and integrate.",
                "acceptance": ["module-1.txt contains module 1"],
            }
        ],
        "nodes": [
            {
                "goal_key": "goal:check-recovery",
                "work_item_key": work_item_key,
                "kind": "work",
                "inputs": {
                    "file_changes": [{"path": path, "content": "module 1\n"}]
                },
                "output_contract": {
                    "required_evidence": [
                        {"kind": "candidate"},
                        {"kind": "check", "check_id": "module-1-affected"},
                    ],
                    "checks": [
                        {
                            "check_id": "module-1-affected",
                            "command": _noisy_affected_command(),
                        }
                    ],
                },
                "effect_contract": {
                    "write_scopes": [path],
                    "external_effects": [],
                },
                "resource_claims": [],
                "runtime_requirements": {"capabilities": ["git", "local_check"]},
                "difficulty": "standard",
                "risk": "standard",
                # The compiled policy authorizes no Repair Round at all: the
                # typed recoverable Check condition must still receive the one
                # bounded Repair Round instead of failing the Plan Node.
                "recovery_policy": {
                    "semantic_attempts": 1,
                    "repair_rounds": 0,
                },
                "skill_reference": None,
            }
        ],
        "edges": [],
    }
    source = {
        "repository": REPOSITORY,
        "work_items": [
            {
                "work_item_key": work_item_key,
                "tracker_state": "ready-for-agent",
                "source_ref": "synthetic://issue/78",
                "title": "Recover one red local Check",
                "outcome_contract": {"path": path, "content": "module 1\n"},
            }
        ],
    }
    policy = {
        "version": 3,
        "low_risk_allowlist": ["module-*.txt"],
        "check_definitions": [
            {
                "check_id": "module-1-affected",
                "version": 1,
                "command": _noisy_affected_command(),
                "hosted_name": None,
                "environment_requirements": ["python"],
                "input_selector": [path],
                "base_sensitive": False,
                "risk": "low",
                "hosted_only": False,
                "suite": "affected",
            },
            {
                "check_id": "module-1-repository",
                "version": 1,
                "command": _quiet_repository_command(),
                "hosted_name": None,
                "environment_requirements": ["python"],
                "input_selector": [path],
                "base_sensitive": False,
                "risk": "low",
                "hosted_only": False,
                "suite": "repository",
            },
            {
                "check_id": "module-1-hosted",
                "version": 1,
                "command": ["python", "-c", "raise SystemExit(0)"],
                "hosted_name": "Module 1 CI",
                "environment_requirements": [],
                "input_selector": [path],
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


def _runtime_config() -> dict:
    worker_binding = {
        "provider": "kimi-cli",
        "settings": {
            "model": "kimi-code/kimi-for-coding",
            "thinkingOptionId": "on",
            "modeId": "yolo",
            "features": {},
        },
    }
    return {
        "active_turn_pools": {"workers": 1, "coordinators": 1},
        "tiers": {
            "light": worker_binding,
            "standard": worker_binding,
            "heavy": worker_binding,
            "frontier": {
                "provider": "codex",
                "settings": {
                    "model": "gpt-5.6-sol",
                    "thinkingOptionId": "xhigh",
                    "modeId": "full-access",
                    "features": {},
                },
            },
        },
        "runtime_profiles": {
            "reviewer_standard": {
                "provider": "codex",
                "settings": {
                    "model": "gpt-5.6-sol",
                    "thinkingOptionId": "high",
                    "modeId": "full-access",
                    "features": {},
                },
            }
        },
        "review_profiles": {"standard_axis": "reviewer_standard"},
        "repositories": {
            REPOSITORY: {
                "active_turn_pools": {"workers": 1, "coordinators": 1}
            }
        },
    }


class _ReviewingInMemoryRuntime(InMemoryRuntimeAdapter):
    def materialize_review_axis(self, request, profile, *, parent_agent_id):
        prompt = request.to_prompt()
        suffix = request.action_key[-12:]
        return ReviewAxisBinding(
            action_key=request.action_key,
            axis=request.axis,
            candidate_sha=request.candidate_sha,
            fixed_input_digest=request.fixed_input_digest,
            runtime_id=f"review:{suffix}",
            agent_id=f"review:{suffix}",
            session_id=f"session:{suffix}",
            workspace_id=f"workspace:{suffix}",
            workspace=str(request.workspace),
            parent_agent_id=parent_agent_id,
            runtime_profile=profile.name,
            profile_digest=profile.digest,
            provider=profile.provider,
            model=profile.model,
            thinking=profile.thinking,
            mode=profile.mode,
            prompt_digest=prompt.digest,
        )

    def observe_review_axis(self, request, binding):
        return ReviewAxisObservation(
            lifecycle="completed",
            axis=request.axis,
            attempt_id=request.attempt_id,
            candidate_sha=request.candidate_sha,
            base_sha=request.base_sha,
            recovery_ordinal=request.recovery_ordinal,
            spec_digest=request.spec_digest,
            check_manifest_digest=request.check_manifest_digest,
            fixed_input_digest=request.fixed_input_digest,
            action_key=request.action_key,
            runtime_id=binding.runtime_id,
            agent_id=binding.agent_id,
            session_id=binding.session_id,
            profile_digest=binding.profile_digest,
            provider=binding.provider,
            model=binding.model,
            thinking=binding.thinking,
            mode=binding.mode,
            output_digest=request.fixed_input_digest,
            findings=(),
        )

    def retire_review_after_evidence(self, _binding, authorization):
        from gwo_v8.retirement import review_retirement_readback

        return review_retirement_readback(
            authorization=authorization,
            workspace_disposition="shared_preserved",
            agent_archived=True,
            directory_absent=False,
            worktree_absent=False,
            branch_deleted=False,
        )


class _LocalCheckRepairRuntime(_ReviewingInMemoryRuntime):
    """In-memory Worker whose first Candidate is red and whose Repair Round
    optionally produces the fixed Candidate."""

    def __init__(self, workspace_root: Path, *, fixed_content: str | None):
        super().__init__(workspace_root)
        self.fixed_content = fixed_content
        self.materialization_count = 0
        self.repair_count = 0
        self.repair_prompts = []

    def materialize(self, admission, prompt=None):
        self.materialization_count += 1
        return super().materialize(admission, prompt)

    def resume(self, binding) -> None:
        state = self._state_for(binding)
        if state.result_claim is None and state.node is not None:
            content = (
                "broken\n" if self.repair_count == 0 else self.fixed_content
            )
            if content is not None:
                state.node["inputs"]["file_changes"][0]["content"] = content
        super().resume(binding)

    def repair(self, binding, prompt, *, action_key) -> None:
        self.repair_count += 1
        self.repair_prompts.append(prompt)
        super().repair(binding, prompt, action_key=action_key)


def _kernel(
    tmp_path,
    runtime,
    *,
    hosted_outcomes=("passed",),
):
    repository = _temporary_repository(tmp_path)
    intent, source, policy = _inputs()
    compiled = PlanCompiler().compile(intent, source, policy)
    store_path = tmp_path / "check-recovery.sqlite3"
    publication = LocalPlanPublication(store_path)
    publication.publish_and_activate(
        compiled,
        expected_active_digest=None,
        writer_generation="check-recovery",
    )
    kernel = Kernel(
        store_path=store_path,
        publication=publication,
        runtime=runtime,
        verifier=EvidenceVerifier(),
        repository_path=repository,
        integration_branch="main",
        writer_generation="check-recovery",
        delivery_control=InMemoryDeliveryControl(hosted_outcomes=hosted_outcomes),
        runtime_config=_runtime_config(),
    )
    work_node = next(
        node
        for node in json.loads(compiled.canonical_bytes)["nodes"]
        if node["kind"] == "work"
    )
    return kernel, compiled, work_node


def _local_check_causes(prompt) -> dict:
    packet = json.loads(prompt.text)["repair_round"]
    return {
        cause["check_id"]: cause
        for cause in packet["causes"]
        if cause["type"] == "local_check_failure"
    }


def test_failed_check_records_typed_condition_and_repairs_red_to_green(
    tmp_path,
):
    runtime = _LocalCheckRepairRuntime(
        tmp_path / "runtime",
        fixed_content="module 1\n",
    )
    kernel, compiled, work_node = _kernel(tmp_path, runtime)

    repair = kernel.reconcile_once(REPOSITORY)

    # The red Checks became a typed recoverable condition with one bounded
    # Repair Round on the same Attempt; the Plan Node was not terminated and
    # nothing was published or sent to hosted CI during the local repair loop.
    assert repair.attempt_state == "repairing"
    assert repair.attempt_ordinal == 1
    assert repair.repair_rounds_used == 1
    assert runtime.materialization_count == 1
    assert runtime.repair_count == 1
    assert kernel.delivery_control.publication_count == 0
    state = kernel._read_state(REPOSITORY, compiled.digest, work_node["node_key"])
    condition = state["recoverable_condition"]
    assert condition["type"] == "local_check_failure"
    assert condition["attempt_id"] == repair.attempt_id
    failed = {check["check_id"]: check for check in condition["checks"]}
    assert set(failed) == {"module-1-affected", "module-1-repository"}
    assert failed["module-1-affected"]["suite"] == "affected"
    assert failed["module-1-repository"]["suite"] == "repository"
    assert failed["module-1-affected"]["exit_code"] != 0
    diagnostics = failed["module-1-affected"]["diagnostics"]
    assert check_diagnostics_valid(diagnostics)

    # The Repair Packet carries the bounded, redacted diagnostics so the
    # Repair Round targets the exact failed Check without rerunning anything.
    condition_diagnostics = failed["module-1-affected"]["diagnostics"]
    assert SECRET not in condition_diagnostics["stderr_tail"]
    assert "token=[redacted]" in condition_diagnostics["stderr_tail"]
    assert "AssertionError" in condition_diagnostics["stderr_tail"]
    assert condition_diagnostics["stderr_tail"].startswith(
        _CHECK_DIAGNOSTIC_TRUNCATION_PREFIX
    )
    causes = _local_check_causes(runtime.repair_prompts[0])
    assert set(causes) == {"module-1-affected", "module-1-repository"}
    stderr_tail = causes["module-1-affected"]["stderr_tail"]
    assert SECRET not in stderr_tail
    assert "token=[redacted]" in stderr_tail
    assert causes["module-1-affected"]["suite"] == "affected"
    assert causes["module-1-repository"]["suite"] == "repository"

    integrated = kernel.reconcile_once(REPOSITORY)

    # The repaired Candidate converged locally first (Review + repository
    # Check), then crossed publication, hosted CI, and Integration.
    assert integrated.status == "complete"
    assert integrated.attempt_id == repair.attempt_id
    assert integrated.attempt_ordinal == 1
    assert runtime.materialization_count == 1
    assert runtime.repair_count == 1
    final_state = kernel._read_state(
        REPOSITORY,
        compiled.digest,
        work_node["node_key"],
    )
    assert final_state["recoverable_condition"] is None


def test_failed_check_enters_terminal_only_after_recovery_is_exhausted(
    tmp_path,
):
    runtime = _LocalCheckRepairRuntime(
        tmp_path / "runtime",
        fixed_content="still broken\n",
    )
    kernel, compiled, work_node = _kernel(tmp_path, runtime)

    repair = kernel.reconcile_once(REPOSITORY)

    assert repair.attempt_state == "repairing"
    assert repair.repair_rounds_used == 1
    assert runtime.repair_count == 1

    failed = kernel.reconcile_once(REPOSITORY)

    # The Repair Round is used and the compiled Ladder allows no frontier
    # Attempt: only now may the Plan Node enter a terminal outcome.
    assert failed.status == "failed"
    assert failed.attempt_state == "terminal"
    assert failed.attempt_ordinal == 1
    assert failed.repair_rounds_used == 1
    assert runtime.materialization_count == 1
    assert runtime.repair_count == 1
    state = kernel._read_state(REPOSITORY, compiled.digest, work_node["node_key"])
    assert state["work_item_state"] == "failed"
    condition = state["recoverable_condition"]
    assert condition["type"] == "local_check_failure"
    assert {
        check["check_id"] for check in condition["checks"]
    } == {"module-1-affected", "module-1-repository"}


def test_failed_check_repair_round_is_durable_across_restart(tmp_path):
    runtime = _LocalCheckRepairRuntime(
        tmp_path / "runtime",
        fixed_content="module 1\n",
    )
    kernel, compiled, work_node = _kernel(tmp_path, runtime)

    repair = kernel.reconcile_once(REPOSITORY)

    assert repair.attempt_state == "repairing"
    original_state = kernel._read_state(
        REPOSITORY,
        compiled.digest,
        work_node["node_key"],
    )
    payload_digest = original_state["repair_prompt"]["payload_digest"]
    condition = original_state["recoverable_condition"]

    restarted_kernel = Kernel(
        store_path=kernel.store_path,
        publication=kernel.publication,
        runtime=runtime,
        verifier=EvidenceVerifier(),
        repository_path=kernel.repository_path,
        integration_branch="main",
        writer_generation="check-recovery",
        delivery_control=kernel.delivery_control,
        runtime_config=kernel.runtime_config,
    )
    restarted_kernel.plan_reconciliation(REPOSITORY)
    restarted_state = restarted_kernel._read_state(
        REPOSITORY,
        compiled.digest,
        work_node["node_key"],
    )

    # Restart readback sees the same typed condition and Repair Packet; no
    # second Repair Round is dispatched.
    assert restarted_state["repair_prompt"]["payload_digest"] == payload_digest
    assert restarted_state["recoverable_condition"] == condition
    assert runtime.repair_count == 1
    assert runtime.materialization_count == 1
