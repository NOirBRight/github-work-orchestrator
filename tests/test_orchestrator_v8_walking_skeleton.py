from __future__ import annotations

from dataclasses import replace
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest


SCRIPTS = (
    Path(__file__).resolve().parents[1] / "skills" / "orchestrator" / "scripts"
)
sys.path.insert(0, str(SCRIPTS))

from gwo_v8 import (  # noqa: E402
    EvidenceVerifier,
    CompileError,
    InMemoryRuntimeAdapter,
    Kernel,
    LocalPlanPublication,
    PlanCompiler,
    ResultClaim,
    RuntimeAdmission,
    RuntimePrompt,
)


def _ready_source(*, state: str = "ready-for-agent") -> dict:
    return {
        "repository": "local/walking-skeleton",
        "work_items": [
            {
                "work_item_key": "issue:41",
                "tracker_state": state,
                "source_ref": "synthetic://issue/41",
                "title": "Write the walking-skeleton artifact",
                "outcome_contract": {"path": "result.txt", "content": "phase-1\n"},
            }
        ],
    }


def _plan_intent(*, skill_reference: str | None = None) -> dict:
    return {
        "parent_plan_digest": None,
        "goals": [
            {
                "goal_key": "goal:phase-1",
                "objective": "Integrate one locally verified candidate.",
                "acceptance": ["result.txt contains phase-1"],
            }
        ],
        "nodes": [
            {
                "goal_key": "goal:phase-1",
                "work_item_key": "issue:41",
                "kind": "work",
                "inputs": {
                    "file_changes": [{"path": "result.txt", "content": "phase-1\n"}]
                },
                "output_contract": {
                    "required_evidence": [
                        {"kind": "candidate"},
                        {"kind": "check", "check_id": "result-content"},
                    ],
                    "checks": [
                        {
                            "check_id": "result-content",
                            "command": [
                                "python",
                                "-c",
                                (
                                    "from pathlib import Path; "
                                    "assert Path('result.txt').read_text() == 'phase-1\\n'"
                                ),
                            ],
                        }
                    ],
                },
                "effect_contract": {
                    "write_scopes": ["result.txt"],
                    "external_effects": [],
                },
                "resource_claims": [],
                "runtime_requirements": {"capabilities": ["git", "local_check"]},
                "difficulty": "light",
                "risk": "low",
                "recovery_policy": {"semantic_attempts": 1, "repair_rounds": 0},
                "skill_reference": skill_reference,
            }
        ],
        "edges": [],
    }


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
    _git(repository, "config", "user.name", "Phase One")
    _git(repository, "config", "user.email", "phase-one@example.invalid")
    (repository / "README.md").write_text("base\n", encoding="utf-8")
    _git(repository, "add", "README.md")
    _git(repository, "commit", "-m", "base")
    return repository


def test_plan_compiler_is_the_only_canonical_planspec_authority():
    compiler = PlanCompiler()

    compiled = compiler.compile(_plan_intent(), _ready_source(), {"version": 1})
    document = json.loads(compiled.canonical_bytes)

    assert set(document) == {
        "schema_version",
        "repository",
        "parent_plan_digest",
        "goals",
        "work_items",
        "nodes",
        "edges",
    }
    assert compiled.digest == hashlib.sha256(compiled.canonical_bytes).hexdigest()
    assert compiled == compiler.compile(
        dict(reversed(list(_plan_intent().items()))),
        dict(reversed(list(_ready_source().items()))),
        {"version": 1},
    )
    assert document["work_items"][0]["tracker_state"] == "ready-for-agent"
    assert {node["kind"] for node in document["nodes"]} == {"work", "integration"}
    assert document["edges"] == [
        {
            "from_node": document["nodes"][1]["node_key"],
            "to_node": document["nodes"][0]["node_key"],
            "type": "result_required",
        }
    ]
    assert b"compilation_record" not in compiled.canonical_bytes
    for runtime_fact in (b"provider", b"model", b"workspace", b"live_capacity"):
        assert runtime_fact not in compiled.canonical_bytes


@pytest.mark.parametrize(
    ("state", "code"),
    [
        ("needs-triage", "WORK_ITEM_NOT_READY"),
        ("needs-info", "WORK_ITEM_NOT_READY"),
        ("ready-for-human", "WORK_ITEM_NOT_READY"),
        ("wontfix", "WORK_ITEM_NOT_READY"),
    ],
)
def test_plan_compiler_rejects_non_ready_work_items(state, code):
    with pytest.raises(CompileError) as rejected:
        PlanCompiler().compile(
            _plan_intent(),
            _ready_source(state=state),
            {"version": 1},
        )

    assert rejected.value.code == code


@pytest.mark.parametrize("skill", ["implement", "/implement-gwo", "$orchestrator"])
def test_plan_compiler_rejects_recursive_workflow_skill_bindings(skill):
    with pytest.raises(CompileError) as rejected:
        PlanCompiler().compile(
            _plan_intent(skill_reference=skill),
            _ready_source(),
            {"version": 1},
        )

    assert rejected.value.code == "WORKFLOW_SKILL_RECURSION"


@pytest.mark.parametrize(
    "location",
    [
        "node.provider",
        "node.runtime_requirements.model",
        "node.inputs.workspace",
        "goal.live_capacity",
        "work_item.provider",
    ],
)
def test_plan_compiler_rejects_runtime_facts_anywhere_in_planspec(location):
    intent = _plan_intent()
    source = _ready_source()
    if location == "node.provider":
        intent["nodes"][0]["provider"] = "codex"
    elif location == "node.runtime_requirements.model":
        intent["nodes"][0]["runtime_requirements"]["model"] = "gpt-5.6-sol"
    elif location == "node.inputs.workspace":
        intent["nodes"][0]["inputs"]["workspace"] = "worker-1"
    elif location == "goal.live_capacity":
        intent["goals"][0]["live_capacity"] = 8
    else:
        source["work_items"][0]["provider"] = "codex"

    with pytest.raises(CompileError) as rejected:
        PlanCompiler().compile(intent, source, {"version": 1})

    assert rejected.value.code == "PLAN_FIELD_INVALID"


