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
    AuthoritativeNodeReadback,
    AuthoritativeRepositoryReadback,
    EvidenceVerifier,
    ExecutionBudgetReadback,
    InMemoryDeliveryControl,
    InMemoryDurablePlanControl,
    InMemoryRuntimeAdapter,
    Kernel,
    LocalPlanPublication,
    PlanCompiler,
    ResultClaim,
    ReviewAxisBinding,
    ReviewAxisObservation,
    RuntimeBinding,
    RuntimeObservation,
    RuntimePrompt,
    StoreReconstructor,
    TypedEvidence,
    bounded_check_diagnostics,
    check_diagnostics_valid,
    redact_secrets,
    secrets_policy_digest,
)
from gwo_v8._canonical import digest_value  # noqa: E402
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
            "sys.stderr.write('INTERNAL-ABCD1234\\n'); "
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
                # The proposal authorizes no Repair Round at all: the
                # deterministic Compiler must authorize the one bounded
                # Repair Round for local Check recovery in the PlanSpec so
                # the Kernel never overrides policy at runtime.
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
        # Explicit deterministic repository secrets policy: the Compiler
        # freezes it into the PlanSpec with its digest, Runtime capture
        # redacts with it, and Evidence verification enforces it closed.
        "secrets_policy": {
            "version": 1,
            "patterns": [
                r"gh[pousr]_[A-Za-z0-9]{16,}",
                r"INTERNAL-[A-Z0-9]{8}",
                (
                    r"(?i)\b(api[_-]?key|token|secret|password|passwd"
                    r"|authorization)(\s*[:=]\s*|\s+)(\S+)"
                ),
            ],
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

    # The deterministic Compiler authorized the bounded Repair Round and the
    # explicit repository secrets policy inside the PlanSpec; the Kernel acts
    # strictly within that compiled policy.
    assert work_node["recovery_policy"] == {
        "semantic_attempts": 1,
        "repair_rounds": 1,
    }
    compiled_policy = work_node["output_contract"]["secrets_policy"]
    assert compiled_policy["policy_digest"] == secrets_policy_digest(
        compiled_policy
    )
    assert r"INTERNAL-[A-Z0-9]{8}" in compiled_policy["patterns"]

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
    # Every condition entry is attributed to the exact member Candidate and
    # Evidence digest — the identities the Integration Batch manifest uses.
    for entry in failed.values():
        assert entry["candidate_sha"] == condition["candidate_sha"]
        assert len(entry["evidence_digest"]) == 64
    diagnostics = failed["module-1-affected"]["diagnostics"]
    assert check_diagnostics_valid(diagnostics)

    # The Repair Packet carries the bounded, redacted diagnostics so the
    # Repair Round targets the exact failed Check without rerunning anything.
    condition_diagnostics = failed["module-1-affected"]["diagnostics"]
    assert SECRET not in condition_diagnostics["stderr_tail"]
    assert "token=[redacted]" in condition_diagnostics["stderr_tail"]
    assert "INTERNAL-ABCD1234" not in condition_diagnostics["stderr_tail"]
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
    assert causes["module-1-affected"]["candidate_sha"] == (
        condition["candidate_sha"]
    )

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


def _compiled_plan():
    compiled = PlanCompiler().compile(*_inputs())
    plan = json.loads(compiled.canonical_bytes)
    work_node = next(node for node in plan["nodes"] if node["kind"] == "work")
    return compiled, work_node


def test_zero_round_proposal_compiles_bounded_repair_authorization():
    compiled, work_node = _compiled_plan()

    # The PlanSpec itself authorizes the one bounded Repair Round for local
    # Check recovery; nodes without local Checks keep their proposed policy.
    assert work_node["recovery_policy"] == {
        "semantic_attempts": 1,
        "repair_rounds": 1,
    }
    plan = json.loads(compiled.canonical_bytes)
    integration_node = next(
        node for node in plan["nodes"] if node["kind"] == "integration"
    )
    assert integration_node["recovery_policy"] == {
        "semantic_attempts": 1,
        "repair_rounds": 0,
    }
    secrets_policy = work_node["output_contract"]["secrets_policy"]
    assert secrets_policy["policy_digest"] == secrets_policy_digest(
        secrets_policy
    )


def _repairing_node_readback(
    compiled,
    work_node,
    tmp_path,
    *,
    repair_rounds_used: int,
) -> AuthoritativeNodeReadback:
    base_sha = "2" * 40
    prompt = RuntimePrompt(
        text="implement module 1",
        digest=digest_value("implement module 1"),
        authority_digest=work_node["contract_digest"],
    )
    binding = RuntimeBinding(
        adapter="paseo",
        runtime_id="runtime:1",
        repository=compiled.repository,
        plan_digest=compiled.digest,
        node_key=work_node["node_key"],
        admission_id="admission:1",
        repository_path=str(tmp_path),
        workspace=str(tmp_path),
        prompt_accepted=True,
        prompt_digest=prompt.digest,
        attempt_id="attempt:1",
        agent_id="agent:1",
        session_id="session:1",
        workspace_id="workspace:1",
        runtime_profile="standard",
        profile_digest="3" * 64,
        provider="kimi-cli",
        model="kimi-code/kimi-for-coding",
        thinking="on",
        mode="yolo",
        features_digest="4" * 64,
        base_sha=base_sha,
    )
    # The durable shape of a repairing node mirrors the live Store: the
    # rejected Candidate and its Evidence are already invalidated, only the
    # bounded Repair Round budget consumption remains.
    return AuthoritativeNodeReadback(
        node_key=work_node["node_key"],
        goal_key=work_node["goal_key"],
        work_item_key=work_node["work_item_key"],
        status="waiting",
        directive="wait_for_runtime",
        admission_id="admission:1",
        admission_state="consumed",
        attempt_id="attempt:1",
        attempt_state="repairing",
        attempt_record_state="running",
        attempt_terminal_reason=None,
        budgets=ExecutionBudgetReadback(
            attempt_ordinal=1,
            repair_rounds_used=repair_rounds_used,
            materialization_create_executions=1,
            materialization_prompt_executions=1,
            hosted_retry_count=0,
            runtime_observation_failures=0,
            runtime_circuits={},
        ),
        base_sha=base_sha,
        prompt=prompt,
        runtime_binding=binding,
        candidate_sha=None,
        wait_condition="runtime_result",
        wait_source_ref="paseo://attempt/attempt:1/repair",
        publication_state=None,
        publication_ref=None,
        hosted_check_state=None,
        hosted_check_evidence=(),
        worker_parked_for_ci=False,
        resume_sent=True,
        publication_eligible=None,
        evidence=(),
        review_children=(),
        review_observations=(),
        held_resource_claims=(),
        integrated_sha=None,
        candidate_source_ref=None,
        integration_source_ref=None,
    )


def test_store_reconstruction_round_trips_compiler_authorized_repair(tmp_path):
    compiled, work_node = _compiled_plan()
    durable = InMemoryDurablePlanControl()
    publication = LocalPlanPublication(
        tmp_path / "source.sqlite3",
        durable=durable,
    )
    publication.publish_and_activate(
        compiled,
        expected_active_digest=None,
        writer_generation="check-recovery",
    )

    def reconstruct(repair_rounds_used: int):
        readback = AuthoritativeRepositoryReadback.from_durable(
            durable,
            compiled.repository,
            nodes=(
                _repairing_node_readback(
                    compiled,
                    work_node,
                    tmp_path,
                    repair_rounds_used=repair_rounds_used,
                ),
            ),
        )
        return StoreReconstructor().reconstruct(
            readback,
            tmp_path / f"reconstructed-{repair_rounds_used}.sqlite3",
        )

    # repair_rounds_used == 1 stays within the compiler-authorized bounded
    # Repair Round: authoritative Store reconstruction round-trips cleanly.
    result = reconstruct(1)
    assert result.status == "reconstructed", result.blockers
    assert "EXECUTION_BUDGET_CONTRADICTION" not in result.blockers

    # A budget beyond the compiled authorization is still caught closed.
    exceeded = reconstruct(2)
    assert "EXECUTION_BUDGET_CONTRADICTION" in exceeded.blockers


def _provenance_state() -> tuple[dict, dict, TypedEvidence]:
    definition = {
        "check_id": "module-1",
        "suite": "affected",
        "hosted_only": False,
        "definition_digest": "d" * 64,
        "command": ["python", "-c", "raise SystemExit(2)"],
        "environment_requirements": [],
    }
    environment = {"platform": "test"}

    def capture(kind: str, subject: str, source_ref: str, payload: dict):
        return TypedEvidence._capture(
            kind=kind,
            subject=subject,
            observer_type="runtime_adapter",
            observer_id="runtime:unit",
            observed_at="2026-07-26T00:00:00+00:00",
            source_ref=source_ref,
            payload=payload,
        )

    candidate = capture(
        "candidate",
        "c" * 40,
        "runtime://candidate/unit",
        {"tree_sha": "c" * 40},
    )
    genuine = capture(
        "check",
        "c" * 40,
        "runtime://check/genuine",
        {
            "check_id": "module-1",
            "outcome": "failed",
            "exit_code": 2,
            "definition_digest": "d" * 64,
            "command_digest": digest_value(definition["command"]),
            "observed_tree_digest": "c" * 40,
            "environment_requirements": [],
            "environment_identity": environment,
            "environment_digest": digest_value(environment),
            "input_projection_digest": "a" * 64,
            "log_digest": "b" * 64,
            "diagnostics": {"stdout_tail": "", "stderr_tail": "boom"},
        },
    )
    work_node = {"output_contract": {"checks": [definition]}}
    state = {
        "candidate_sha": "c" * 40,
        "candidate_observation": {
            "binding": {"runtime_id": "runtime:unit"},
            "evidence": [candidate.__dict__, genuine.__dict__],
        },
    }
    return state, work_node, genuine


def test_repair_causes_require_fully_validated_check_evidence():
    state, work_node, genuine = _provenance_state()
    observation = state["candidate_observation"]

    def forged(payload_updates=None, *, envelope=None, subject=None):
        evidence = dict(genuine.__dict__)
        payload = dict(genuine.payload)
        payload.update(payload_updates or {})
        evidence["payload"] = payload
        if subject is not None:
            evidence["subject"] = subject
        if envelope:
            evidence.update(envelope)
        return evidence

    observation["evidence"].extend(
        [
            # Wrong command digest: shaped like a failure, but not bound to
            # the compiled Check Definition.
            forged({"command_digest": "0" * 64}),
            # Wrong subject: not bound to the exact Candidate.
            forged(subject="e" * 40),
            # Tampered envelope: payload edited after capture.
            forged({"exit_code": 137}, envelope={"content_digest": "f" * 64}),
            # Raw secret inside shaped diagnostics.
            forged(
                {
                    "diagnostics": {
                        "stdout_tail": "",
                        "stderr_tail": f"token={SECRET}",
                    }
                }
            ),
            # Missing provenance digests entirely.
            forged(
                {
                    "environment_digest": None,
                    "input_projection_digest": None,
                    "log_digest": None,
                }
            ),
        ]
    )

    failures = Kernel._local_check_failures(state, work_node)
    causes = Kernel._repair_causes(
        state,
        work_node,
        cause_type="candidate_verification_failure",
        findings=("local check:module-1 did not pass",),
    )

    assert [failure["check_id"] for failure in failures] == ["module-1"]
    assert failures[0]["evidence_digest"] == genuine.content_digest
    check_causes = [
        cause for cause in causes if cause["type"] == "local_check_failure"
    ]
    assert len(check_causes) == 1
    assert check_causes[0]["source_ref"] == "runtime://check/genuine"
    assert check_causes[0]["candidate_sha"] == "c" * 40
    assert check_causes[0]["evidence_digest"] == genuine.content_digest


def _verification_setup():
    compiled, work_node = _compiled_plan()
    contract = work_node["output_contract"]
    check = next(
        item
        for item in contract["checks"]
        if item["check_id"] == "module-1-affected"
    )
    binding = RuntimeBinding(
        adapter="in-memory",
        runtime_id="runtime:unit",
        repository=compiled.repository,
        plan_digest=compiled.digest,
        node_key=work_node["node_key"],
        admission_id="admission:unit",
        repository_path="unused",
        workspace="unused",
        prompt_accepted=True,
        prompt_digest="5" * 64,
        attempt_id="attempt:1",
        agent_id="agent:unit",
        session_id="session:unit",
        workspace_id="workspace:unit",
        runtime_profile="standard",
        profile_digest="6" * 64,
        provider="in-memory",
        model="deterministic",
        thinking=None,
        mode=None,
        features_digest="7" * 64,
        base_sha="8" * 40,
    )
    claim = ResultClaim(
        attempt_id="attempt:1",
        node_key=work_node["node_key"],
        candidate_sha="c" * 40,
    )
    candidate = TypedEvidence._capture(
        kind="candidate",
        subject="c" * 40,
        observer_type="runtime_adapter",
        observer_id="runtime:unit",
        observed_at="2026-07-26T00:00:00+00:00",
        source_ref="runtime://candidate/unit",
        payload={"tree_sha": "c" * 40},
    )
    environment = {
        "platform": "test",
        "python": {"executable": "python", "version": "3"},
    }
    base_payload = {
        "check_id": "module-1-affected",
        "outcome": "passed",
        "exit_code": 0,
        "definition_digest": check["definition_digest"],
        "command_digest": digest_value(check["command"]),
        "observed_tree_digest": "c" * 40,
        "environment_requirements": ["python"],
        "environment_identity": environment,
        "environment_digest": digest_value(environment),
        "input_projection_digest": "a" * 64,
        "log_digest": "b" * 64,
    }

    def verify_with(payload_updates, *, remove=()):
        payload = {**base_payload, **payload_updates}
        for key in remove:
            payload.pop(key, None)
        evidence = TypedEvidence._capture(
            kind="check",
            subject="c" * 40,
            observer_type="runtime_adapter",
            observer_id="runtime:unit",
            observed_at="2026-07-26T00:00:00+00:00",
            source_ref="runtime://check/unit",
            payload=payload,
        )
        observation = RuntimeObservation(
            binding=binding,
            lifecycle="completed",
            result_claim=claim,
            evidence=(candidate, evidence),
        )
        return EvidenceVerifier().verify(claim, contract, observation)

    return verify_with, contract["secrets_policy"]


def _repair_causes_for_failed_payload(payload_updates=None, *, remove=()):
    state, work_node, genuine = _provenance_state()
    payload = {**genuine.payload, **(payload_updates or {})}
    for key in remove:
        payload.pop(key, None)
    failed_check = TypedEvidence._capture(
        kind=genuine.kind,
        subject=genuine.subject,
        observer_type=genuine.observer_type,
        observer_id=genuine.observer_id,
        observed_at=genuine.observed_at,
        source_ref=genuine.source_ref,
        payload=payload,
    )
    state["candidate_observation"]["evidence"] = [
        state["candidate_observation"]["evidence"][0],
        failed_check.__dict__,
    ]
    return Kernel._repair_causes(
        state,
        work_node,
        cause_type="candidate_verification_failure",
        findings=("local check:module-1 did not pass",),
    )


def test_failed_check_without_diagnostics_is_not_recoverable():
    verify_with, _secrets_policy = _verification_setup()

    decision = verify_with({"outcome": "failed", "exit_code": 2})
    causes = _repair_causes_for_failed_payload(remove=("diagnostics",))

    assert decision.status != "accepted"
    assert any(
        "failed diagnostics are required" in finding
        for finding in decision.findings
    )
    assert not any(cause["type"] == "local_check_failure" for cause in causes)


@pytest.mark.parametrize(
    ("exit_code", "remove_exit_code"),
    (
        pytest.param(None, True, id="missing"),
        pytest.param(0, False, id="zero"),
        pytest.param(True, False, id="boolean"),
        pytest.param("2", False, id="numeric-string"),
        pytest.param("not-an-integer", False, id="nonnumeric-string"),
    ),
)
def test_failed_check_requires_non_boolean_nonzero_integer_exit_code(
    exit_code,
    remove_exit_code,
):
    verify_with, secrets_policy = _verification_setup()
    payload_updates = {
        "outcome": "failed",
        "exit_code": exit_code,
        "diagnostics": {"stdout_tail": "", "stderr_tail": "boom"},
        "secrets_policy_digest": secrets_policy["policy_digest"],
    }
    remove = ("exit_code",) if remove_exit_code else ()

    decision = verify_with(payload_updates, remove=remove)
    causes = _repair_causes_for_failed_payload(
        {"exit_code": exit_code},
        remove=remove,
    )

    assert decision.status != "accepted"
    assert any(
        "failed exit code must be a nonzero integer" in finding
        for finding in decision.findings
    )
    assert not any(cause["type"] == "local_check_failure" for cause in causes)


@pytest.mark.parametrize(
    ("exit_code", "remove_exit_code"),
    (
        pytest.param(2, False, id="nonzero"),
        pytest.param(None, True, id="missing"),
        pytest.param(False, False, id="boolean"),
        pytest.param("0", False, id="numeric-string"),
        pytest.param("not-an-integer", False, id="nonnumeric-string"),
    ),
)
def test_passed_check_requires_non_boolean_integer_zero_exit_code(
    exit_code,
    remove_exit_code,
):
    verify_with, _secrets_policy = _verification_setup()
    remove = ("exit_code",) if remove_exit_code else ()

    decision = verify_with({"exit_code": exit_code}, remove=remove)

    assert decision.status != "accepted"
    assert decision.result is None
    assert any(
        "passed exit code must be the integer zero" in finding
        for finding in decision.findings
    )


def test_verifier_fails_closed_on_secrets_policy_mismatch():
    verify_with, secrets_policy = _verification_setup()

    decision = verify_with(
        {
            "diagnostics": {"stdout_tail": "", "stderr_tail": "clean"},
            "secrets_policy_digest": "0" * 64,
        }
    )

    # The mismatched Evidence is excluded closed: it can never ground an
    # acceptance, and the typed finding names the policy violation.
    assert decision.status != "accepted"
    assert any(
        "secrets policy mismatch" in finding for finding in decision.findings
    )


def test_verifier_fails_closed_on_still_secret_excerpt():
    verify_with, secrets_policy = _verification_setup()

    decision = verify_with(
        {
            "diagnostics": {
                "stdout_tail": "",
                "stderr_tail": f"token={SECRET}",
            },
            "secrets_policy_digest": secrets_policy["policy_digest"],
        }
    )

    assert decision.status != "accepted"
    assert any(
        "diagnostics leak secrets" in finding for finding in decision.findings
    )

    clean = verify_with(
        {
            "diagnostics": {
                "stdout_tail": "",
                "stderr_tail": "token=[redacted]",
            },
            "secrets_policy_digest": secrets_policy["policy_digest"],
        }
    )
    assert not any(
        "secrets policy" in finding or "leak secrets" in finding
        for finding in clean.findings
    )


def _batch_inputs() -> tuple[dict, dict, dict]:
    nodes = []
    work_items = []
    definitions = []
    for ordinal in (1, 2):
        path = f"module-{ordinal}.txt"
        content = f"module {ordinal}\n"
        work_item_key = f"issue:{780 + ordinal}"
        affected_command = [
            "python",
            "-c",
            f"from pathlib import Path; assert Path('{path}').is_file()",
        ]
        repository_command = [
            "python",
            "-c",
            (
                "from pathlib import Path; "
                f"assert Path('{path}').read_text() == {content!r}"
            ),
        ]
        work_items.append(
            {
                "work_item_key": work_item_key,
                "tracker_state": "ready-for-agent",
                "source_ref": f"synthetic://issue/{780 + ordinal}",
                "title": f"Build module {ordinal}",
                "outcome_contract": {"path": path, "content": content},
            }
        )
        nodes.append(
            {
                "goal_key": "goal:check-recovery",
                "work_item_key": work_item_key,
                "kind": "work",
                "inputs": {"file_changes": [{"path": path, "content": content}]},
                "output_contract": {
                    "required_evidence": [
                        {"kind": "candidate"},
                        {
                            "kind": "check",
                            "check_id": f"module-{ordinal}-affected",
                        },
                    ],
                    "checks": [
                        {
                            "check_id": f"module-{ordinal}-affected",
                            "command": affected_command,
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
                "risk": "low",
                "recovery_policy": {
                    "semantic_attempts": 1,
                    "repair_rounds": 0,
                },
                "skill_reference": None,
            }
        )
        definitions.extend(
            [
                {
                    "check_id": f"module-{ordinal}-affected",
                    "version": 1,
                    "command": affected_command,
                    "hosted_name": None,
                    "environment_requirements": ["python"],
                    "input_selector": [path],
                    "base_sensitive": False,
                    "risk": "low",
                    "hosted_only": False,
                    "suite": "affected",
                },
                {
                    "check_id": f"module-{ordinal}-repository",
                    "version": 1,
                    "command": repository_command,
                    "hosted_name": None,
                    "environment_requirements": ["python"],
                    "input_selector": [path],
                    "base_sensitive": False,
                    "risk": "low",
                    "hosted_only": False,
                    "suite": "repository",
                },
                {
                    "check_id": f"module-{ordinal}-hosted",
                    "version": 1,
                    "command": ["python", "-c", "raise SystemExit(0)"],
                    "hosted_name": f"Module {ordinal} CI",
                    "environment_requirements": [],
                    "input_selector": [path],
                    "base_sensitive": False,
                    "risk": "low",
                    "hosted_only": True,
                    "suite": "hosted",
                },
            ]
        )
    intent = {
        "parent_plan_digest": None,
        "goals": [
            {
                "goal_key": "goal:check-recovery",
                "objective": "Integrate two independent modules.",
                "acceptance": ["Both modules are integrated."],
            }
        ],
        "nodes": nodes,
        "edges": [],
    }
    source = {"repository": REPOSITORY, "work_items": work_items}
    policy = {
        "version": 3,
        "low_risk_allowlist": ["module-*.txt"],
        "check_definitions": definitions,
        "strict_review": {
            "specialist_requirements": [],
            "human_decision_required": True,
        },
    }
    return intent, source, policy


class _BatchRepairRuntime(_ReviewingInMemoryRuntime):
    """Two-member Batch where only one member's repository Check is red."""

    def __init__(self, workspace_root: Path, *, broken_node_key: str):
        super().__init__(workspace_root)
        self.broken_node_key = broken_node_key
        self.repaired_admissions: list[str] = []
        self.repair_prompts: list[tuple[str, object]] = []

    def repair(self, binding, prompt, *, action_key) -> None:
        self.repaired_admissions.append(binding.admission_id)
        self.repair_prompts.append((binding.node_key, prompt))
        super().repair(binding, prompt, action_key=action_key)

    def resume(self, binding) -> None:
        state = self._state_for(binding)
        if (
            state.result_claim is None
            and state.node is not None
            and state.node.get("node_key") == self.broken_node_key
        ):
            state.node["inputs"]["file_changes"][0]["content"] = (
                "module 1\n"
                if binding.admission_id in self.repaired_admissions
                else "broken\n"
            )
        super().resume(binding)


def test_batch_repository_check_failure_targets_only_implicated_member(
    tmp_path,
):
    repository = _temporary_repository(tmp_path)
    intent, source, policy = _batch_inputs()
    compiled = PlanCompiler().compile(intent, source, policy)
    plan = json.loads(compiled.canonical_bytes)
    work_nodes = {
        node["inputs"]["file_changes"][0]["path"]: node
        for node in plan["nodes"]
        if node["kind"] == "work"
    }
    assert set(work_nodes) == {"module-1.txt", "module-2.txt"}
    member_one = work_nodes["module-1.txt"]
    member_two = work_nodes["module-2.txt"]
    assert member_one["recovery_policy"]["repair_rounds"] == 1
    assert member_two["recovery_policy"]["repair_rounds"] == 1
    store_path = tmp_path / "check-recovery-batch.sqlite3"
    publication = LocalPlanPublication(store_path)
    publication.publish_and_activate(
        compiled,
        expected_active_digest=None,
        writer_generation="check-recovery",
    )
    runtime = _BatchRepairRuntime(
        tmp_path / "runtime",
        broken_node_key=member_one["node_key"],
    )
    kernel = Kernel(
        store_path=store_path,
        publication=publication,
        runtime=runtime,
        verifier=EvidenceVerifier(),
        repository_path=repository,
        integration_branch="main",
        writer_generation="check-recovery",
        delivery_control=InMemoryDeliveryControl(
            hosted_outcomes=("passed", "passed"),
        ),
        runtime_config=_runtime_config(),
    )

    kernel.reconcile_once(REPOSITORY)
    kernel.reconcile_once(REPOSITORY)

    member_one_state = kernel._read_state(
        REPOSITORY,
        compiled.digest,
        member_one["node_key"],
    )
    member_two_state = kernel._read_state(
        REPOSITORY,
        compiled.digest,
        member_two["node_key"],
    )
    assert member_one_state is not None
    assert member_two_state is not None

    # Only the implicated member carries a typed condition, and it names
    # exactly its own failed repository Check bound to its own Candidate.
    condition = member_one_state["recoverable_condition"]
    assert condition is not None
    assert condition["type"] == "local_check_failure"
    assert {
        check["check_id"] for check in condition["checks"]
    } == {"module-1-repository"}
    entry = condition["checks"][0]
    assert entry["suite"] == "repository"
    assert entry["candidate_sha"] == condition["candidate_sha"]
    assert len(entry["evidence_digest"]) == 64
    assert member_one_state["attempt_state"] == "repairing"
    assert member_one_state["repair_rounds_used"] == 1

    # The unrelated member was never recovered: no condition, no Repair
    # Round, no repair Prompt, and its parked Worker keeps waiting.
    assert member_two_state["recoverable_condition"] is None
    assert member_two_state["repair_rounds_used"] == 0
    assert member_two_state["attempt_state"] != "repairing"
    assert all(
        node_key != member_two["node_key"]
        for node_key, _prompt in runtime.repair_prompts
    )
    packet = json.loads(runtime.repair_prompts[0][1].text)["repair_round"]
    assert packet["candidate_sha"] == condition["candidate_sha"]
    assert {
        cause.get("check_id")
        for cause in packet["causes"]
        if cause["type"] == "local_check_failure"
    } == {"module-1-repository"}

    for _ in range(6):
        kernel.reconcile_once(REPOSITORY)

    member_one_final = kernel._read_state(
        REPOSITORY,
        compiled.digest,
        member_one["node_key"],
    )
    member_two_final = kernel._read_state(
        REPOSITORY,
        compiled.digest,
        member_two["node_key"],
    )
    assert member_one_final["status"] == "complete"
    assert member_one_final["repair_rounds_used"] == 1
    assert member_two_final["status"] == "complete"
    # The unrelated member integrated without ever entering recovery.
    assert member_two_final["repair_rounds_used"] == 0
    assert len(runtime.repaired_admissions) == 1
