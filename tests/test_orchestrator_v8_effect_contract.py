"""#67: the Plan Node Effect Contract is enforced against the actual Candidate diff.

Before any Review materialization the Kernel consumes one typed decision from
the deep effect-contract verification module. The module computes authoritative
changed paths from the integration base SHA to the exact Candidate SHA with
Git identity, never from Worker self-report or the current workspace status.
"""

from __future__ import annotations

import json
from pathlib import Path
import sqlite3
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
    KernelError,
    LocalPlanPublication,
    PlanCompiler,
    ReviewAxisBinding,
    ReviewAxisObservation,
    TypedEvidence,
)
from gwo_v8.effect_verification import (  # noqa: E402
    EffectContractDecision,
    EffectContractVerifier,
    EffectVerificationError,
    _parse_changed_paths,
)
from gwo_v8._canonical import digest_value  # noqa: E402


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


class _AdoptionMustNotRun:
    adapter_name = "adoption-must-not-run"

    def normalize_profile(self, profile):
        return profile

    def __getattr__(self, name):
        raise AssertionError(f"invalid Result Adoption called Runtime.{name}")


def _write_tamper(path: str, content: str, *, only_execution: int | None = None):
    def tamper(workspace: Path, ordinal: int) -> None:
        if only_execution is not None and ordinal != only_execution:
            (workspace / path).unlink(missing_ok=True)
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
    return {**record["payload"], "_evidence": record}


def _successor_kernel(kernel, compiled):
    intent, source = _intent(["module-1.txt"])
    source["work_items"][0]["title"] = "Same Node contract in a successor Plan"
    successor = PlanCompiler().compile(intent, source, _policy())
    assert successor.digest != compiled.digest
    kernel.publication.publish_and_activate(
        successor,
        expected_active_digest=compiled.digest,
        writer_generation="issue-67",
    )
    return successor, Kernel(
        store_path=kernel.store_path,
        publication=kernel.publication,
        runtime=_AdoptionMustNotRun(),
        verifier=EvidenceVerifier(),
        repository_path=kernel.repository_path,
        integration_branch="main",
        writer_generation="issue-67",
        delivery_control=InMemoryDeliveryControl(hosted_outcomes=("passed",)),
        runtime_config=_runtime_config(),
    )


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
    assert TypedEvidence(**record["_evidence"]).has_valid_digest()
    del base_sha


def test_candidate_manifest_and_verified_result_retain_effect_evidence_source(
    tmp_path,
):
    runtime = _ReviewingRuntime(tmp_path / "runtime")
    kernel, compiled, work_node = _kernel(
        tmp_path,
        runtime,
        scopes=["module-1.txt"],
    )

    outcome = kernel.reconcile_once("local/issue-67")

    assert outcome.status == "complete"
    state = kernel._read_state(
        "local/issue-67",
        compiled.digest,
        work_node["node_key"],
    )
    assert state is not None
    effect = TypedEvidence(**state["effect_verification"])
    manifest = state["candidate_evidence_manifest"]
    assert {
        "kind": "decision",
        "decision_type": "effect_contract_verification",
        "content_digest": effect.content_digest,
        "source_ref": effect.source_ref,
    } in manifest["evidence"]
    assert state["candidate_evidence_manifest_digest"] == digest_value(manifest)
    with sqlite3.connect(kernel.store_path) as connection:
        saved = json.loads(
            connection.execute(
                """
                SELECT evidence_json
                FROM v8_verified_results
                WHERE plan_digest = ? AND node_key = ?
                """,
                (compiled.digest, work_node["node_key"]),
            ).fetchone()[0]
        )
    assert saved["effect_contract_verification"] == state["effect_verification"]
    assert saved["candidate_evidence_manifest"] == manifest