def test_plan_compiler_rejects_work_outside_the_effect_contract():
    intent = _plan_intent()
    intent["nodes"][0]["effect_contract"]["write_scopes"] = ["other.txt"]

    with pytest.raises(CompileError) as rejected:
        PlanCompiler().compile(intent, _ready_source(), {"version": 1})

    assert rejected.value.code == "EFFECT_CONTRACT_VIOLATION"


def test_local_plan_publication_consumes_compiled_bytes_unchanged(tmp_path):
    compiled = PlanCompiler().compile(
        _plan_intent(), _ready_source(), {"version": 1}
    )
    publication = LocalPlanPublication(tmp_path / "v8.sqlite3")

    activated = publication.publish_and_activate(
        compiled,
        expected_active_digest=None,
        writer_generation="phase-1",
    )
    active = publication.read_active("local/walking-skeleton")

    assert activated.status == "active"
    assert activated.plan_digest == compiled.digest
    assert active is not None
    assert active.plan_digest == compiled.digest
    assert active.canonical_bytes == compiled.canonical_bytes
    assert active.compilation_record == compiled.compilation_record
    assert publication.publish_and_activate(
        compiled,
        expected_active_digest=compiled.digest,
        writer_generation="phase-1",
    ) == activated


def test_worker_assertion_alone_cannot_verify_a_result():
    claim = ResultClaim(
        attempt_id="attempt:1",
        node_key="node:work",
        candidate_sha="a" * 40,
        assertions={"checks": ["result-content"], "done": True},
    )

    decision = EvidenceVerifier().verify(
        claim,
        {
            "required_evidence": [
                {"kind": "candidate"},
                {"kind": "check", "check_id": "result-content"},
            ]
        },
        None,
    )

    assert decision.status == "waiting"
    assert decision.result is None
    assert decision.missing_evidence == ("candidate", "check:result-content")


def test_in_memory_runtime_observes_candidate_and_local_check(tmp_path):
    repository = _temporary_repository(tmp_path)
    compiled = PlanCompiler().compile(
        _plan_intent(), _ready_source(), {"version": 1}
    )
    work_node = next(
        node
        for node in json.loads(compiled.canonical_bytes)["nodes"]
        if node["kind"] == "work"
    )
    admission = RuntimeAdmission(
        repository="local/walking-skeleton",
        plan_digest=compiled.digest,
        node_key=work_node["node_key"],
        admission_id="admission:1",
        repository_path=repository,
        base_sha=_git(repository, "rev-parse", "HEAD"),
    )
    runtime = InMemoryRuntimeAdapter(tmp_path / "runtime")

    binding = runtime.materialize(admission)
    assert binding.attempt_id is None
    assert binding.prompt_accepted is False
    runtime.accept_prompt(binding, RuntimePrompt.from_node(work_node))
    binding = runtime.read_binding(admission.admission_id)
    assert binding is not None
    assert binding.prompt_accepted is True
    runtime.attach_attempt(binding, "attempt:1")
    binding = runtime.read_binding(admission.admission_id)
    assert binding is not None
    assert binding.attempt_id == "attempt:1"
    runtime.resume(binding)
    observation = runtime.observe(binding)
    assert observation.result_claim is not None
    decision = EvidenceVerifier().verify(
        observation.result_claim,
        work_node["output_contract"],
        observation,
    )

    assert binding.repository == admission.repository
    assert binding.plan_digest == admission.plan_digest
    assert binding.node_key == admission.node_key
    assert binding.admission_id == admission.admission_id
    assert observation.result_claim.candidate_sha == _git(
        Path(binding.workspace), "rev-parse", "HEAD"
    )
    assert {evidence.kind for evidence in observation.evidence} == {
        "candidate",
        "check",
    }
    assert decision.status == "accepted"
    misbound = replace(observation.result_claim, attempt_id="attempt:other")
    assert (
        EvidenceVerifier()
        .verify(misbound, work_node["output_contract"], observation)
        .status
        == "rejected"
    )
    runtime.retire(binding)


def test_reconcile_once_completes_the_single_node_walking_skeleton(tmp_path):
    repository = _temporary_repository(tmp_path)
    store_path = tmp_path / "v8.sqlite3"
    compiled = PlanCompiler().compile(
        _plan_intent(), _ready_source(), {"version": 1}
    )
    publication = LocalPlanPublication(store_path)
    publication.publish_and_activate(
        compiled,
        expected_active_digest=None,
        writer_generation="phase-1",
    )
    kernel = Kernel(
        store_path=store_path,
        publication=publication,
        runtime=InMemoryRuntimeAdapter(tmp_path / "runtime"),
        verifier=EvidenceVerifier(),
        repository_path=repository,
        integration_branch="main",
        writer_generation="phase-1",
    )

    outcome = kernel.reconcile_once("local/walking-skeleton")

    assert outcome.directive == "goal_complete"
    assert outcome.admission_state == "consumed"
    assert outcome.attempt_state == "verified"
    assert outcome.goal_state == "completed"
    assert outcome.work_item_state == "integrated"
    assert outcome.plan_digest == compiled.digest
    assert outcome.candidate_sha == _git(repository, "rev-parse", "HEAD")
    assert (repository / "result.txt").read_text(encoding="utf-8") == "phase-1\n"
    assert kernel.reconcile_once("local/walking-skeleton") == outcome
