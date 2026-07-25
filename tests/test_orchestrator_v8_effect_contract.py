"""#67: the Plan Node Effect Contract is enforced against the actual Candidate diff.

Before any Review materialization the Kernel consumes one typed decision from
the deep effect-contract verification module. The module computes authoritative
changed paths from the integration base SHA to the exact Candidate SHA with
Git identity, never from Worker self-report or the current workspace status.
"""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

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
)
from gwo_v8.effect_verification import (  # noqa: E402
    EffectContractDecision,
    EffectContractVerification,
    EffectContractVerifier,
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


def _temporary_repository(tmp_path: Path, extra_files: dict | None = None) -> Path:
    repository = tmp_path / "repository"
    repository.mkdir()
    subprocess.run(
        ["git", "init", "-b", "main", str(repository)],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    _git(repository, "config", "user.name", "Issue Sixty Seven")
    _git(repository, "config", "user.email", "issue-67@example.invalid")
    (repository / "README.md").write_text("base\n", encoding="utf-8")
    for relative, content in (extra_files or {}).items():
        target = repository / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    _git(repository, "add", "--all")
    _git(repository, "commit", "-m", "base")
    return repository


def _intent(scopes: list[str]) -> tuple[dict, dict]:
    path = "module-1.txt"
    content = "module 1\n"
    work_item_key = "issue:167"
    work_item = {
        "work_item_key": work_item_key,
        "tracker_state": "ready-for-agent",
        "source_ref": "synthetic://issue/167",
        "title": "Build module 1 inside the authorized Write Scope",
        "outcome_contract": {"path": path, "content": content},
    }
    node = {
        "goal_key": "goal:issue-67",
        "work_item_key": work_item_key,
        "kind": "work",
        "inputs": {
            "file_changes": [{"path": path, "content": content}],
        },
        "output_contract": {
            "required_evidence": [
                {"kind": "candidate"},
                {"kind": "check", "check_id": "module-1"},
            ],
            "checks": [
                {
                    "check_id": "module-1",
                    "command": [
                        "python",
                        "-c",
                        (
                            "from pathlib import Path; "
                            "assert Path('module-1.txt').read_text() == 'module 1\\n'"
                        ),
                    ],
                }
            ],
        },
        "effect_contract": {
            "write_scopes": scopes,
            "external_effects": [],
        },
        "resource_claims": [],
        "runtime_requirements": {
            "capabilities": ["git", "local_check"],
        },
        "difficulty": "standard",
        "risk": "standard",
        "recovery_policy": {
            "semantic_attempts": 2,
            "repair_rounds": 1,
        },
        "skill_reference": None,
    }
    intent = {
        "parent_plan_digest": None,
        "goals": [
            {
                "goal_key": "goal:issue-67",
                "objective": "Enforce the Effect Contract against the Candidate diff.",
                "acceptance": ["Only authorized paths change."],
            }
        ],
        "nodes": [node],
        "edges": [],
    }
    source = {
        "repository": "local/issue-67",
        "work_items": [work_item],
    }
    return intent, source


def _policy() -> dict:
    return {
        "version": 3,
        "low_risk_allowlist": [],
        "check_definitions": [
            {
                "check_id": "module-1",
                "version": 1,
                "command": [
                    "python",
                    "-c",
                    (
                        "from pathlib import Path; "
                        "assert Path('module-1.txt').read_text() == 'module 1\\n'"
                    ),
                ],
                "hosted_name": None,
                "environment_requirements": ["python"],
                "input_selector": ["module-1.txt"],
                "base_sensitive": False,
                "risk": "low",
                "hosted_only": False,
                "suite": "affected",
            },
            {
                "check_id": "module-1-repository",
                "version": 1,
                "command": [
                    "python",
                    "-c",
                    (
                        "from pathlib import Path; "
                        "assert Path('module-1.txt').read_text() == 'module 1\\n'"
                    ),
                ],
                "hosted_name": None,
                "environment_requirements": ["python"],
                "input_selector": ["module-1.txt"],
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
                "input_selector": ["module-1.txt"],
                "base_sensitive": False,
                "risk": "low",
                "hosted_only": True,
                "suite": "hosted",
            },
        ],
        "strict_review": {
            "specialist_requirements": [],
            "human_decision_required": False,
        },
    }


def _runtime_config() -> dict:
    settings = {
        "model": "kimi-code/kimi-for-coding",
        "thinkingOptionId": "on",
        "modeId": "yolo",
        "features": {},
    }
    return {
        "active_turn_pools": {"workers": 1, "coordinators": 1},
        "tiers": {
            tier: {"provider": "kimi-cli", "settings": dict(settings)}
            for tier in ("light", "standard", "heavy", "frontier")
        },
        "repositories": {
            "local/issue-67": {
                "active_turn_pools": {"workers": 1, "coordinators": 1}
            }
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
    }


class _ReviewingRuntime(InMemoryRuntimeAdapter):
    """In-memory Adapter that records Review materialization and can tamper diffs."""

    def __init__(self, workspace_root: Path, *, tamper=None):
        super().__init__(workspace_root)
        self.review_materializations: list[str] = []
        self.executions = 0
        self._tamper = tamper

    def _execute(self, binding, node):
        self.executions += 1
        if self._tamper is not None:
            self._tamper(Path(binding.workspace).resolve(), self.executions)
        return super()._execute(binding, node)

    def materialize_review_axis(self, request, profile, *, parent_agent_id):
        self.review_materializations.append(request.axis)
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


def _write_tamper(path: str, content: str, *, only_execution: int | None = None):
    def tamper(workspace: Path, ordinal: int) -> None:
        if only_execution is not None and ordinal != only_execution:
            return
        target = workspace / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    return tamper


def _delete_tamper(path: str):
    def tamper(workspace: Path, _ordinal: int) -> None:
        (workspace / path).unlink()

    return tamper


def _rename_tamper(source: str, target: str):
    def tamper(workspace: Path, _ordinal: int) -> None:
        (workspace / source).rename(workspace / target)

    return tamper


def _copy_tamper(source: str, target: str):
    def tamper(workspace: Path, _ordinal: int) -> None:
        (workspace / target).write_text(
            (workspace / source).read_text(encoding="utf-8"),
            encoding="utf-8",
        )

    return tamper


def _combined_tamper(*tampers):
    def tamper(workspace: Path, ordinal: int) -> None:
        for item in tampers:
            item(workspace, ordinal)

    return tamper


def _kernel(tmp_path, runtime, *, scopes, base_files=None):
    repository = _temporary_repository(tmp_path, extra_files=base_files)
    intent, source = _intent(scopes)
    compiled = PlanCompiler().compile(intent, source, _policy())
    store_path = tmp_path / "effect-contract.sqlite3"
    publication = LocalPlanPublication(store_path)
    publication.publish_and_activate(
        compiled,
        expected_active_digest=None,
        writer_generation="issue-67",
    )
    kernel = Kernel(
        store_path=store_path,
        publication=publication,
        runtime=runtime,
        verifier=EvidenceVerifier(),
        repository_path=repository,
        integration_branch="main",
        writer_generation="issue-67",
        delivery_control=InMemoryDeliveryControl(hosted_outcomes=("passed",)),
        runtime_config=_runtime_config(),
    )
    work_node = next(
        node
        for node in json.loads(compiled.canonical_bytes)["nodes"]
        if node["kind"] == "work"
    )
    return kernel, compiled, work_node


def _effect_record(kernel, compiled, work_node) -> dict:
    state = kernel._read_state(
        "local/issue-67",
        compiled.digest,
        work_node["node_key"],
    )
    assert state is not None
    record = state.get("effect_verification")
    assert isinstance(record, dict)
    return record


def test_in_scope_candidate_is_accepted_and_review_materializes(tmp_path):
    runtime = _ReviewingRuntime(tmp_path / "runtime")
    kernel, compiled, work_node = _kernel(
        tmp_path,
        runtime,
        scopes=["module-1.txt"],
    )

    outcome = kernel.reconcile_once("local/issue-67")

    assert outcome.status == "complete"
    assert sorted(runtime.review_materializations) == ["spec", "standards"]
    record = _effect_record(kernel, compiled, work_node)
    assert record["status"] == "accepted"
    assert record["findings"] == []
    assert record["plan_digest"] == compiled.digest
    assert record["node_key"] == work_node["node_key"]
    assert record["attempt_id"] == outcome.attempt_id
    assert record["candidate_sha"] == outcome.candidate_sha
    base_sha = _git(Path(kernel.repository_path), "rev-parse", "main")
    # Integration moved the branch; the record binds the admitted base.
    assert len(record["base_sha"]) == 40
    assert record["changed_paths"] == [
        {"status": "A", "path": "module-1.txt"}
    ]
    assert len(record["diff_projection_digest"]) == 64
    assert EffectContractVerification(
        **{k: v for k, v in record.items() if k != "changed_paths"}
        | {"changed_paths": tuple(record["changed_paths"])}
    ).has_valid_digest()
    del base_sha


def test_out_of_scope_write_fails_closed_before_review_materialization(tmp_path):
    runtime = _ReviewingRuntime(
        tmp_path / "runtime",
        tamper=_write_tamper("forbidden.txt", "out of scope\n"),
    )
    kernel, compiled, work_node = _kernel(
        tmp_path,
        runtime,
        scopes=["module-1.txt"],
    )

    first = kernel.reconcile_once("local/issue-67")

    # Fail closed: the unauthorized Candidate enters a Repair Round and no
    # Reviewer axis or deferred check capture ever starts for it.
    assert first.status == "waiting"
    assert first.attempt_state == "repairing"
    assert runtime.review_materializations == []
    record = _effect_record(kernel, compiled, work_node)
    assert record["status"] == "rejected"
    assert any("forbidden.txt" in finding for finding in record["findings"])
    assert {
        (entry["status"], entry["path"]) for entry in record["changed_paths"]
    } == {("A", "forbidden.txt"), ("A", "module-1.txt")}


def test_out_of_scope_delete_fails_closed(tmp_path):
    runtime = _ReviewingRuntime(
        tmp_path / "runtime",
        tamper=_delete_tamper("README.md"),
    )
    kernel, compiled, work_node = _kernel(
        tmp_path,
        runtime,
        scopes=["module-1.txt"],
    )

    first = kernel.reconcile_once("local/issue-67")

    assert first.status == "waiting"
    assert first.attempt_state == "repairing"
    assert runtime.review_materializations == []
    record = _effect_record(kernel, compiled, work_node)
    assert record["status"] == "rejected"
    assert any("README.md" in finding for finding in record["findings"])
    assert {
        (entry["status"], entry["path"]) for entry in record["changed_paths"]
    } == {("A", "module-1.txt"), ("D", "README.md")}


def test_rename_pair_source_out_of_scope_fails_closed(tmp_path):
    runtime = _ReviewingRuntime(
        tmp_path / "runtime",
        tamper=_rename_tamper("README.md", "stolen.txt"),
    )
    kernel, compiled, work_node = _kernel(
        tmp_path,
        runtime,
        scopes=["module-1.txt", "stolen.txt"],
    )

    first = kernel.reconcile_once("local/issue-67")

    assert first.status == "waiting"
    assert first.attempt_state == "repairing"
    assert runtime.review_materializations == []
    record = _effect_record(kernel, compiled, work_node)
    assert record["status"] == "rejected"
    assert any("README.md" in finding for finding in record["findings"])
    rename = next(
        entry for entry in record["changed_paths"] if entry["status"] == "R"
    )
    assert rename["source_path"] == "README.md"
    assert rename["path"] == "stolen.txt"


def test_copy_pair_source_out_of_scope_fails_closed(tmp_path):
    runtime = _ReviewingRuntime(
        tmp_path / "runtime",
        tamper=_copy_tamper("README.md", "copied.txt"),
    )
    kernel, compiled, work_node = _kernel(
        tmp_path,
        runtime,
        scopes=["module-1.txt", "copied.txt"],
    )

    first = kernel.reconcile_once("local/issue-67")

    assert first.status == "waiting"
    assert first.attempt_state == "repairing"
    assert runtime.review_materializations == []
    record = _effect_record(kernel, compiled, work_node)
    assert record["status"] == "rejected"
    assert any("README.md" in finding for finding in record["findings"])
    copy = next(
        entry for entry in record["changed_paths"] if entry["status"] == "C"
    )
    assert copy["source_path"] == "README.md"
    assert copy["path"] == "copied.txt"


def test_in_scope_rename_and_delete_are_accepted(tmp_path):
    runtime = _ReviewingRuntime(
        tmp_path / "runtime",
        tamper=_combined_tamper(
            _rename_tamper("legacy.txt", "modern.txt"),
            _delete_tamper("obsolete.txt"),
        ),
    )
    kernel, compiled, work_node = _kernel(
        tmp_path,
        runtime,
        scopes=["module-1.txt", "legacy.txt", "modern.txt", "obsolete.txt"],
        base_files={"legacy.txt": "legacy\n", "obsolete.txt": "obsolete\n"},
    )

    outcome = kernel.reconcile_once("local/issue-67")

    assert outcome.status == "complete"
    assert sorted(runtime.review_materializations) == ["spec", "standards"]
    record = _effect_record(kernel, compiled, work_node)
    assert record["status"] == "accepted"
    assert {
        (entry["status"], entry["path"]) for entry in record["changed_paths"]
    } == {("A", "module-1.txt"), ("R", "modern.txt"), ("D", "obsolete.txt")}


def test_updated_candidate_sha_recomputes_verification(tmp_path):
    runtime = _ReviewingRuntime(
        tmp_path / "runtime",
        tamper=_write_tamper("forbidden.txt", "out of scope\n", only_execution=1),
    )
    kernel, compiled, work_node = _kernel(
        tmp_path,
        runtime,
        scopes=["module-1.txt"],
    )

    first = kernel.reconcile_once("local/issue-67")

    assert first.status == "waiting"
    assert first.attempt_state == "repairing"
    assert runtime.review_materializations == []
    rejected_record = _effect_record(kernel, compiled, work_node)
    assert rejected_record["status"] == "rejected"

    outcome = first
    for _ in range(4):
        if outcome.status == "complete":
            break
        outcome = kernel.reconcile_once("local/issue-67")

    assert outcome.status == "complete"
    assert runtime.executions == 2
    # Review materialized exactly once per axis, only for the repaired Candidate.
    assert sorted(runtime.review_materializations) == ["spec", "standards"]
    record = _effect_record(kernel, compiled, work_node)
    assert record["status"] == "accepted"
    assert record["candidate_sha"] != rejected_record["candidate_sha"]
    # The recomputed record binds whatever Attempt delivered the accepted
    # Candidate (a fresh frontier Attempt may win the Recovery Ladder race).
    assert record["candidate_sha"] == outcome.candidate_sha
    assert record["attempt_id"] == outcome.attempt_id


def _unit_repository(tmp_path: Path) -> tuple[Path, str, str]:
    repository = _temporary_repository(
        tmp_path,
        extra_files={
            "modify.txt": "old\n",
            "delete.txt": "bye\n",
            "rename-old.txt": "rename me\n",
            "copy-source.txt": "copy me\n",
        },
    )
    base_sha = _git(repository, "rev-parse", "HEAD")
    (repository / "modify.txt").write_text("new\n", encoding="utf-8")
    (repository / "delete.txt").unlink()
    (repository / "rename-old.txt").rename(repository / "rename-new.txt")
    (repository / "copy-target.txt").write_text("copy me\n", encoding="utf-8")
    (repository / "added.txt").write_text("added\n", encoding="utf-8")
    _git(repository, "add", "--all")
    _git(repository, "commit", "-m", "candidate")
    candidate_sha = _git(repository, "rev-parse", "HEAD")
    return repository, base_sha, candidate_sha


def _unit_verifier(persisted: list) -> EffectContractVerifier:
    return EffectContractVerifier(
        assert_writer=lambda _state: None,
        persist_state=lambda state: persisted.append(dict(state)),
    )


def _unit_state(base_sha: str) -> dict:
    return {
        "repository": "local/unit",
        "plan_digest": "a" * 64,
        "activation_id": "activation:unit",
        "node_key": "node:unit",
        "attempt_id": "attempt:unit:1",
        "base_sha": base_sha,
        "effect_verification": None,
    }


def _unit_observation(candidate_sha: str) -> SimpleNamespace:
    return SimpleNamespace(
        result_claim=SimpleNamespace(candidate_sha=candidate_sha)
    )


def test_verification_covers_add_modify_delete_rename_copy_statuses(tmp_path):
    repository, base_sha, candidate_sha = _unit_repository(tmp_path)
    scopes = [
        "added.txt",
        "modify.txt",
        "delete.txt",
        "rename-old.txt",
        "rename-new.txt",
        "copy-source.txt",
        "copy-target.txt",
    ]
    persisted: list = []
    verifier = _unit_verifier(persisted)
    state = _unit_state(base_sha)

    decision = verifier.verify_candidate(
        state,
        {"effect_contract": {"write_scopes": scopes}},
        SimpleNamespace(workspace=str(repository)),
        _unit_observation(candidate_sha),
    )

    assert decision.status == "accepted"
    assert decision.verification.has_valid_digest()
    statuses = {
        (entry["status"], entry.get("source_path"), entry["path"])
        for entry in decision.verification.changed_paths
    }
    assert (("A", None, "added.txt")) in statuses
    assert (("M", None, "modify.txt")) in statuses
    assert (("D", None, "delete.txt")) in statuses
    assert (("R", "rename-old.txt", "rename-new.txt")) in statuses
    assert (("C", "copy-source.txt", "copy-target.txt")) in statuses
    assert decision.verification.plan_digest == "a" * 64
    assert decision.verification.node_key == "node:unit"
    assert decision.verification.attempt_id == "attempt:unit:1"
    assert decision.verification.base_sha == base_sha
    assert decision.verification.candidate_sha == candidate_sha
    assert len(persisted) == 1


def test_saved_record_is_reused_for_the_same_candidate(tmp_path):
    repository, base_sha, candidate_sha = _unit_repository(tmp_path)
    persisted: list = []
    verifier = _unit_verifier(persisted)
    state = _unit_state(base_sha)
    work_node = {"effect_contract": {"write_scopes": ["modify.txt"]}}
    binding = SimpleNamespace(workspace=str(repository))
    observation = _unit_observation(candidate_sha)

    first = verifier.verify_candidate(state, work_node, binding, observation)
    second = verifier.verify_candidate(state, work_node, binding, observation)

    assert first.status == "rejected"
    assert second.status == "rejected"
    assert first.verification == second.verification
    assert first.verification.has_valid_digest()
    # The second pass consumed the durable record instead of recomputing Git.
    assert len(persisted) == 1


def test_unresolvable_candidate_identity_fails_closed(tmp_path):
    repository, base_sha, _candidate_sha = _unit_repository(tmp_path)
    persisted: list = []
    verifier = _unit_verifier(persisted)
    state = _unit_state(base_sha)

    decision = verifier.verify_candidate(
        state,
        {"effect_contract": {"write_scopes": ["modify.txt"]}},
        SimpleNamespace(workspace=str(repository)),
        _unit_observation("f" * 40),
    )

    assert decision.status == "rejected"
    assert decision.findings
    assert decision.verification.changed_paths == ()
    assert len(persisted) == 1


def test_effect_contract_verification_is_one_deep_module_behind_kernel():
    import inspect
    import typing

    hints = typing.get_type_hints(EffectContractDecision)
    assert set(typing.get_args(hints["status"])) == {"accepted", "rejected"}
    assert hasattr(EffectContractVerifier, "verify_candidate")
    assert hasattr(EffectContractVerifier, "initial_fields")
    assert EffectContractVerifier.initial_fields() == {
        "effect_verification": None
    }
    kernel_source = inspect.getsource(Kernel)
    assert "--name-status" not in kernel_source
    assert "find-copies-harder" not in kernel_source
    assert "diff_projection_digest" not in kernel_source