def test_result_adoption_rejects_missing_effect_contract_evidence(tmp_path):
    runtime = _ReviewingRuntime(tmp_path / "runtime")
    kernel, compiled, _work_node = _kernel(
        tmp_path,
        runtime,
        scopes=["module-1.txt"],
    )
    assert kernel.reconcile_once("local/issue-67").status == "complete"
    with sqlite3.connect(kernel.store_path) as connection:
        row = connection.execute(
            """
            SELECT rowid, evidence_json
            FROM v8_verified_results
            WHERE plan_digest = ?
            """,
            (compiled.digest,),
        ).fetchone()
        evidence_record = json.loads(row[1])
        del evidence_record["effect_contract_verification"]
        connection.execute(
            """
            UPDATE v8_verified_results
            SET evidence_json = ?, evidence_manifest_digest = ?
            WHERE rowid = ?
            """,
            (
                json.dumps(evidence_record, separators=(",", ":"), sort_keys=True),
                digest_value(evidence_record),
                row[0],
            ),
        )
    _successor, successor_kernel = _successor_kernel(kernel, compiled)

    with pytest.raises(AssertionError, match="Runtime.read_binding"):
        successor_kernel.reconcile_once("local/issue-67")


def test_result_adoption_accepts_bound_evidence_across_plan_digests(tmp_path):
    runtime = _ReviewingRuntime(tmp_path / "runtime")
    kernel, compiled, work_node = _kernel(
        tmp_path,
        runtime,
        scopes=["module-1.txt"],
    )
    original = kernel.reconcile_once("local/issue-67")
    successor, successor_kernel = _successor_kernel(kernel, compiled)

    adopted = successor_kernel.reconcile_once("local/issue-67")

    assert successor.digest != compiled.digest
    assert adopted.status == "complete"
    assert adopted.attempt_state == "adopted"
    assert adopted.candidate_sha == original.candidate_sha
    assert adopted.result_digest == original.result_digest
    state = successor_kernel._read_state(
        "local/issue-67",
        successor.digest,
        work_node["node_key"],
    )
    assert state["adopted_from_plan_digest"] == compiled.digest


def test_result_adoption_accepts_later_descendant_target_commit(tmp_path):
    runtime = _ReviewingRuntime(tmp_path / "runtime")
    kernel, compiled, _work_node = _kernel(
        tmp_path,
        runtime,
        scopes=["module-1.txt"],
    )
    original = kernel.reconcile_once("local/issue-67")
    repository = Path(kernel.repository_path)
    (repository / "README.md").write_text(
        "base\nlater integrated work\n",
        encoding="utf-8",
    )
    _git(repository, "add", "README.md")
    _git(repository, "commit", "-m", "later target commit")
    later_target = _git(repository, "rev-parse", "main")
    assert later_target != original.candidate_sha
    _successor, successor_kernel = _successor_kernel(kernel, compiled)

    adopted = successor_kernel.reconcile_once("local/issue-67")

    assert adopted.status == "complete"
    assert adopted.attempt_state == "adopted"
    assert adopted.candidate_sha == original.candidate_sha
    assert adopted.result_digest == original.result_digest


def test_result_adoption_rejects_divergent_current_target(tmp_path):
    runtime = _ReviewingRuntime(tmp_path / "runtime")
    kernel, compiled, work_node = _kernel(
        tmp_path,
        runtime,
        scopes=["module-1.txt"],
    )
    assert kernel.reconcile_once("local/issue-67").status == "complete"
    state = kernel._read_state(
        "local/issue-67",
        compiled.digest,
        work_node["node_key"],
    )
    repository = Path(kernel.repository_path)
    _git(repository, "switch", "--detach", state["base_sha"])
    (repository / "README.md").write_text(
        "base\ndivergent target\n",
        encoding="utf-8",
    )
    _git(repository, "add", "README.md")
    _git(repository, "commit", "-m", "divergent target")
    divergent_target = _git(repository, "rev-parse", "HEAD")
    _git(repository, "update-ref", "refs/heads/main", divergent_target)
    _successor, successor_kernel = _successor_kernel(kernel, compiled)

    with pytest.raises(AssertionError, match="Runtime.read_binding"):
        successor_kernel.reconcile_once("local/issue-67")


def test_result_adoption_rejects_forged_effect_evidence_binding(tmp_path):
    runtime = _ReviewingRuntime(tmp_path / "runtime")
    kernel, compiled, _work_node = _kernel(
        tmp_path,
        runtime,
        scopes=["module-1.txt"],
    )
    assert kernel.reconcile_once("local/issue-67").status == "complete"
    with sqlite3.connect(kernel.store_path) as connection:
        row = connection.execute(
            """
            SELECT rowid, evidence_json
            FROM v8_verified_results
            WHERE plan_digest = ?
            """,
            (compiled.digest,),
        ).fetchone()
        evidence_record = json.loads(row[1])
        original = TypedEvidence(
            **evidence_record["effect_contract_verification"]
        )
        forged_payload = {
            **original.payload,
            "changed_paths": [
                *original.payload["changed_paths"],
                {"status": "A", "path": "forged.txt"},
            ],
        }
        forged_payload["diff_projection_digest"] = digest_value(
            {
                "base_sha": forged_payload["base_sha"],
                "base_tree_sha": forged_payload["base_tree_sha"],
                "candidate_sha": forged_payload["candidate_sha"],
                "candidate_tree_sha": forged_payload["candidate_tree_sha"],
                "changed_paths": forged_payload["changed_paths"],
            }
        )
        forged = TypedEvidence._capture(
            kind=original.kind,
            subject=original.subject,
            observer_type=original.observer_type,
            observer_id=original.observer_id,
            observed_at=original.observed_at,
            source_ref=original.source_ref,
            payload=forged_payload,
        )
        evidence_record["effect_contract_verification"] = (
            forged.__dict__
        )
        for item in evidence_record["candidate_evidence_manifest"]["evidence"]:
            if item.get("decision_type") == "effect_contract_verification":
                item["content_digest"] = forged.content_digest
        evidence_record["candidate_evidence_manifest_digest"] = digest_value(
            evidence_record["candidate_evidence_manifest"]
        )
        connection.execute(
            """
            UPDATE v8_verified_results
            SET evidence_json = ?, evidence_manifest_digest = ?
            WHERE rowid = ?
            """,
            (
                json.dumps(evidence_record, separators=(",", ":"), sort_keys=True),
                digest_value(evidence_record),
                row[0],
            ),
        )
    _successor, successor_kernel = _successor_kernel(kernel, compiled)

    with pytest.raises(AssertionError, match="Runtime.read_binding"):
        successor_kernel.reconcile_once("local/issue-67")


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
        observer_id="kernel:unit",
        assert_writer=lambda _state: None,
        persist_state=lambda state: persisted.append(dict(state)),
    )


def _unit_state(base_sha: str) -> dict:
    return {
        "repository": "local/unit",
        "plan_digest": "a" * 64,
        "activation_id": "activation:unit",
        "node_key": "node:unit",
        "contract_digest": "b" * 64,
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
    payload = decision.verification.payload
    statuses = {
        (entry["status"], entry.get("source_path"), entry["path"])
        for entry in payload["changed_paths"]
    }
    assert (("A", None, "added.txt")) in statuses
    assert (("M", None, "modify.txt")) in statuses
    assert (("D", None, "delete.txt")) in statuses
    assert (("R", "rename-old.txt", "rename-new.txt")) in statuses
    assert (("C", "copy-source.txt", "copy-target.txt")) in statuses
    assert payload["plan_digest"] == "a" * 64
    assert payload["node_key"] == "node:unit"
    assert payload["attempt_id"] == "attempt:unit:1"
    assert payload["base_sha"] == base_sha
    assert payload["candidate_sha"] == candidate_sha
    assert len(persisted) == 1


def test_effect_contract_verification_uses_common_typed_evidence_envelope(tmp_path):
    repository, base_sha, candidate_sha = _unit_repository(tmp_path)
    persisted: list = []
    state = _unit_state(base_sha)
    work_node = {
        "node_key": state["node_key"],
        "contract_digest": state["contract_digest"],
        "effect_contract": {"write_scopes": ["modify.txt"]},
    }

    decision = _unit_verifier(persisted).verify_candidate(
        state,
        work_node,
        SimpleNamespace(workspace=str(repository)),
        _unit_observation(candidate_sha),
    )

    evidence = decision.verification
    assert isinstance(evidence, TypedEvidence)
    assert evidence.kind == "decision"
    assert evidence.subject == candidate_sha
    assert evidence.observer_type == "kernel"
    assert evidence.observer_id == "kernel:unit"
    assert evidence.source_ref == (
        "store://effect-contract-verification/"
        f"{state['plan_digest']}/{state['node_key']}/"
        f"{state['attempt_id']}/{candidate_sha}"
    )
    assert evidence.payload["decision_type"] == "effect_contract_verification"
    assert evidence.payload["plan_digest"] == state["plan_digest"]
    assert evidence.payload["node_key"] == state["node_key"]
    assert evidence.payload["attempt_id"] == state["attempt_id"]
    assert evidence.payload["base_sha"] == base_sha
    assert evidence.payload["candidate_sha"] == candidate_sha
    assert evidence.payload["contract_digest"] == state["contract_digest"]
    assert evidence.payload["write_scopes"] == ["modify.txt"]
    assert len(evidence.payload["diff_projection_digest"]) == 64
    assert evidence.has_valid_digest()
    assert state["effect_verification"] == persisted[0]["effect_verification"]


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
    assert decision.verification.payload["changed_paths"] == []
    assert len(persisted) == 1


def test_candidate_parent_of_exact_base_fails_closed_before_diff(tmp_path):
    repository, parent_sha, exact_base_sha = _unit_repository(tmp_path)
    persisted: list = []
    verifier = _unit_verifier(persisted)

    decision = verifier.verify_candidate(
        _unit_state(exact_base_sha),
        {"effect_contract": {"write_scopes": ["modify.txt"]}},
        SimpleNamespace(workspace=str(repository)),
        _unit_observation(parent_sha),
    )

    assert decision.status == "rejected"
    assert decision.verification.payload["changed_paths"] == []
    assert decision.findings == (
        "Candidate is not descended from the exact integration base",
    )
    assert len(persisted) == 1


def test_merge_base_operational_error_does_not_consume_recovery(
    tmp_path,
    monkeypatch,
):
    runtime = _ReviewingRuntime(tmp_path / "runtime")
    kernel, compiled, work_node = _kernel(
        tmp_path,
        runtime,
        scopes=["module-1.txt"],
    )
    real_run = subprocess.run

    def fail_merge_base(command, *args, **kwargs):
        if "merge-base" in command:
            return subprocess.CompletedProcess(
                command,
                128,
                stdout=b"",
                stderr=b"fatal: repository read failed",
            )
        return real_run(command, *args, **kwargs)

    monkeypatch.setattr(
        "gwo_v8.effect_verification.subprocess.run",
        fail_merge_base,
    )

    with pytest.raises(KernelError) as operational:
        kernel.reconcile_once("local/issue-67")

    assert operational.value.code == "GIT_OPERATION_FAILED"
    assert runtime.review_materializations == []
    state = kernel._read_state(
        "local/issue-67",
        compiled.digest,
        work_node["node_key"],
    )
    assert state["attempt_state"] == "result_submitted"
    assert state["repair_rounds_used"] == 0
    assert state["effect_verification"] is None


def test_git_posix_path_with_literal_backslash_is_not_windows_normalized():
    with pytest.raises(EffectVerificationError) as rejected:
        _parse_changed_paths(b"A\0directory\\literal.txt\0")

    assert rejected.value.code == "GIT_CHANGED_PATHS_INVALID"
    assert "backslash" in rejected.value.detail


@pytest.mark.parametrize(
    "raw_path",
    (b"/absolute.txt", b"directory/../escape.txt", b"directory/./alias.txt"),
)
def test_git_diff_paths_reject_absolute_and_dot_escapes(raw_path):
    with pytest.raises(EffectVerificationError) as rejected:
        _parse_changed_paths(b"A\0" + raw_path + b"\0")

    assert rejected.value.code == "GIT_CHANGED_PATHS_INVALID"


def test_git_diff_paths_reject_nul_record_injection():
    with pytest.raises(EffectVerificationError):
        _parse_changed_paths(b"A\0safe.txt\0forged-status\0")


def test_effect_violation_remains_in_mixed_check_and_review_repair_packet():
    review = TypedEvidence._capture(
        kind="review",
        subject="c" * 40,
        observer_type="runtime_adapter",
        observer_id="review:unit",
        observed_at="2026-07-26T00:00:00+00:00",
        source_ref="runtime://review/unit",
        payload={
            "axes": [
                {
                    "axis": "spec",
                    "findings": [
                        {
                            "severity": "hard",
                            "code": "SPEC_BLOCK",
                            "source": "synthetic://issue/67",
                            "location": "forbidden.txt:1",
                            "message": "The Candidate changes a forbidden path.",
                        }
                    ],
                }
            ]
        },
    )
    check_definition = {
        "check_id": "module-1",
        "suite": "affected",
        "hosted_only": False,
        "definition_digest": "d" * 64,
        "command": ["python", "-c", "raise SystemExit(2)"],
        "environment_requirements": [],
    }
    environment = {"platform": "test"}
    candidate = TypedEvidence._capture(
        kind="candidate",
        subject="c" * 40,
        observer_type="runtime_adapter",
        observer_id="runtime:unit",
        observed_at="2026-07-26T00:00:00+00:00",
        source_ref="runtime://candidate/unit",
        payload={"tree_sha": "c" * 40},
    )
    failed_check = TypedEvidence._capture(
        kind="check",
        subject="c" * 40,
        observer_type="runtime_adapter",
        observer_id="runtime:unit",
        observed_at="2026-07-26T00:00:00+00:00",
        source_ref="runtime://check/unit",
        payload={
            "check_id": "module-1",
            "outcome": "failed",
            "exit_code": 2,
            "definition_digest": "d" * 64,
            "command_digest": digest_value(check_definition["command"]),
            "observed_tree_digest": "c" * 40,
            "environment_requirements": [],
            "environment_identity": environment,
            "environment_digest": digest_value(environment),
            "input_projection_digest": "a" * 64,
            "log_digest": "b" * 64,
        },
    )
    state = {
        "candidate_sha": "c" * 40,
        "review_evidence": review.__dict__,
        "candidate_observation": {
            "binding": {"runtime_id": "runtime:unit"},
            "evidence": [candidate.__dict__, failed_check.__dict__],
        },
    }
    work_node = {"output_contract": {"checks": [check_definition]}}

    causes = Kernel._repair_causes(
        state,
        work_node,
        cause_type="effect_contract_violation",
        findings=("changed path 'forbidden.txt' is outside Write Scope",),
    )

    assert [cause["type"] for cause in causes] == [
        "review_blocker",
        "local_check_failure",
        "effect_contract_violation",
    ]
    assert causes[1]["candidate_sha"] == "c" * 40
    assert causes[1]["evidence_digest"] == failed_check.content_digest
    assert causes[-1]["messages"] == [
        "changed path 'forbidden.txt' is outside Write Scope"
    ]


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
    assert "merge-base" not in kernel_source
    assert "effect_contract_evidence_binds" not in kernel_source
    assert "expected_manifest" not in kernel_source
