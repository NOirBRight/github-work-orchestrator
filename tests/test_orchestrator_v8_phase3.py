from __future__ import annotations

from dataclasses import replace
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest


SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "orchestrator" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from gwo_v8 import (  # noqa: E402
    CompileError,
    EvidenceVerifier,
    GitHubCliDeliveryControl,
    InMemoryDeliveryControl,
    InMemoryPaseoClient,
    InMemoryRuntimeAdapter,
    Kernel,
    KernelError,
    LocalPlanPublication,
    PlanCompiler,
    PaseoCliClient,
    PaseoAgentRecord,
    PaseoCreateRequest,
    PaseoRuntimeAdapter,
    ReviewAxisRequest,
    RecoveryLadder,
    RuntimeAdmission,
    RuntimeAdapterError,
    RuntimeProfile,
    RuntimePrompt,
    resolve_review_profile,
)
from gwo_v8.kernel import DeliveryControlError  # noqa: E402
from gwo_v8.runtime import (  # noqa: E402
    PASEO_INLINE_PROMPT_MAX_BYTES,
    _environment_snapshot,
)
import orch_core  # noqa: E402


def test_paseo_cli_client_resolves_windows_command_trampoline(monkeypatch):
    resolved = r"C:\Users\test\.local\bin\paseo.CMD"
    monkeypatch.setattr("gwo_v8.runtime.shutil.which", lambda _value: resolved)

    assert PaseoCliClient().executable == resolved


def test_paseo_cli_create_does_not_publish_prompt_digest_from_create_ack(
    monkeypatch,
):
    client = PaseoCliClient(executable="paseo")
    prompt = RuntimePrompt(text="work", digest="a" * 64)
    profile = RuntimeProfile(
        name="standard",
        provider="kimi-cli",
        model="kimi-code/kimi-for-coding",
        thinking="on",
        mode="yolo",
        features={},
    )
    record = PaseoAgentRecord(
        agent_id="agent-1",
        session_id="session-1",
        workspace_id="workspace-1",
        workspace="C:/worktree",
        parent_agent_id=None,
        provider="kimi-cli",
        model=profile.model,
        profile_digest=profile.digest,
        thinking=profile.thinking,
        mode=profile.mode,
        features={},
        labels={},
        lifecycle="running",
        archived=False,
    )
    commands = []

    def run(command, **_kwargs):
        commands.append(command)
        if command[0] == "run":
            return {"agentId": record.agent_id}
        raise AssertionError(command)

    monkeypatch.setattr(client, "_run", run)
    monkeypatch.setattr(
        client,
        "find_by_labels",
        lambda labels: (replace(record, labels=dict(labels)),),
    )

    created = client.create(
        PaseoCreateRequest(
            action_key="action-1",
            title="worker",
            labels={"gwo.action_key": "action-1"},
            prompt=prompt,
            repository_path="C:/repository",
            base_sha="b" * 40,
            profile=profile,
            parent_agent_id=None,
        )
    )

    assert created.agent_id == record.agent_id
    prompt_label = f"gwo.prompt_digest={prompt.digest}"
    assert prompt_label not in commands[0]
    assert prompt.text == commands[0][-1]
    assert prompt_label not in created.labels


def _ready_source() -> dict:
    return {
        "repository": "local/phase-three",
        "work_items": [
            {
                "work_item_key": "issue:45",
                "tracker_state": "ready-for-agent",
                "source_ref": "synthetic://issue/45",
                "title": "Deliver one exact Candidate locally first",
                "outcome_contract": {
                    "path": "result.txt",
                    "content": "phase-3\n",
                },
            }
        ],
    }


def _plan_intent(*, risk: str = "low") -> dict:
    check_command = [
        "python",
        "-c",
        (
            "from pathlib import Path; "
            "assert Path('result.txt').read_text() == 'phase-3\\n'"
        ),
    ]
    affected_command = [
        "python",
        "-c",
        "from pathlib import Path; assert Path('result.txt').is_file()",
    ]
    return {
        "parent_plan_digest": None,
        "goals": [
            {
                "goal_key": "goal:phase-3",
                "objective": "Publish and integrate one exact Candidate.",
                "acceptance": ["result.txt contains phase-3"],
            }
        ],
        "nodes": [
            {
                "goal_key": "goal:phase-3",
                "work_item_key": "issue:45",
                "kind": "work",
                "inputs": {
                    "file_changes": [{"path": "result.txt", "content": "phase-3\n"}]
                },
                "output_contract": {
                    "required_evidence": [
                        {"kind": "candidate"},
                        {"kind": "check", "check_id": "result-content"},
                        {"kind": "check", "check_id": "result-affected"},
                    ],
                    "checks": [
                        {
                            "check_id": "result-content",
                            "command": check_command,
                        },
                        {
                            "check_id": "result-affected",
                            "command": affected_command,
                        },
                    ],
                },
                "effect_contract": {
                    "write_scopes": ["result.txt"],
                    "external_effects": [],
                },
                "resource_claims": [],
                "runtime_requirements": {"capabilities": ["git", "local_check"]},
                "difficulty": "standard",
                "risk": risk,
                "recovery_policy": {
                    "semantic_attempts": 2,
                    "repair_rounds": 1,
                },
                "skill_reference": None,
            }
        ],
        "edges": [],
    }


def _policy_snapshot() -> dict:
    return {
        "version": 3,
        "low_risk_allowlist": ["result.txt"],
        "check_definitions": [
            {
                "check_id": "result-content",
                "version": 1,
                "command": _plan_intent()["nodes"][0]["output_contract"]["checks"][0][
                    "command"
                ],
                "hosted_name": None,
                "environment_requirements": ["python"],
                "input_selector": ["result.txt"],
                "base_sensitive": False,
                "risk": "low",
                "hosted_only": False,
                "suite": "repository",
            },
            {
                "check_id": "result-affected",
                "version": 1,
                "command": _plan_intent()["nodes"][0]["output_contract"]["checks"][1][
                    "command"
                ],
                "hosted_name": None,
                "environment_requirements": ["python"],
                "input_selector": ["result.txt"],
                "base_sensitive": False,
                "risk": "low",
                "hosted_only": False,
                "suite": "affected",
            },
            {
                "check_id": "result-hosted",
                "version": 1,
                "command": ["python", "-c", "raise SystemExit(0)"],
                "hosted_name": "Phase Three CI",
                "environment_requirements": [],
                "input_selector": ["result.txt"],
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


def _compiled_work_node(*, risk: str = "low") -> dict:
    compiled = PlanCompiler().compile(
        _plan_intent(risk=risk),
        _ready_source(),
        _policy_snapshot(),
    )
    plan = json.loads(compiled.canonical_bytes)
    return next(node for node in plan["nodes"] if node["kind"] == "work")


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
    _git(repository, "config", "user.name", "Phase Three")
    _git(repository, "config", "user.email", "phase-three@example.invalid")
    (repository / "README.md").write_text("base\n", encoding="utf-8")
    _git(repository, "add", "README.md")
    _git(repository, "commit", "-m", "base")
    return repository


def test_repair_changed_files_preserves_exact_git_paths(tmp_path):
    repository = _temporary_repository(tmp_path)
    base_sha = _git(repository, "rev-parse", "HEAD")
    paths = [
        "unicodé-雪.txt",
        "line\nbreak.txt",
        " leading.txt",
        "trailing.txt ",
    ]
    tree_entries = [("README.md", _git(repository, "rev-parse", "HEAD:README.md"))]
    for ordinal, path in enumerate(paths):
        blob = subprocess.run(
            ["git", "-C", str(repository), "hash-object", "-w", "--stdin"],
            input=f"exact path {ordinal}\n".encode(),
            check=True,
            capture_output=True,
        ).stdout.decode("ascii").strip()
        tree_entries.append((path, blob))
    tree_input = b"".join(
        b"100644 blob "
        + blob.encode("ascii")
        + b"\t"
        + path.encode("utf-8")
        + b"\0"
        for path, blob in sorted(
            tree_entries,
            key=lambda entry: entry[0].encode("utf-8"),
        )
    )
    tree_sha = subprocess.run(
        ["git", "-C", str(repository), "mktree", "-z"],
        input=tree_input,
        check=True,
        capture_output=True,
    ).stdout.decode("ascii").strip()
    candidate_sha = subprocess.run(
        ["git", "-C", str(repository), "commit-tree", tree_sha, "-p", base_sha],
        input=b"exact paths\n",
        check=True,
        capture_output=True,
    ).stdout.decode("ascii").strip()
    binding = SimpleNamespace(workspace=str(repository))
    declared_inputs = {
        "inputs": {"file_changes": [{"path": "declared-only.txt"}]}
    }

    changed_files = Kernel._repair_changed_files(
        object(),
        {"candidate_sha": candidate_sha, "base_sha": base_sha},
        declared_inputs,
        binding,
    )

    assert changed_files == sorted(paths, key=lambda path: path.encode("utf-8"))

    with pytest.raises(KernelError) as git_error:
        Kernel._repair_changed_files(
            object(),
            {"candidate_sha": "0" * 40, "base_sha": base_sha},
            declared_inputs,
            binding,
        )
    assert git_error.value.code == "GIT_OPERATION_FAILED"


def _execute_candidate(tmp_path: Path, *, risk: str = "low"):
    repository = _temporary_repository(tmp_path)
    compiled = PlanCompiler().compile(
        _plan_intent(risk=risk),
        _ready_source(),
        _policy_snapshot(),
    )
    work_node = next(
        node
        for node in json.loads(compiled.canonical_bytes)["nodes"]
        if node["kind"] == "work"
    )
    admission = RuntimeAdmission(
        repository="local/phase-three",
        plan_digest=compiled.digest,
        node_key=work_node["node_key"],
        admission_id="admission:phase-three",
        repository_path=repository,
        base_sha=_git(repository, "rev-parse", "HEAD"),
    )
    runtime = InMemoryRuntimeAdapter(tmp_path / "runtime")
    binding = runtime.materialize(
        admission,
        RuntimePrompt.from_node(work_node),
    )
    binding = runtime.attach_attempt(binding, "attempt:phase-three:1")
    runtime.resume(binding)
    observation = runtime.observe(binding)
    assert observation.result_claim is not None
    return work_node, runtime, binding, observation


def test_compiler_derives_low_risk_review_none_and_versioned_check_contract():
    node = _compiled_work_node()

    assert node["output_contract"]["review_requirement"] == {
        "mode": "none",
        "axes": [],
        "specialist_requirements": [],
        "human_decision_required": False,
    }
    check = next(
        item
        for item in node["output_contract"]["checks"]
        if item["check_id"] == "result-content"
    )
    assert check["check_id"] == "result-content"
    assert check["version"] == 1
    assert check["input_selector"] == ["result.txt"]
    assert check["suite"] == "repository"
    assert len(check["definition_digest"]) == 64
    assert (
        hashlib.sha256(
            json.dumps(
                {
                    key: value
                    for key, value in check.items()
                    if key != "definition_digest"
                },
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()
        == check["definition_digest"]
    )
    encoded = json.dumps(node["output_contract"], sort_keys=True)
    for runtime_fact in ("provider", "model", "live_capacity", "review_profile"):
        assert runtime_fact not in encoded


def test_compiler_enforces_review_risk_and_canonical_spec_input():
    standard = _compiled_work_node(risk="standard")

    assert standard["output_contract"]["review_requirement"] == {
        "mode": "dual_axis",
        "axes": ["standards", "spec"],
        "specialist_requirements": [],
        "human_decision_required": False,
    }
    assert {"kind": "review"} in standard["output_contract"]["required_evidence"]

    outside_allowlist = _plan_intent()
    outside_allowlist["nodes"][0]["inputs"]["file_changes"][0]["path"] = "src/api.py"
    outside_allowlist["nodes"][0]["effect_contract"]["write_scopes"] = ["src/api.py"]
    outside_allowlist["workaround"] = "not canonical"
    source = _ready_source()
    source["work_items"][0]["outcome_contract"]["path"] = "src/api.py"
    with pytest.raises(CompileError) as downgraded:
        PlanCompiler().compile(
            {
                key: value
                for key, value in outside_allowlist.items()
                if key != "workaround"
            },
            source,
            _policy_snapshot(),
        )
    assert downgraded.value.code == "LOW_RISK_NOT_ALLOWED"

    missing_spec = _plan_intent(risk="standard")
    missing_spec["goals"][0]["acceptance"] = []
    with pytest.raises(CompileError) as rejected:
        PlanCompiler().compile(
            missing_spec,
            _ready_source(),
            _policy_snapshot(),
        )
    assert rejected.value.code == "SPEC_INPUT_MISSING"


def test_compiler_requires_full_suite_and_pre_review_affected_check():
    affected_only = _plan_intent()
    contract = affected_only["nodes"][0]["output_contract"]
    contract["checks"] = contract["checks"][1:]
    contract["required_evidence"] = [
        {"kind": "candidate"},
        {"kind": "check", "check_id": "result-affected"},
    ]
    affected_policy = _policy_snapshot()
    affected_policy["check_definitions"] = affected_policy["check_definitions"][1:]
    with pytest.raises(CompileError) as no_full:
        PlanCompiler().compile(
            affected_only,
            _ready_source(),
            affected_policy,
        )
    assert no_full.value.code == "REPOSITORY_CHECK_MISSING"

    full_only = _plan_intent(risk="standard")
    contract = full_only["nodes"][0]["output_contract"]
    contract["checks"] = contract["checks"][:1]
    contract["required_evidence"] = [
        {"kind": "candidate"},
        {"kind": "check", "check_id": "result-content"},
    ]
    repository_policy = _policy_snapshot()
    repository_policy["check_definitions"] = [
        repository_policy["check_definitions"][0],
        repository_policy["check_definitions"][2],
    ]
    with pytest.raises(CompileError) as no_cheap:
        PlanCompiler().compile(
            full_only,
            _ready_source(),
            repository_policy,
        )
    assert no_cheap.value.code == "AFFECTED_CHECK_MISSING"


def test_compiler_adds_every_applicable_policy_check_and_evidence_requirement():
    intent = _plan_intent()
    contract = intent["nodes"][0]["output_contract"]
    contract["checks"] = []
    contract["required_evidence"] = [{"kind": "candidate"}]

    compiled = PlanCompiler().compile(
        intent,
        _ready_source(),
        _policy_snapshot(),
    )
    node = next(
        item
        for item in json.loads(compiled.canonical_bytes)["nodes"]
        if item["kind"] == "work"
    )

    assert {check["check_id"] for check in node["output_contract"]["checks"]} == {
        "result-content",
        "result-affected",
        "result-hosted",
    }
    assert {
        requirement["check_id"]
        for requirement in node["output_contract"]["required_evidence"]
        if requirement["kind"] == "check"
    } == {"result-content", "result-affected"}


def test_compiler_rejects_v3_delivery_without_typed_hosted_check():
    policy = _policy_snapshot()
    policy["check_definitions"] = [
        definition
        for definition in policy["check_definitions"]
        if definition["hosted_only"] is not True
    ]

    with pytest.raises(CompileError) as rejected:
        PlanCompiler().compile(
            _plan_intent(),
            _ready_source(),
            policy,
        )

    assert rejected.value.code == "HOSTED_CHECK_MISSING"


def test_compiler_emits_explicit_human_decision_gate_for_strict_work():
    compiled = PlanCompiler().compile(
        _plan_intent(risk="strict"),
        _ready_source(),
        _policy_snapshot(),
    )
    plan = json.loads(compiled.canonical_bytes)
    work = next(node for node in plan["nodes"] if node["kind"] == "work")
    decision = next(node for node in plan["nodes"] if node["kind"] == "decision")
    integration = next(node for node in plan["nodes"] if node["kind"] == "integration")

    assert work["output_contract"]["review_requirement"] == {
        "mode": "strict",
        "axes": ["standards", "spec"],
        "specialist_requirements": [],
        "human_decision_required": True,
    }
    assert plan["edges"] == [
        {
            "from_node": work["node_key"],
            "to_node": decision["node_key"],
            "type": "result_required",
        },
        {
            "from_node": decision["node_key"],
            "to_node": integration["node_key"],
            "type": "decision_required",
        },
    ]


def test_low_risk_publication_eligibility_consumes_typed_check_evidence(
    tmp_path,
):
    work_node, _runtime, _binding, observation = _execute_candidate(tmp_path)
    verifier = EvidenceVerifier()

    eligibility = verifier.publication_eligibility(
        observation.result_claim,
        work_node["output_contract"],
        observation,
    )

    assert eligibility.eligible is True
    assert eligibility.candidate_sha == observation.result_claim.candidate_sha
    assert eligibility.missing_evidence == ()
    assert eligibility.blockers == ()
    assert eligibility.review_evidence_digest is None
    check = next(
        evidence for evidence in observation.evidence if evidence.kind == "check"
    )
    definition = work_node["output_contract"]["checks"][0]
    candidate = next(
        evidence for evidence in observation.evidence if evidence.kind == "candidate"
    )
    assert check.payload["definition_digest"] == definition["definition_digest"]
    assert check.payload["observed_tree_digest"] == candidate.payload["tree_sha"]
    assert check.payload["environment_requirements"] == ["python"]
    assert check.payload["environment_identity"]["python"]["executable"]
    assert check.payload["environment_identity"]["python"]["version"]
    assert len(check.payload["environment_digest"]) == 64
    assert len(check.payload["input_projection_digest"]) == 64
    assert eligibility.check_evidence_digests == tuple(
        sorted(
            evidence.content_digest
            for evidence in observation.evidence
            if evidence.kind == "check"
        )
    )
    assert (
        verifier.verify(
            observation.result_claim,
            work_node["output_contract"],
            observation,
        ).status
        == "accepted"
    )


def _runtime_config() -> dict:
    return {
        "role_profiles": {
            "reviewer_standard": {
                "provider": "codex",
                "settings": {
                    "model": "gpt-5.6-sol",
                    "thinkingOptionId": "high",
                    "modeId": "full-access",
                    "features": {},
                },
            },
            "reviewer_recovery": {
                "provider": "codex",
                "settings": {
                    "model": "gpt-5.6-sol",
                    "thinkingOptionId": "max",
                    "modeId": "full-access",
                    "features": {},
                },
            },
            "reviewer_strict": {
                "provider": "codex",
                "settings": {
                    "model": "gpt-5.6-sol",
                    "thinkingOptionId": "max",
                    "modeId": "full-access",
                    "features": {},
                },
            },
        },
        "review_profiles": {
            "standard_axis": "reviewer_standard",
            "recovery_axis": "reviewer_recovery",
            "strict_specialist": "reviewer_strict",
        },
        "repositories": {},
    }


def test_default_config_exposes_review_profile_selectors_and_repo_override():
    config = orch_core.default_config()

    assert config["review_profiles"] == {
        "standard_axis": "reviewer_standard",
        "recovery_axis": "reviewer_recovery",
        "strict_specialist": "reviewer_strict",
    }
    orch_core.validate_config(config)
    config["role_profiles"]["repo-review"] = {
        "provider": "codex",
        "settings": {
            "model": "repo-sol",
            "thinkingOptionId": "high",
            "modeId": "full-access",
            "features": {},
        },
    }
    config["repositories"]["local/phase-three"] = {
        "review_profiles": {"standard_axis": "repo-review"},
        "role_profiles": {
            "repo-review": config["role_profiles"]["repo-review"],
        },
    }

    profile = resolve_review_profile(
        config,
        repository="local/phase-three",
        selector="standard_axis",
    )

    assert profile.name == "repo-review"
    assert profile.model == "repo-sol"


def test_runtime_profile_fallback_v8_review_is_explicitly_rejected():
    config = _runtime_config()
    config["role_profiles"]["reviewer_standard"]["fallback"] = {
        "provider": "codex",
        "settings": {
            "model": "gpt-5.6-sol",
            "thinkingOptionId": "max",
            "modeId": "full-access",
            "features": {},
        },
    }

    with pytest.raises(RuntimeAdapterError) as unsupported:
        resolve_review_profile(
            config,
            repository="local/phase-three",
            selector="standard_axis",
        )

    assert unsupported.value.code == "REVIEW_PROFILE_FALLBACK_UNSUPPORTED"
    assert "availability-aware selection before Review child dispatch" in str(
        unsupported.value
    )


def _review_axis_request(
    worker_binding,
    observation,
    *,
    axis: str,
    recovery_ordinal: int = 0,
) -> ReviewAxisRequest:
    return ReviewAxisRequest(
        repository="local/phase-three",
        attempt_id=observation.result_claim.attempt_id,
        candidate_sha=observation.result_claim.candidate_sha,
        base_sha=_git(Path(worker_binding.repository_path), "rev-parse", "main"),
        axis=axis,
        recovery_ordinal=recovery_ordinal,
        workspace=Path(worker_binding.workspace),
        diff_command=("git", "diff", "main...HEAD"),
        commit_list=("candidate for phase three",),
        spec_source_ref="synthetic://issue/45",
        spec_text="result.txt must contain phase-3",
        standards_sources=("AGENTS.md",),
        check_manifest_digest=hashlib.sha256(
            "".join(
                sorted(
                    item.content_digest
                    for item in observation.evidence
                    if item.kind == "check"
                )
            ).encode("utf-8")
        ).hexdigest(),
    )


def test_runtime_materializes_cross_provider_typed_review_axis(tmp_path):
    work_node, _worker_runtime, worker_binding, observation = _execute_candidate(
        tmp_path,
        risk="standard",
    )
    candidate_sha = observation.result_claim.candidate_sha
    profile = resolve_review_profile(
        _runtime_config(),
        repository="local/phase-three",
        selector="standard_axis",
    )
    request = _review_axis_request(
        worker_binding,
        observation,
        axis="standards",
    )
    client = InMemoryPaseoClient()
    adapter = PaseoRuntimeAdapter(client)

    binding = adapter.materialize_review_axis(
        request,
        profile,
        parent_agent_id=worker_binding.agent_id,
    )

    assert binding.provider == "codex"
    assert binding.model == "gpt-5.6-sol"
    assert binding.thinking == "high"
    assert binding.parent_agent_id == worker_binding.agent_id
    assert client.create_count == 1
    assert client.inspect(binding.agent_id).labels["gwo.review_axis"] == "standards"
    assert "transcript" not in request.to_prompt().text
    output_protocol = json.loads(request.to_prompt().text)["output_protocol"]
    assert output_protocol["schema_version"] == 1
    assert f'"action_key":"{request.action_key}"' in output_protocol["instruction"]
    assert output_protocol["instruction"].startswith(
        "End with exactly one compact JSON line"
    )

    client.set_output(
        binding.agent_id,
        "GWO_REVIEW_AXIS "
        + json.dumps(
            {
                "schema_version": 1,
                "action_key": request.action_key,
                "candidate_sha": candidate_sha,
                "axis": "standards",
                "fixed_input_digest": request.fixed_input_digest,
                "findings": [
                    {
                        "severity": "advisory",
                        "code": "possible-mysterious-name",
                        "source": "Fowler smell baseline",
                        "location": "result.txt:1",
                        "message": "Synthetic advisory for the contract test.",
                    }
                ],
            },
            separators=(",", ":"),
        ),
    )
    axis = adapter.observe_review_axis(request, binding)

    assert axis.lifecycle == "completed"
    assert axis.axis == "standards"
    assert axis.candidate_sha == candidate_sha
    assert axis.fixed_input_digest == request.fixed_input_digest
    assert axis.profile_digest == profile.digest
    assert axis.findings[0]["severity"] == "advisory"
    assert len(axis.output_digest) == 64


def test_repair_packet_accepts_two_valid_axis_envelopes(tmp_path):
    work_node, worker_runtime, worker_binding, observation = _execute_candidate(
        tmp_path,
        risk="standard",
    )
    profile = resolve_review_profile(
        _runtime_config(),
        repository="local/phase-three",
        selector="standard_axis",
    )
    client = InMemoryPaseoClient()
    adapter = PaseoRuntimeAdapter(client)
    axis_observations = []
    for axis_name in ("standards", "spec"):
        request = _review_axis_request(
            worker_binding,
            observation,
            axis=axis_name,
        )
        binding = adapter.materialize_review_axis(
            request,
            profile,
            parent_agent_id=worker_binding.agent_id,
        )
        envelope = {
            "schema_version": 1,
            "action_key": request.action_key,
            "candidate_sha": request.candidate_sha,
            "axis": axis_name,
            "fixed_input_digest": request.fixed_input_digest,
            "findings": [],
        }
        while True:
            envelope["findings"].append(
                {
                    "severity": "hard",
                    "code": "x",
                    "source": "x",
                    "location": "x",
                    "message": "x",
                }
            )
            encoded_envelope = json.dumps(
                envelope,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )
            if len(encoded_envelope.encode("utf-8")) > 16_384:
                envelope["findings"].pop()
                break
        encoded_envelope = json.dumps(
            envelope,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        envelope["findings"][-1]["message"] += "x" * (
            16_384 - len(encoded_envelope.encode("utf-8"))
        )
        encoded_envelope = json.dumps(
            envelope,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        assert len(encoded_envelope.encode("utf-8")) == 16_384
        assert len(envelope["findings"]) >= 200
        client.set_output(
            binding.agent_id,
            f"GWO_REVIEW_AXIS {encoded_envelope}",
        )
        axis_observations.append(adapter.observe_review_axis(request, binding))

    causes = [
        {
            "type": "review_blocker",
            "axis": axis.axis,
            "finding": dict(finding),
        }
        for axis in axis_observations
        for finding in axis.findings
    ]
    changed_files = []
    changed_file_index = 0
    while True:
        candidate_files = [*changed_files, f"路径-{changed_file_index}"]
        encoded_files = json.dumps(
            candidate_files,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        if len(encoded_files) > 4 * 1024:
            break
        changed_files = candidate_files
        changed_file_index += 1
    assert len(
        json.dumps(
            changed_files,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ) > 4_000
    ladder = RecoveryLadder(semantic_attempts=2, repair_rounds=1)
    packet = ladder.recovery_packet(
        candidate_sha=observation.result_claim.candidate_sha,
        acceptance_digest="b" * 64,
        changed_files=changed_files,
        causes=causes,
    )

    packet_value = json.loads(packet)
    assert packet_value["causes"] == causes
    assert packet_value["changed_files"] == changed_files
    assert packet_value["acceptance_digest"] == "b" * 64
    assert 55 * 1024 < len(packet.encode("utf-8")) <= 64 * 1024
    store_path = tmp_path / "repair-prompt.sqlite3"
    kernel = Kernel(
        store_path=store_path,
        publication=LocalPlanPublication(store_path),
        runtime=worker_runtime,
        verifier=EvidenceVerifier(),
        repository_path=Path(worker_binding.repository_path),
        integration_branch="main",
        writer_generation="phase-3",
    )
    repair_prompt = kernel._recovery_prompt(
        work_node,
        packet,
        same_attempt=True,
    )
    repair_round = json.loads(repair_prompt.text)["repair_round"]
    assert repair_round["causes"] == causes
    assert repair_round["changed_files"] == changed_files
    assert repair_round["acceptance_digest"] == "b" * 64
    assert len(repair_prompt.text.encode("utf-8")) <= 64 * 1024

    with pytest.raises(KernelError) as overlong_path:
        ladder.recovery_packet(
            candidate_sha="a" * 40,
            acceptance_digest="b" * 64,
            changed_files=["a.py", "x" * 257, "b.py"],
            causes=causes,
        )
    assert overlong_path.value.code == "REPAIR_CHANGED_FILES_TOO_LARGE"

    with pytest.raises(KernelError) as oversized_file_list:
        ladder.recovery_packet(
            candidate_sha="a" * 40,
            acceptance_digest="b" * 64,
            changed_files=[*changed_files, f"路径-{changed_file_index}"],
            causes=causes,
        )
    assert oversized_file_list.value.code == "REPAIR_CHANGED_FILES_TOO_LARGE"

    boundary_cause = {
        "type": "review_blocker",
        "axis": "standards",
        "finding": {
            "severity": "hard",
            "code": "EXACT_PACKET_BOUNDARY",
            "source": "CONTEXT.md",
            "location": "result.txt:1",
            "message": "x",
        },
    }
    boundary_seed = ladder.recovery_packet(
        candidate_sha="a" * 40,
        acceptance_digest="b" * 64,
        changed_files=["result.txt"],
        causes=[boundary_cause],
    )
    boundary_cause["finding"]["message"] += "x" * (
        64 * 1024 - len(boundary_seed.encode("utf-8"))
    )
    boundary_packet = ladder.recovery_packet(
        candidate_sha="a" * 40,
        acceptance_digest="b" * 64,
        changed_files=["result.txt"],
        causes=[boundary_cause],
    )
    assert len(boundary_packet.encode("utf-8")) == 64 * 1024
    assert (
        json.loads(boundary_packet)["causes"][0]["finding"]["message"]
        == boundary_cause["finding"]["message"]
    )

    boundary_cause["finding"]["message"] += "x"
    with pytest.raises(KernelError) as raised:
        ladder.recovery_packet(
            candidate_sha="a" * 40,
            acceptance_digest="b" * 64,
            changed_files=["result.txt"],
            causes=[boundary_cause],
        )
    assert raised.value.code == "RECOVERY_PACKET_TOO_LARGE"


def test_review_axis_materialization_is_one_execution_and_readback_first(tmp_path):
    _node, _worker_runtime, worker_binding, observation = _execute_candidate(
        tmp_path,
        risk="standard",
    )
    request = _review_axis_request(
        worker_binding,
        observation,
        axis="standards",
    )
    profile = resolve_review_profile(
        _runtime_config(),
        repository="local/phase-three",
        selector="standard_axis",
    )
    transient_client = InMemoryPaseoClient(create_failures=("transient", "transient"))
    adapter = PaseoRuntimeAdapter(transient_client)
    for expected_count in (1, 2):
        with pytest.raises(RuntimeAdapterError) as transient:
            adapter.materialize_review_axis(
                request,
                profile,
                parent_agent_id=worker_binding.agent_id,
            )
        assert transient.value.code == "PASEO_CREATE_TRANSIENT"
        assert transient.value.failure_class == "transient"
        assert transient_client.create_count == expected_count
    binding = adapter.materialize_review_axis(
        request,
        profile,
        parent_agent_id=worker_binding.agent_id,
    )
    assert transient_client.create_count == 3
    assert binding.action_key == request.action_key

    ambiguous_client = InMemoryPaseoClient(create_failures=("ambiguous_after_create",))
    adopted = PaseoRuntimeAdapter(ambiguous_client).materialize_review_axis(
        request,
        profile,
        parent_agent_id=worker_binding.agent_id,
    )
    assert ambiguous_client.create_count == 1
    assert adopted.action_key == request.action_key

    permanent_client = InMemoryPaseoClient(create_failures=("permanent",))
    with pytest.raises(RuntimeAdapterError) as rejected:
        PaseoRuntimeAdapter(permanent_client).materialize_review_axis(
            request,
            profile,
            parent_agent_id=worker_binding.agent_id,
        )
    assert rejected.value.failure_class == "permanent"
    assert permanent_client.create_count == 1


class _ReviewOrphanTrackingPaseoClient(InMemoryPaseoClient):
    def __init__(self):
        super().__init__()
        self.archived_agent_ids = []
        self.cleaned_action_keys = []

    def archive(self, agent_id):
        self.archived_agent_ids.append(agent_id)
        super().archive(agent_id)

    def cleanup_orphan_worktree(self, action_key, _repository_path):
        self.cleaned_action_keys.append(action_key)


def test_review_axis_materialization_blocks_conflicting_orphan_without_cleanup(
    tmp_path,
):
    _node, _worker_runtime, worker_binding, observation = _execute_candidate(
        tmp_path,
        risk="standard",
    )
    request = _review_axis_request(
        worker_binding,
        observation,
        axis="standards",
    )
    profile = resolve_review_profile(
        _runtime_config(),
        repository="local/phase-three",
        selector="standard_axis",
    )
    client = _ReviewOrphanTrackingPaseoClient()
    orphan_prompt = RuntimePrompt(text="orphan", digest="f" * 64)
    orphan = client.create(
        PaseoCreateRequest(
            action_key=request.action_key,
            title="orphan",
            labels={
                "gwo.action_key": request.action_key,
                "gwo.review_candidate": "0" * 40,
            },
            prompt=orphan_prompt,
            repository_path=str(request.workspace),
            base_sha=request.candidate_sha,
            profile=profile,
            parent_agent_id=worker_binding.agent_id,
        )
    )

    with pytest.raises(RuntimeAdapterError) as blocked:
        PaseoRuntimeAdapter(client).materialize_review_axis(
            request,
            profile,
            parent_agent_id=worker_binding.agent_id,
        )

    assert blocked.value.code == "REVIEW_AXIS_IDENTITY_CONFLICT"
    assert blocked.value.failure_class == "permanent"
    assert client.archived_agent_ids == []
    assert client.cleaned_action_keys == []
    assert client.inspect(orphan.agent_id).archived is False


def test_review_axis_materialization_repairs_accepted_agent_labels_without_reprompt(
    tmp_path,
):
    _node, _worker_runtime, worker_binding, observation = _execute_candidate(
        tmp_path,
        risk="standard",
    )
    request = _review_axis_request(
        worker_binding,
        observation,
        axis="standards",
    )
    profile = resolve_review_profile(
        _runtime_config(),
        repository="local/phase-three",
        selector="standard_axis",
    )
    client = InMemoryPaseoClient()
    prompt = request.to_prompt()
    incomplete = client.create(
        PaseoCreateRequest(
            action_key=request.action_key,
            title="incomplete accepted Review child",
            labels={"gwo.action_key": request.action_key},
            prompt=prompt,
            repository_path=str(request.workspace),
            base_sha=request.candidate_sha,
            profile=profile,
            parent_agent_id=worker_binding.agent_id,
        )
    )

    binding = PaseoRuntimeAdapter(client).materialize_review_axis(
        request,
        profile,
        parent_agent_id=worker_binding.agent_id,
    )

    repaired = client.inspect(incomplete.agent_id)
    assert binding.agent_id == incomplete.agent_id
    assert repaired.labels["gwo.review_candidate"] == request.candidate_sha
    assert repaired.labels["gwo.review_axis"] == request.axis
    assert repaired.labels["gwo.prompt_digest"] == prompt.digest
    assert client.create_count == 1
    assert client.send_count == 0
    assert client.prompt_acceptance_count(binding.agent_id, prompt) == 1


class _AmbiguousCreateWithoutIdentityPaseoClient(
    _ReviewOrphanTrackingPaseoClient
):
    def __init__(self):
        super().__init__()
        self.fail_ambiguously = True

    def create(self, request):
        if self.fail_ambiguously:
            self.fail_ambiguously = False
            self.create_count += 1
            raise RuntimeAdapterError(
                "PASEO_CREATE_AMBIGUOUS",
                "synthetic create lost before Agent identity readback",
                failure_class="ambiguous",
            )
        return super().create(request)


def test_review_axis_materialization_ambiguous_create_never_cleans_without_orphan_proof(
    tmp_path,
):
    _node, _worker_runtime, worker_binding, observation = _execute_candidate(
        tmp_path,
        risk="standard",
    )
    request = _review_axis_request(
        worker_binding,
        observation,
        axis="standards",
    )
    profile = resolve_review_profile(
        _runtime_config(),
        repository="local/phase-three",
        selector="standard_axis",
    )
    client = _AmbiguousCreateWithoutIdentityPaseoClient()
    adapter = PaseoRuntimeAdapter(client)

    with pytest.raises(RuntimeAdapterError) as ambiguous:
        adapter.materialize_review_axis(
            request,
            profile,
            parent_agent_id=worker_binding.agent_id,
        )

    assert ambiguous.value.code == "PASEO_CREATE_AMBIGUOUS"
    assert client.cleaned_action_keys == []
    binding = adapter.materialize_review_axis(
        request,
        profile,
        parent_agent_id=worker_binding.agent_id,
    )
    assert binding.action_key == request.action_key
    assert client.create_count == 2
    assert client.cleaned_action_keys == []


def test_review_axis_materialization_tls_transport_is_transient_but_config_is_permanent():
    permanent_failures = (
        "TLS certificate verify failed",
        "certificate validation failed for local issuer",
        "unauthorized provider authentication",
        "invalid configuration: unknown model",
        "unknown provider kimi-x",
    )
    transient_failures = (
        "TLS connect timeout",
        "TLS certificate handshake timed out",
        "TLS wrong version number",
        "connection reset by peer",
    )

    assert {
        PaseoCliClient.classify_failure(message, default="transient")
        for message in permanent_failures
    } == {"permanent"}
    assert {
        PaseoCliClient.classify_failure(message, default="permanent")
        for message in transient_failures
    } == {"transient"}


def test_windows_environment_requirement_resolves_logical_paseo_executable(
    tmp_path,
    monkeypatch,
):
    resolved = r"C:\Users\test\.local\bin\paseo.cmd"
    commands = []

    monkeypatch.setattr("gwo_v8.runtime.sys.platform", "win32")
    monkeypatch.setattr(
        "gwo_v8.runtime.shutil.which",
        lambda executable: resolved if executable == "paseo" else None,
    )

    def run(command, *, cwd):
        commands.append((command, cwd))
        return SimpleNamespace(returncode=0, stdout="paseo 1.2.3\n", stderr="")

    monkeypatch.setattr("gwo_v8.runtime._run", run)

    snapshot = _environment_snapshot(("paseo",), cwd=tmp_path)

    assert snapshot["platform"] == "win32"
    assert snapshot["paseo"] == {
        "executable": resolved,
        "version": "paseo 1.2.3",
    }
    assert commands == [([resolved, "--version"], tmp_path)]


class _DelayedReviewPromptPaseoClient(InMemoryPaseoClient):
    def __init__(self, previous=None):
        if previous is None:
            super().__init__()
            self.sent_action_keys = []
            self.pending_prompts = {}
        else:
            self._agents = previous._agents
            self._create_failures = previous._create_failures
            self._send_acceptances = previous._send_acceptances
            self._accepted_prompt_digests = previous._accepted_prompt_digests
            self._worker_turn_capacity = previous._worker_turn_capacity
            self.create_count = previous.create_count
            self.send_count = previous.send_count
            self.create_prompt_digests = previous.create_prompt_digests
            self.sent_action_keys = previous.sent_action_keys
            self.pending_prompts = previous.pending_prompts
        self.ack_receipt_visible = False

    def find_by_labels(self, labels):
        delivery = labels.get("gwo.prompt_delivery")
        if (
            isinstance(delivery, str)
            and delivery.startswith("acked:")
            and not self.ack_receipt_visible
        ):
            return ()
        return super().find_by_labels(labels)

    def create(self, request):
        record = super().create(request)
        if "gwo.review_axis" not in request.labels:
            return record
        self._accepted_prompt_digests[record.agent_id] = []
        self._agents[record.agent_id] = replace(record, lifecycle="idle")
        return self._agents[record.agent_id]

    def send_prompt(self, agent_id, prompt, *, action_key):
        axis = self.inspect(agent_id).labels.get("gwo.review_axis")
        if axis is None:
            return super().send_prompt(
                agent_id,
                prompt,
                action_key=action_key,
            )
        self.sent_action_keys.append(action_key)
        self.send_count += 1
        self.pending_prompts[axis] = (agent_id, prompt, action_key)
        self._agents[agent_id] = replace(
            self.inspect(agent_id),
            lifecycle="idle",
        )

    def expose_prompt_boundary(self):
        for agent_id, prompt, _action_key in self.pending_prompts.values():
            self._accepted_prompt_digests[agent_id].append(prompt.digest)
            self._agents[agent_id] = replace(
                self.inspect(agent_id),
                lifecycle="running",
            )


def test_review_prompt_ambiguity_survives_delayed_visibility_windows(
    tmp_path,
    monkeypatch,
):
    repository = _temporary_repository(tmp_path)
    intent = _plan_intent(risk="standard")
    intent["goals"][0]["acceptance"] = [
        "delayed Review transport authority " * 512
    ]
    compiled = PlanCompiler().compile(
        intent,
        _ready_source(),
        _policy_snapshot(),
    )
    store_path = tmp_path / "v8.sqlite3"
    publication = LocalPlanPublication(store_path)
    publication.publish_and_activate(
        compiled,
        expected_active_digest=None,
        writer_generation="phase-3",
    )
    client = _DelayedReviewPromptPaseoClient()
    kernel = Kernel(
        store_path=store_path,
        publication=publication,
        runtime=PaseoRuntimeAdapter(client),
        verifier=EvidenceVerifier(),
        repository_path=repository,
        integration_branch="main",
        writer_generation="phase-3",
        runtime_profile=RuntimeProfile(
            name="worker-standard",
            provider="kimi-cli",
            model="kimi-code/kimi-for-coding",
            thinking="max",
            mode="yolo",
            features={},
        ),
        frontier_runtime_profile=RuntimeProfile(
            name="worker-frontier",
            provider="codex",
            model="gpt-5.6-sol",
            thinking="xhigh",
            mode="full-access",
            features={},
        ),
        runtime_config=_runtime_config(),
        delivery_control=InMemoryDeliveryControl(hosted_outcomes=("passed",)),
    )
    waiting_for_worker = kernel.reconcile_once("local/phase-three")
    worker = client.find_by_labels(
        {"gwo.admission": waiting_for_worker.admission_id}
    )[0]
    workspace = Path(worker.workspace)
    subprocess.run(
        [
            "git",
            "-C",
            str(repository),
            "worktree",
            "add",
            "--detach",
            str(workspace),
            "HEAD",
        ],
        check=True,
        capture_output=True,
    )
    (workspace / "result.txt").write_text("phase-3\n", encoding="utf-8")
    _git(workspace, "add", "result.txt")
    _git(workspace, "commit", "-m", "delayed Review Candidate")
    candidate_sha = _git(workspace, "rev-parse", "HEAD")
    client.set_output(
        worker.agent_id,
        "GWO_RESULT "
        + json.dumps(
            {
                "schema_version": 1,
                "action_key": waiting_for_worker.node_key,
                "candidate_sha": candidate_sha,
            },
            separators=(",", ":"),
        ),
    )
    clock = {"tick": 0}

    def monotonic():
        clock["tick"] += 1
        return float(clock["tick"])

    monkeypatch.setattr("gwo_v8.runtime.time.monotonic", monotonic)
    monkeypatch.setattr("gwo_v8.runtime.time.sleep", lambda _seconds: None)
    monkeypatch.setattr("gwo_v8.runtime.PASEO_BOOTSTRAP_WAIT_SECONDS", 3.0)

    ambiguous = [kernel.reconcile_once("local/phase-three")]
    client = _DelayedReviewPromptPaseoClient(client)
    kernel.runtime = PaseoRuntimeAdapter(client)
    ambiguous.extend(
        kernel.reconcile_once("local/phase-three")
        for _ in range(3)
    )
    client.ack_receipt_visible = True
    ambiguous.append(kernel.reconcile_once("local/phase-three"))

    assert len(ambiguous) == 5
    assert all(outcome.status == "waiting" for outcome in ambiguous)
    assert all(
        outcome.wait_condition == "review_prompt_readback"
        for outcome in ambiguous
    )
    assert all(outcome.attempt_state == "reviewing" for outcome in ambiguous)
    review_agents = client.find_by_labels(
        {"gwo.review_candidate": candidate_sha}
    )
    assert {
        agent.labels["gwo.review_axis"] for agent in review_agents
    } == {"standards", "spec"}
    assert len(
        {agent.labels["gwo.action_key"] for agent in review_agents}
    ) == 2
    assert client.create_count == 1 + len(review_agents)
    assert client.send_count == len(review_agents)
    assert len(client.sent_action_keys) == len(review_agents)
    assert len(set(client.sent_action_keys)) == len(review_agents)
    assert set(client.pending_prompts) == {"standards", "spec"}
    standards_action_key = client.pending_prompts["standards"][2]
    assert {
        outcome.wait_source_ref for outcome in ambiguous
    } == {
        f"paseo://review/{candidate_sha}/action/{standards_action_key}"
    }
    assert {
        outcome.wait_event_identity for outcome in ambiguous
    } == {
        f"{standards_action_key}:prompt_readback"
    }
    assert all(
        len(prompt.text.encode("utf-8")) > PASEO_INLINE_PROMPT_MAX_BYTES
        for _agent_id, prompt, _action_key in client.pending_prompts.values()
    )
    client.expose_prompt_boundary()
    adopted = kernel.reconcile_once("local/phase-three")

    assert adopted.status == "waiting"
    assert adopted.wait_condition == "review_axis"
    review_agents = client.find_by_labels(
        {"gwo.review_candidate": candidate_sha}
    )
    for axis in ("standards", "spec"):
        agent = next(
            agent
            for agent in review_agents
            if agent.labels["gwo.review_axis"] == axis
        )
        agent_id, prompt, action_key = client.pending_prompts[axis]
        assert agent.agent_id == agent_id
        assert agent.labels["gwo.action_key"] == action_key
        assert client.prompt_acceptance_count(agent_id, prompt) == 1
    assert client.create_count == 3
    assert client.send_count == 2


class _DelayedInlinePaseoClient(InMemoryPaseoClient):
    def __init__(self):
        super().__init__()
        self.created_prompt = None

    def create(self, request):
        record = super().create(request)
        self.created_prompt = request.prompt
        self._accepted_prompt_digests[record.agent_id] = []
        self._agents[record.agent_id] = replace(
            record,
            lifecycle="idle",
        )
        return self._agents[record.agent_id]

    def expose_created_prompt(self):
        record = next(iter(self._agents.values()))
        self._accepted_prompt_digests[record.agent_id].append(
            self.created_prompt.digest
        )


def test_prompt_ambiguity_does_not_exhaust_materialization(
    tmp_path,
    monkeypatch,
):
    repository = _temporary_repository(tmp_path)
    compiled = PlanCompiler().compile(
        _plan_intent(),
        _ready_source(),
        _policy_snapshot(),
    )
    work_node = next(
        node
        for node in json.loads(compiled.canonical_bytes)["nodes"]
        if node["kind"] == "work"
    )
    prompt = RuntimePrompt.from_node(work_node)
    assert len(prompt.text.encode("utf-8")) <= PASEO_INLINE_PROMPT_MAX_BYTES
    store_path = tmp_path / "v8.sqlite3"
    publication = LocalPlanPublication(store_path)
    publication.publish_and_activate(
        compiled,
        expected_active_digest=None,
        writer_generation="phase-3",
    )
    client = _DelayedInlinePaseoClient()
    kernel = Kernel(
        store_path=store_path,
        publication=publication,
        runtime=PaseoRuntimeAdapter(client),
        verifier=EvidenceVerifier(),
        repository_path=repository,
        integration_branch="main",
        writer_generation="phase-3",
        runtime_profile=RuntimeProfile(
            name="worker-standard",
            provider="kimi-cli",
            model="kimi-code/kimi-for-coding",
            thinking="max",
            mode="yolo",
            features={},
        ),
    )
    monkeypatch.setattr("gwo_v8.runtime.PASEO_BOOTSTRAP_WAIT_SECONDS", 0.0)

    ambiguous = [
        kernel.reconcile_once("local/phase-three")
        for _ in range(5)
    ]

    assert all(outcome.status == "waiting" for outcome in ambiguous)
    assert all(
        outcome.admission_state == "materialization_ambiguous"
        for outcome in ambiguous
    )
    assert {outcome.materialization_executions for outcome in ambiguous} == {2}
    assert client.create_count == 1
    assert client.send_count == 0

    client.expose_created_prompt()
    adopted = kernel.reconcile_once("local/phase-three")

    assert adopted.status != "blocked"
    assert adopted.attempt_id is not None
    assert adopted.materialization_executions == 2
    assert client.create_count == 1
    assert client.send_count == 0


def test_dual_axis_review_evidence_preserves_axes_and_unlocks_publication(
    tmp_path,
):
    work_node, _worker_runtime, worker_binding, observation = _execute_candidate(
        tmp_path,
        risk="standard",
    )
    client = InMemoryPaseoClient()
    adapter = PaseoRuntimeAdapter(client)
    profile = resolve_review_profile(
        _runtime_config(),
        repository="local/phase-three",
        selector="standard_axis",
    )
    axis_observations = []
    requests = []
    for axis_name in ("standards", "spec"):
        request = _review_axis_request(
            worker_binding,
            observation,
            axis=axis_name,
        )
        requests.append(request)
        binding = adapter.materialize_review_axis(
            request,
            profile,
            parent_agent_id=worker_binding.agent_id,
        )
        client.set_output(
            binding.agent_id,
            "GWO_REVIEW_AXIS "
            + json.dumps(
                {
                    "schema_version": 1,
                    "action_key": request.action_key,
                    "candidate_sha": request.candidate_sha,
                    "axis": axis_name,
                    "fixed_input_digest": request.fixed_input_digest,
                    "findings": [],
                },
                separators=(",", ":"),
            ),
        )
        axis_observations.append(adapter.observe_review_axis(request, binding))

    gate = EvidenceVerifier().assemble_review_evidence(
        observation.result_claim,
        work_node["output_contract"]["review_requirement"],
        tuple(axis_observations),
        acceptance_digest=requests[0].spec_digest,
        check_manifest_digest=requests[0].check_manifest_digest,
        observer_id=worker_binding.runtime_id,
    )

    assert gate.status == "accepted"
    assert gate.missing_axes == ()
    assert gate.blockers == ()
    assert gate.evidence is not None
    assert [record["axis"] for record in gate.evidence.payload["axes"]] == [
        "standards",
        "spec",
    ]
    assert all("summary" not in record for record in gate.evidence.payload["axes"])
    reviewed = replace(
        observation,
        evidence=observation.evidence + (gate.evidence,),
    )
    eligibility = EvidenceVerifier().publication_eligibility(
        observation.result_claim,
        work_node["output_contract"],
        reviewed,
    )
    assert eligibility.eligible is True
    assert eligibility.review_evidence_digest == gate.evidence.content_digest


def test_kernel_runs_standard_review_inside_candidate_work_attempt(tmp_path):
    repository = _temporary_repository(tmp_path)
    compiled = PlanCompiler().compile(
        _plan_intent(risk="standard"),
        _ready_source(),
        _policy_snapshot(),
    )
    store_path = tmp_path / "v8.sqlite3"
    publication = LocalPlanPublication(store_path)
    publication.publish_and_activate(
        compiled,
        expected_active_digest=None,
        writer_generation="phase-3",
    )
    client = InMemoryPaseoClient()
    kernel = Kernel(
        store_path=store_path,
        publication=publication,
        runtime=PaseoRuntimeAdapter(client),
        verifier=EvidenceVerifier(),
        repository_path=repository,
        integration_branch="main",
        writer_generation="phase-3",
        runtime_profile=RuntimeProfile(
            name="worker-standard",
            provider="kimi-cli",
            model="kimi-code/kimi-for-coding",
            thinking="max",
            mode="yolo",
            features={},
        ),
        frontier_runtime_profile=RuntimeProfile(
            name="worker-frontier",
            provider="codex",
            model="gpt-5.6-sol",
            thinking="xhigh",
            mode="full-access",
            features={},
        ),
        runtime_config=_runtime_config(),
        delivery_control=InMemoryDeliveryControl(hosted_outcomes=("passed",)),
    )

    waiting_for_worker = kernel.reconcile_once("local/phase-three")
    worker = client.find_by_labels({"gwo.admission": waiting_for_worker.admission_id})[
        0
    ]
    workspace = Path(worker.workspace)
    subprocess.run(
        [
            "git",
            "-C",
            str(repository),
            "worktree",
            "add",
            "--detach",
            str(workspace),
            "HEAD",
        ],
        check=True,
        capture_output=True,
    )
    (workspace / "result.txt").write_text("phase-3\n", encoding="utf-8")
    _git(workspace, "add", "result.txt")
    _git(workspace, "commit", "-m", "phase three Candidate")
    candidate_sha = _git(workspace, "rev-parse", "HEAD")
    client.set_output(
        worker.agent_id,
        "GWO_RESULT "
        + json.dumps(
            {
                "schema_version": 1,
                "action_key": waiting_for_worker.node_key,
                "candidate_sha": candidate_sha,
            },
            separators=(",", ":"),
        ),
    )

    waiting_for_review = kernel.reconcile_once("local/phase-three")

    assert waiting_for_review.wait_condition == "review_axis"
    assert waiting_for_review.active_worker_turns == 1
    review_agents = client.find_by_labels({"gwo.review_candidate": candidate_sha})
    assert {agent.labels["gwo.review_axis"] for agent in review_agents} == {
        "standards",
        "spec",
    }
    assert all(agent.parent_agent_id == worker.agent_id for agent in review_agents)
    assert len(review_agents) == 2
    for agent in review_agents:
        findings = [] if agent.labels["gwo.review_axis"] == "spec" else None
        payload = {
            "schema_version": 1,
            "action_key": agent.labels["gwo.action_key"],
            "candidate_sha": candidate_sha,
            "axis": agent.labels["gwo.review_axis"],
            "fixed_input_digest": agent.labels["gwo.review_input"],
        }
        if findings is not None:
            payload["findings"] = findings
        client.set_output(
            agent.agent_id,
            "GWO_REVIEW_AXIS " + json.dumps(payload, separators=(",", ":")),
        )

    recovery_wait = kernel.reconcile_once("local/phase-three")

    assert recovery_wait.wait_condition == "review_axis"
    all_review_agents = client.find_by_labels({"gwo.review_candidate": candidate_sha})
    recovery = next(
        agent
        for agent in all_review_agents
        if agent.labels["gwo.review_axis"] == "standards" and agent.thinking == "max"
    )
    assert (
        len(
            [
                agent
                for agent in all_review_agents
                if agent.labels["gwo.review_axis"] == "spec"
            ]
        )
        == 1
    )
    client.set_output(
        recovery.agent_id,
        "GWO_REVIEW_AXIS "
        + json.dumps(
            {
                "schema_version": 1,
                "action_key": recovery.labels["gwo.action_key"],
                "candidate_sha": candidate_sha,
                "axis": "standards",
                "fixed_input_digest": recovery.labels["gwo.review_input"],
                "findings": [],
            },
            separators=(",", ":"),
        ),
    )

    completed = kernel.reconcile_once("local/phase-three")

    assert completed.status == "complete"
    assert completed.attempt_id == waiting_for_worker.attempt_id
    assert completed.attempt_state == "verified"
    assert completed.candidate_sha == candidate_sha
    assert client.create_count == 4
    assert client.inspect(worker.agent_id).archived is True
    assert all(
        client.inspect(agent.agent_id).archived is True for agent in all_review_agents
    )


def test_kernel_publishes_once_waits_for_exact_sha_ci_then_integrates(
    tmp_path,
):
    repository = _temporary_repository(tmp_path)
    compiled = PlanCompiler().compile(
        _plan_intent(),
        _ready_source(),
        _policy_snapshot(),
    )
    store_path = tmp_path / "v8.sqlite3"
    publication = LocalPlanPublication(store_path)
    publication.publish_and_activate(
        compiled,
        expected_active_digest=None,
        writer_generation="phase-3",
    )
    runtime = InMemoryRuntimeAdapter(tmp_path / "runtime")
    delivery = InMemoryDeliveryControl(hosted_outcomes=("pending", "passed"))
    kernel = Kernel(
        store_path=store_path,
        publication=publication,
        runtime=runtime,
        verifier=EvidenceVerifier(),
        repository_path=repository,
        integration_branch="main",
        writer_generation="phase-3",
        delivery_control=delivery,
    )
    base_sha = _git(repository, "rev-parse", "HEAD")

    waiting = kernel.reconcile_once("local/phase-three")

    assert waiting.status == "waiting"
    assert waiting.wait_condition == "hosted_ci"
    assert waiting.publication_eligible is True
    assert waiting.publication_state == "published"
    assert waiting.hosted_check_state == "pending"
    assert _git(repository, "rev-parse", "HEAD") == base_sha
    candidate_sha = waiting.candidate_sha
    assert delivery.publication_count == 1
    assert delivery.published_candidate_sha == candidate_sha

    completed = kernel.reconcile_once("local/phase-three")

    assert completed.status == "complete"
    assert completed.candidate_sha == candidate_sha
    assert completed.hosted_check_state == "passed"
    assert delivery.publication_count == 1
    assert delivery.hosted_read_candidates == [candidate_sha, candidate_sha]
    assert delivery.integrated_candidates == [candidate_sha]
    assert _git(repository, "rev-parse", "HEAD") == candidate_sha


def test_v3_delivery_requirement_fails_closed_without_delivery_control(
    tmp_path,
):
    repository = _temporary_repository(tmp_path)
    compiled = PlanCompiler().compile(
        _plan_intent(),
        _ready_source(),
        _policy_snapshot(),
    )
    store_path = tmp_path / "v8.sqlite3"
    publication = LocalPlanPublication(store_path)
    publication.publish_and_activate(
        compiled,
        expected_active_digest=None,
        writer_generation="phase-3",
    )
    kernel = Kernel(
        store_path=store_path,
        publication=publication,
        runtime=InMemoryRuntimeAdapter(tmp_path / "runtime"),
        verifier=EvidenceVerifier(),
        repository_path=repository,
        integration_branch="main",
        writer_generation="phase-3",
    )

    blocked = kernel.reconcile_once("local/phase-three")

    assert blocked.status == "blocked"
    assert blocked.attempt_state == "delivery_control_missing"
    assert blocked.publication_state is None


def test_recovery_ladder_is_bounded_and_runtime_loss_never_fails_node():
    ladder = RecoveryLadder(semantic_attempts=2, repair_rounds=1)

    assert (
        ladder.decide(
            terminal_reason="rejected",
            attempt_ordinal=1,
            repair_rounds_used=0,
        ).action
        == "repair_same_attempt"
    )
    assert (
        ladder.decide(
            terminal_reason="rejected",
            attempt_ordinal=1,
            repair_rounds_used=1,
        ).action
        == "start_frontier_attempt"
    )
    assert (
        ladder.decide(
            terminal_reason="no_result",
            attempt_ordinal=2,
            repair_rounds_used=0,
        ).action
        == "repair_same_attempt"
    )
    exhausted = ladder.decide(
        terminal_reason="no_result",
        attempt_ordinal=2,
        repair_rounds_used=1,
    )
    assert exhausted.action == "fail_plan_node"
    assert exhausted.consumes_semantic_attempt is True

    runtime_lost = ladder.decide(
        terminal_reason="runtime_lost",
        attempt_ordinal=1,
        repair_rounds_used=1,
    )
    assert runtime_lost.action == "block_runtime_unavailable"
    assert runtime_lost.consumes_semantic_attempt is False
    assert runtime_lost.plan_node_failed is False

    packet = ladder.recovery_packet(
        candidate_sha="a" * 40,
        acceptance_digest="b" * 64,
        changed_files=["result.txt"],
        causes=[
            {
                "type": "runtime_no_result",
                "messages": ["x" * 20_000],
            }
        ],
    )
    assert len(packet.encode("utf-8")) <= 64 * 1024
    assert "full_transcript" not in packet
    assert json.loads(packet)["acceptance_digest"] == "b" * 64

    review_causes = [
        {
            "type": "review_blocker",
            "axis": "standards" if index % 2 == 0 else "spec",
            "finding": {
                "severity": "hard",
                "code": f"F{index:02d}",
                "source": "CONTEXT.md",
                "location": f"result.txt:{index + 1}",
                "message": f"blocking finding {index}",
            },
        }
        for index in range(65)
    ]
    review_packet = ladder.recovery_packet(
        candidate_sha="a" * 40,
        acceptance_digest="b" * 64,
        changed_files=["result.txt"],
        causes=review_causes,
    )
    assert json.loads(review_packet)["causes"] == review_causes
    assert len(review_packet.encode("utf-8")) <= 64 * 1024


def test_terminal_worker_without_result_enters_one_bounded_repair_round(
    tmp_path,
):
    repository = _temporary_repository(tmp_path)
    compiled = PlanCompiler().compile(
        _plan_intent(),
        _ready_source(),
        _policy_snapshot(),
    )
    store_path = tmp_path / "v8.sqlite3"
    publication = LocalPlanPublication(store_path)
    publication.publish_and_activate(
        compiled,
        expected_active_digest=None,
        writer_generation="phase-3",
    )
    client = _RepairCapturePaseoClient()
    kernel = Kernel(
        store_path=store_path,
        publication=publication,
        runtime=PaseoRuntimeAdapter(client),
        verifier=EvidenceVerifier(),
        repository_path=repository,
        integration_branch="main",
        writer_generation="phase-3",
        runtime_profile=RuntimeProfile(
            name="worker",
            provider="kimi-cli",
            model="kimi-code/kimi-for-coding",
            thinking="max",
            mode="yolo",
            features={},
        ),
    )

    first = kernel.reconcile_once("local/phase-three")
    worker = client.find_by_labels({"gwo.admission": first.admission_id})[0]
    client.stop(worker.agent_id)
    resumed = kernel.reconcile_once("local/phase-three")
    assert resumed.attempt_state == "running"
    client.stop(worker.agent_id)

    repaired = kernel.reconcile_once("local/phase-three")

    assert repaired.attempt_id == first.attempt_id
    assert repaired.attempt_state == "repairing"
    assert repaired.repair_rounds_used == 1
    assert repaired.wait_condition == "runtime_result"
    repair_round = json.loads(client.repair_prompts[0][1].text)["repair_round"]
    assert repair_round["candidate_sha"] == ""
    assert repair_round["changed_files"] == []


def test_paseo_runtime_captures_bounded_typed_no_result(tmp_path):
    repository = _temporary_repository(tmp_path)
    work_node = _compiled_work_node()
    admission = RuntimeAdmission(
        repository="local/phase-three",
        plan_digest="a" * 64,
        node_key=work_node["node_key"],
        admission_id="admission:typed-no-result",
        repository_path=repository,
        base_sha=_git(repository, "rev-parse", "HEAD"),
        runtime_profile=RuntimeProfile(
            name="worker",
            provider="kimi-cli",
            model="kimi-code/kimi-for-coding",
            thinking="max",
            mode="yolo",
            features={},
        ),
    )
    client = InMemoryPaseoClient()
    runtime = PaseoRuntimeAdapter(client)
    binding = runtime.materialize(admission, RuntimePrompt.from_node(work_node))
    binding = runtime.attach_attempt(binding, "attempt:typed-no-result:1")
    client.set_output(
        binding.agent_id,
        "GWO_RESULT "
        + json.dumps(
            {
                "schema_version": 1,
                "action_key": work_node["node_key"],
                "terminal_reason": "no_result",
                "reason": "The requested API is absent from this checkout.",
            },
            separators=(",", ":"),
        ),
    )

    observation = runtime.observe(binding)

    assert observation.result_claim is None
    assert observation.terminal_reason == "no_result"
    assert observation.terminal_detail.startswith("The requested API")
    assert observation.evidence[0].payload["terminal_reason"] == "no_result"


def test_invalid_worker_result_consumes_no_result_recovery_immediately(
    tmp_path,
):
    repository = _temporary_repository(tmp_path)
    compiled = PlanCompiler().compile(
        _plan_intent(),
        _ready_source(),
        _policy_snapshot(),
    )
    store_path = tmp_path / "v8.sqlite3"
    publication = LocalPlanPublication(store_path)
    publication.publish_and_activate(
        compiled,
        expected_active_digest=None,
        writer_generation="phase-3",
    )
    client = InMemoryPaseoClient()
    kernel = Kernel(
        store_path=store_path,
        publication=publication,
        runtime=PaseoRuntimeAdapter(client),
        verifier=EvidenceVerifier(),
        repository_path=repository,
        integration_branch="main",
        writer_generation="phase-3",
        runtime_profile=RuntimeProfile(
            name="worker",
            provider="kimi-cli",
            model="kimi-code/kimi-for-coding",
            thinking="max",
            mode="yolo",
            features={},
        ),
        delivery_control=InMemoryDeliveryControl(hosted_outcomes=("passed",)),
    )
    waiting = kernel.reconcile_once("local/phase-three")
    worker = client.find_by_labels({"gwo.admission": waiting.admission_id})[0]
    client.set_output(
        worker.agent_id,
        "GWO_RESULT "
        + json.dumps(
            {
                "schema_version": 1,
                "action_key": waiting.node_key,
                "candidate_sha": "not-a-sha",
            },
            separators=(",", ":"),
        ),
    )

    repaired = kernel.reconcile_once("local/phase-three")

    assert repaired.attempt_state == "repairing"
    assert repaired.repair_rounds_used == 1
    assert repaired.attempt_ordinal == 1


class _ObservationLossAdapter:
    adapter_name = "observation-loss"

    def __init__(self, delegate):
        self.delegate = delegate

    def __getattr__(self, name):
        return getattr(self.delegate, name)

    def observe(self, _binding):
        raise RuntimeAdapterError(
            "RUNTIME_CONNECTION_LOST",
            "runtime observation is unavailable",
            failure_class="transient",
        )


def test_repeated_runtime_loss_blocks_without_consuming_semantic_attempt(
    tmp_path,
):
    repository = _temporary_repository(tmp_path)
    compiled = PlanCompiler().compile(
        _plan_intent(),
        _ready_source(),
        _policy_snapshot(),
    )
    store_path = tmp_path / "v8.sqlite3"
    publication = LocalPlanPublication(store_path)
    publication.publish_and_activate(
        compiled,
        expected_active_digest=None,
        writer_generation="phase-3",
    )
    runtime = _ObservationLossAdapter(InMemoryRuntimeAdapter(tmp_path / "runtime"))
    kernel = Kernel(
        store_path=store_path,
        publication=publication,
        runtime=runtime,
        verifier=EvidenceVerifier(),
        repository_path=repository,
        integration_branch="main",
        writer_generation="phase-3",
    )

    assert kernel.reconcile_once("local/phase-three").status == "waiting"
    assert kernel.reconcile_once("local/phase-three").status == "waiting"
    blocked = kernel.reconcile_once("local/phase-three")

    assert blocked.status == "blocked"
    assert blocked.wait_condition == "runtime_available"
    assert blocked.attempt_terminal_reason == "runtime_lost"
    assert blocked.attempt_ordinal == 1
    assert blocked.repair_rounds_used == 0


def test_hosted_infrastructure_failure_retries_twice_then_blocks(tmp_path):
    repository = _temporary_repository(tmp_path)
    compiled = PlanCompiler().compile(
        _plan_intent(),
        _ready_source(),
        _policy_snapshot(),
    )
    store_path = tmp_path / "v8.sqlite3"
    publication = LocalPlanPublication(store_path)
    publication.publish_and_activate(
        compiled,
        expected_active_digest=None,
        writer_generation="phase-3",
    )
    delivery = InMemoryDeliveryControl(
        hosted_outcomes=(
            "infrastructure_failure",
            "infrastructure_failure",
            "infrastructure_failure",
        )
    )
    kernel = Kernel(
        store_path=store_path,
        publication=publication,
        runtime=InMemoryRuntimeAdapter(tmp_path / "runtime"),
        verifier=EvidenceVerifier(),
        repository_path=repository,
        integration_branch="main",
        writer_generation="phase-3",
        delivery_control=delivery,
    )

    first = kernel.reconcile_once("local/phase-three")
    second = kernel.reconcile_once("local/phase-three")
    blocked = kernel.reconcile_once("local/phase-three")

    assert first.hosted_retry_count == 1
    assert second.hosted_retry_count == 2
    assert blocked.status == "blocked"
    assert blocked.hosted_check_state == "infrastructure_failure"
    assert delivery.hosted_retry_count == 2


def test_hosted_cancellation_waits_without_rejecting_candidate(tmp_path):
    repository = _temporary_repository(tmp_path)
    compiled = PlanCompiler().compile(
        _plan_intent(),
        _ready_source(),
        _policy_snapshot(),
    )
    store_path = tmp_path / "v8.sqlite3"
    publication = LocalPlanPublication(store_path)
    publication.publish_and_activate(
        compiled,
        expected_active_digest=None,
        writer_generation="phase-3",
    )
    kernel = Kernel(
        store_path=store_path,
        publication=publication,
        runtime=InMemoryRuntimeAdapter(tmp_path / "runtime"),
        verifier=EvidenceVerifier(),
        repository_path=repository,
        integration_branch="main",
        writer_generation="phase-3",
        delivery_control=InMemoryDeliveryControl(hosted_outcomes=("cancelled",)),
    )

    waiting = kernel.reconcile_once("local/phase-three")

    assert waiting.status == "waiting"
    assert waiting.directive == "request_decision"
    assert waiting.attempt_state == "batch_wait"
    assert waiting.wait_condition == "hosted_ci_cancelled"
    assert waiting.attempt_ordinal == 1


class _AmbiguousPublicationDelivery(InMemoryDeliveryControl):
    def __init__(self):
        super().__init__(hosted_outcomes=("passed",))
        self.publish_attempts = 0

    def publish_once(
        self,
        repository,
        candidate_sha,
        evidence_manifest_digest,
        *,
        target_branch=None,
    ):
        self.publish_attempts += 1
        if self.publish_attempts == 1:
            raise DeliveryControlError(
                "PULL_REQUEST_CREATE_AMBIGUOUS",
                "pull request creation has no authoritative readback yet",
            )
        return super().publish_once(
            repository,
            candidate_sha,
            evidence_manifest_digest,
            target_branch=target_branch,
        )


def test_kernel_reconciles_again_after_ambiguous_publication_readback(tmp_path):
    repository = _temporary_repository(tmp_path)
    compiled = PlanCompiler().compile(
        _plan_intent(),
        _ready_source(),
        _policy_snapshot(),
    )
    store_path = tmp_path / "v8.sqlite3"
    publication = LocalPlanPublication(store_path)
    publication.publish_and_activate(
        compiled,
        expected_active_digest=None,
        writer_generation="phase-3",
    )
    delivery = _AmbiguousPublicationDelivery()
    kernel = Kernel(
        store_path=store_path,
        publication=publication,
        runtime=InMemoryRuntimeAdapter(tmp_path / "runtime"),
        verifier=EvidenceVerifier(),
        repository_path=repository,
        integration_branch="main",
        writer_generation="phase-3",
        delivery_control=delivery,
    )

    waiting = kernel.reconcile_once("local/phase-three")

    assert waiting.status == "waiting"
    assert waiting.directive == "reconcile_again"
    assert waiting.attempt_state == "batch_wait"
    assert waiting.wait_condition == "publication_readback"
    assert waiting.wait_event_identity.startswith("publication-readback:")

    completed = kernel.reconcile_once("local/phase-three")

    assert completed.status == "complete"
    assert delivery.publish_attempts == 2


class _FakeGitHubDelivery(GitHubCliDeliveryControl):
    def __init__(self, repository_path: Path, candidate_sha: str):
        super().__init__(repository_path=repository_path)
        self.candidate_sha = candidate_sha
        self.published = False
        self.manifest_digest = None
        self.manifest_state = None
        self.reruns = []
        self.runs = []
        self.jobs = {}
        self.pull_requests = []
        self.pull_request_create_returncode = 0
        self.pull_request_visible_after_failed_create = False
        self.pull_request_visibility_delay = 0
        self.pull_request_reads = 0
        self.pull_request_read_failures = set()
        self.manifest_visibility_delay = 0
        self.manifest_reads = 0

    @staticmethod
    def _wait_for_publication_readback():
        return None

    def _command(self, command):
        returncode = 0
        stdout = ""
        if command[:2] == ["git", "ls-remote"]:
            if self.published:
                stdout = (
                    f"{self.candidate_sha}\t"
                    f"refs/heads/gwo/candidates/{self.candidate_sha}\n"
                )
            else:
                returncode = 2
        elif command[:2] == ["git", "push"]:
            self.published = True
        elif command[:2] == ["git", "fetch"]:
            pass
        elif command[:2] == ["git", "merge-base"]:
            pass
        elif (
            command[:3] == ["gh", "api", "--method"]
            and "/statuses/" in command[4]
        ):
            self.manifest_digest = next(
                item.split("=", 1)[1]
                for item in command
                if item.startswith("description=")
            )
            self.manifest_state = next(
                item.split("=", 1)[1]
                for item in command
                if item.startswith("state=")
            )
        elif (
            command[:4] == ["gh", "api", "--method", "GET"]
            and command[4].endswith("/pulls")
        ):
            self.pull_request_reads += 1
            if self.pull_request_reads in self.pull_request_read_failures:
                returncode = 1
                stderr = "temporary pull request readback failure"
            elif self.pull_request_reads > self.pull_request_visibility_delay:
                stdout = json.dumps(self.pull_requests)
            else:
                stdout = json.dumps([])
        elif (
            command[:4] == ["gh", "api", "--method", "POST"]
            and command[4].endswith("/pulls")
        ):
            if self.pull_requests:
                returncode = 1
                stderr = "a pull request for this Candidate already exists"
            else:
                returncode = self.pull_request_create_returncode
                if (
                    returncode == 0
                    or self.pull_request_visible_after_failed_create
                ):
                    fields = {
                        item.split("=", 1)[0]: item.split("=", 1)[1]
                        for item in command
                        if "=" in item
                    }
                    pull_request = {
                        "state": "open",
                        "merged_at": None,
                        "html_url": "https://github.invalid/pull/1",
                        "head": {
                            "ref": fields["head"],
                            "sha": self.candidate_sha,
                        },
                        "base": {"ref": fields["base"]},
                    }
                    self.pull_requests.append(pull_request)
                    if returncode == 0:
                        stdout = json.dumps(pull_request)
                    else:
                        stderr = "connection reset after create"
                else:
                    stderr = "connection reset after create"
        elif (
            command[:4] == ["gh", "api", "--method", "GET"]
            and "/actions/runs/" in command[4]
            and command[4].endswith("/jobs")
        ):
            run_id = int(command[4].split("/runs/", 1)[1].split("/", 1)[0])
            stdout = json.dumps({"jobs": self.jobs.get(run_id, [])})
        elif command[:2] == ["gh", "api"]:
            self.manifest_reads += 1
            statuses = []
            if (
                self.manifest_digest is not None
                and self.manifest_reads > self.manifest_visibility_delay
            ):
                statuses.append(
                    {
                        "context": self.evidence_context,
                        "description": self.manifest_digest,
                        "state": self.manifest_state,
                    }
                )
            stdout = json.dumps({"statuses": statuses})
        elif command[:3] == ["gh", "run", "list"]:
            stdout = json.dumps(self.runs)
        elif command[:3] == ["gh", "run", "rerun"]:
            self.reruns.append(command[3])
        else:
            raise AssertionError(command)
        return subprocess.CompletedProcess(
            command,
            returncode,
            stdout,
            locals().get("stderr", ""),
        )


def test_github_delivery_retries_eventually_consistent_publication_readback(
    tmp_path,
):
    repository = _temporary_repository(tmp_path)
    candidate_sha = _git(repository, "rev-parse", "HEAD")
    delivery = _FakeGitHubDelivery(repository, candidate_sha)
    delivery.manifest_visibility_delay = 2

    publication = delivery.publish_once(
        "owner/repository",
        candidate_sha,
        "e" * 64,
    )

    assert publication.candidate_sha == candidate_sha
    assert publication.evidence_manifest_digest == "e" * 64
    assert delivery.manifest_reads == 3


def test_github_delivery_converges_exact_candidate_pull_request_and_status(
    tmp_path,
):
    repository = _temporary_repository(tmp_path)
    candidate_sha = _git(repository, "rev-parse", "HEAD")
    delivery = _FakeGitHubDelivery(repository, candidate_sha)

    first = delivery.publish_once(
        "owner/repository",
        candidate_sha,
        "e" * 64,
        target_branch="dev",
    )
    adopted = delivery.publish_once(
        "owner/repository",
        candidate_sha,
        "e" * 64,
        target_branch="dev",
    )

    assert first == adopted
    assert first.source_ref == "https://github.invalid/pull/1"
    assert delivery.manifest_state == "success"
    assert len(delivery.pull_requests) == 1


def test_github_delivery_adopts_pull_request_after_ambiguous_create(tmp_path):
    repository = _temporary_repository(tmp_path)
    candidate_sha = _git(repository, "rev-parse", "HEAD")
    delivery = _FakeGitHubDelivery(repository, candidate_sha)
    delivery.pull_request_create_returncode = 1
    delivery.pull_request_visible_after_failed_create = True

    publication = delivery.publish_once(
        "owner/repository",
        candidate_sha,
        "e" * 64,
        target_branch="dev",
    )

    assert publication.source_ref == "https://github.invalid/pull/1"
    assert len(delivery.pull_requests) == 1


def test_github_delivery_retries_eventually_consistent_pull_request_readback(
    tmp_path,
):
    repository = _temporary_repository(tmp_path)
    candidate_sha = _git(repository, "rev-parse", "HEAD")
    delivery = _FakeGitHubDelivery(repository, candidate_sha)
    delivery.pull_request_visibility_delay = 2

    with pytest.raises(DeliveryControlError) as ambiguous:
        delivery.publish_once(
            "owner/repository",
            candidate_sha,
            "e" * 64,
            target_branch="dev",
        )
    publication = delivery.publish_once(
        "owner/repository",
        candidate_sha,
        "e" * 64,
        target_branch="dev",
    )

    assert ambiguous.value.code == "PULL_REQUEST_CREATE_AMBIGUOUS"
    assert publication.source_ref == "https://github.invalid/pull/1"
    assert len(delivery.pull_requests) == 1
    assert delivery.pull_request_reads == 3


def test_github_delivery_retries_transient_pull_request_readback_failure(
    tmp_path,
):
    repository = _temporary_repository(tmp_path)
    candidate_sha = _git(repository, "rev-parse", "HEAD")
    delivery = _FakeGitHubDelivery(repository, candidate_sha)
    delivery.pull_request_read_failures = {2}

    with pytest.raises(DeliveryControlError) as ambiguous:
        delivery.publish_once(
            "owner/repository",
            candidate_sha,
            "e" * 64,
            target_branch="dev",
        )
    publication = delivery.publish_once(
        "owner/repository",
        candidate_sha,
        "e" * 64,
        target_branch="dev",
    )

    assert ambiguous.value.code == "PULL_REQUEST_CREATE_AMBIGUOUS"
    assert publication.source_ref == "https://github.invalid/pull/1"
    assert len(delivery.pull_requests) == 1
    assert delivery.pull_request_reads == 3


def test_github_delivery_adopts_pull_request_after_bounded_readback_exhaustion(
    tmp_path,
):
    repository = _temporary_repository(tmp_path)
    candidate_sha = _git(repository, "rev-parse", "HEAD")
    delivery = _FakeGitHubDelivery(repository, candidate_sha)
    delivery.pull_request_visibility_delay = 6

    for _attempt in range(3):
        with pytest.raises(DeliveryControlError) as ambiguous:
            delivery.publish_once(
                "owner/repository",
                candidate_sha,
                "e" * 64,
                target_branch="dev",
            )
        assert ambiguous.value.code == "PULL_REQUEST_CREATE_AMBIGUOUS"
    publication = delivery.publish_once(
        "owner/repository",
        candidate_sha,
        "e" * 64,
        target_branch="dev",
    )

    assert publication.source_ref == "https://github.invalid/pull/1"
    assert len(delivery.pull_requests) == 1
    assert delivery.pull_request_reads == 7


def test_github_delivery_surfaces_initial_pull_request_readback_as_ambiguous(
    tmp_path,
):
    repository = _temporary_repository(tmp_path)
    candidate_sha = _git(repository, "rev-parse", "HEAD")
    delivery = _FakeGitHubDelivery(repository, candidate_sha)
    delivery.pull_request_read_failures = {1}

    with pytest.raises(DeliveryControlError) as ambiguous:
        delivery.publish_once(
            "owner/repository",
            candidate_sha,
            "e" * 64,
            target_branch="dev",
        )

    assert ambiguous.value.code == "PULL_REQUEST_READBACK_AMBIGUOUS"
    assert delivery.pull_requests == []


def test_github_delivery_fails_closed_on_candidate_pull_request_conflict(
    tmp_path,
):
    repository = _temporary_repository(tmp_path)
    candidate_sha = _git(repository, "rev-parse", "HEAD")
    delivery = _FakeGitHubDelivery(repository, candidate_sha)
    delivery.pull_requests = [
        {
            "state": "open",
            "merged_at": None,
            "html_url": "https://github.invalid/pull/1",
            "head": {
                "ref": f"gwo/candidates/{candidate_sha}",
                "sha": candidate_sha,
            },
            "base": {"ref": "main"},
        }
    ]

    with pytest.raises(DeliveryControlError) as captured:
        delivery.publish_once(
            "owner/repository",
            candidate_sha,
            "e" * 64,
            target_branch="dev",
        )

    assert captured.value.code == "PULL_REQUEST_IDENTITY_CONFLICT"


def test_github_delivery_treats_timeout_as_candidate_failure(tmp_path):
    repository = _temporary_repository(tmp_path)
    candidate_sha = _git(repository, "rev-parse", "HEAD")
    delivery = _FakeGitHubDelivery(repository, candidate_sha)

    publication = delivery.publish_once(
        "owner/repository",
        candidate_sha,
        "e" * 64,
    )
    delivery.runs = [
        {
            "databaseId": 42,
            "status": "completed",
            "conclusion": "timed_out",
            "url": "https://github.invalid/actions/runs/42",
            "headSha": candidate_sha,
        }
    ]
    hosted = delivery.read_hosted_checks("owner/repository", candidate_sha)
    integrated = delivery.integrate_serially(
        "owner/repository",
        candidate_sha,
        "main",
    )

    assert publication.candidate_sha == candidate_sha
    assert publication.evidence_manifest_digest == "e" * 64
    assert hosted.status == "code_failure"
    assert delivery.reruns == []
    assert integrated.candidate_sha == candidate_sha
    assert integrated.target_branch == "main"


def test_github_delivery_retries_explicit_startup_failure(tmp_path):
    repository = _temporary_repository(tmp_path)
    candidate_sha = _git(repository, "rev-parse", "HEAD")
    delivery = _FakeGitHubDelivery(repository, candidate_sha)
    delivery.runs = [
        {
            "databaseId": 43,
            "status": "completed",
            "conclusion": "startup_failure",
            "url": "https://github.invalid/actions/runs/43",
            "headSha": candidate_sha,
        }
    ]

    hosted = delivery.read_hosted_checks("owner/repository", candidate_sha)
    delivery.retry_hosted_checks("owner/repository", candidate_sha)

    assert hosted.status == "infrastructure_failure"
    assert delivery.reruns == ["43"]


def test_github_delivery_reports_cancellation_without_candidate_verdict(tmp_path):
    repository = _temporary_repository(tmp_path)
    candidate_sha = _git(repository, "rev-parse", "HEAD")
    delivery = _FakeGitHubDelivery(repository, candidate_sha)
    delivery.runs = [
        {
            "databaseId": 44,
            "status": "completed",
            "conclusion": "cancelled",
            "url": "https://github.invalid/actions/runs/44",
            "headSha": candidate_sha,
        }
    ]

    hosted = delivery.read_hosted_checks("owner/repository", candidate_sha)

    assert hosted.status == "cancelled"
    assert delivery.reruns == []


@pytest.mark.parametrize(
    "other_conclusion",
    ["cancelled", "startup_failure"],
)
def test_github_delivery_prioritizes_candidate_failure_in_mixed_runs(
    tmp_path,
    other_conclusion,
):
    repository = _temporary_repository(tmp_path)
    candidate_sha = _git(repository, "rev-parse", "HEAD")
    delivery = _FakeGitHubDelivery(repository, candidate_sha)
    delivery.runs = [
        {
            "databaseId": 45,
            "status": "completed",
            "conclusion": "failure",
            "url": "https://github.invalid/actions/runs/45",
            "headSha": candidate_sha,
        },
        {
            "databaseId": 46,
            "status": "completed",
            "conclusion": other_conclusion,
            "url": "https://github.invalid/actions/runs/46",
            "headSha": candidate_sha,
        },
    ]

    hosted = delivery.read_hosted_checks("owner/repository", candidate_sha)

    assert hosted.status == "code_failure"
    assert delivery.reruns == []


def test_github_delivery_recovers_partial_publication_and_matches_check_name(
    tmp_path,
):
    repository = _temporary_repository(tmp_path)
    candidate_sha = _git(repository, "rev-parse", "HEAD")
    delivery = _FakeGitHubDelivery(repository, candidate_sha)
    delivery.published = True

    recovered = delivery.publish_once(
        "owner/repository",
        candidate_sha,
        "f" * 64,
    )
    required = (
        {
            "check_id": "hosted-required",
            "hosted_name": "Required CI",
            "definition_digest": "d" * 64,
        },
    )
    delivery.runs = [
        {
            "databaseId": 50,
            "status": "completed",
            "conclusion": "success",
            "url": "https://github.invalid/actions/runs/50",
            "headSha": candidate_sha,
            "name": "Unrelated CI",
            "workflowName": "Unrelated CI",
        }
    ]
    missing = delivery.read_hosted_checks(
        "owner/repository",
        candidate_sha,
        required,
    )
    delivery.runs[0]["name"] = "Required CI"
    delivery.runs[0]["workflowName"] = "Required CI"
    passed = delivery.read_hosted_checks(
        "owner/repository",
        candidate_sha,
        required,
    )

    assert recovered.evidence_manifest_digest == "f" * 64
    assert missing.status == "pending"
    assert passed.status == "passed"
    assert passed.definition_digests == ("d" * 64,)


def test_github_delivery_matches_exact_workflow_job_name(tmp_path):
    repository = _temporary_repository(tmp_path)
    candidate_sha = _git(repository, "rev-parse", "HEAD")
    delivery = _FakeGitHubDelivery(repository, candidate_sha)
    delivery.runs = [
        {
            "databaseId": 51,
            "status": "completed",
            "conclusion": "success",
            "url": "https://github.invalid/actions/runs/51",
            "headSha": candidate_sha,
            "name": "GWO CI",
            "workflowName": "GWO CI",
        }
    ]
    delivery.jobs[51] = [
        {
            "status": "completed",
            "conclusion": "success",
            "html_url": "https://github.invalid/actions/runs/51/job/1",
            "head_sha": candidate_sha,
            "name": "acceptance",
        }
    ]
    required = (
        {
            "check_id": "hosted-required",
            "hosted_name": "GWO CI / acceptance",
            "definition_digest": "d" * 64,
        },
    )

    hosted = delivery.read_hosted_checks(
        "owner/repository",
        candidate_sha,
        required,
    )

    assert hosted.status == "passed"
    assert hosted.source_ref == "https://github.invalid/actions/runs/51/job/1"
    assert hosted.definition_digests == ("d" * 64,)


def test_github_delivery_does_not_match_combined_name_at_workflow_level(
    tmp_path,
):
    repository = _temporary_repository(tmp_path)
    candidate_sha = _git(repository, "rev-parse", "HEAD")
    delivery = _FakeGitHubDelivery(repository, candidate_sha)
    delivery.runs = [
        {
            "databaseId": 51,
            "status": "completed",
            "conclusion": "success",
            "url": "https://github.invalid/actions/runs/51",
            "headSha": candidate_sha,
            "name": "GWO CI / acceptance",
            "workflowName": "GWO CI / acceptance",
        }
    ]
    required = (
        {
            "check_id": "hosted-required",
            "hosted_name": "GWO CI / acceptance",
            "definition_digest": "d" * 64,
        },
    )

    hosted = delivery.read_hosted_checks(
        "owner/repository",
        candidate_sha,
        required,
    )

    assert hosted.status == "pending"


def test_github_delivery_does_not_match_job_from_another_sha(tmp_path):
    repository = _temporary_repository(tmp_path)
    candidate_sha = _git(repository, "rev-parse", "HEAD")
    delivery = _FakeGitHubDelivery(repository, candidate_sha)
    delivery.runs = [
        {
            "databaseId": 52,
            "status": "completed",
            "conclusion": "success",
            "url": "https://github.invalid/actions/runs/52",
            "headSha": candidate_sha,
            "name": "GWO CI",
            "workflowName": "GWO CI",
        }
    ]
    delivery.jobs[52] = [
        {
            "status": "completed",
            "conclusion": "success",
            "html_url": "https://github.invalid/actions/runs/52/job/1",
            "head_sha": "f" * 40,
            "name": "acceptance",
        }
    ]
    required = (
        {
            "check_id": "hosted-required",
            "hosted_name": "GWO CI / acceptance",
            "definition_digest": "d" * 64,
        },
    )

    hosted = delivery.read_hosted_checks(
        "owner/repository",
        candidate_sha,
        required,
    )

    assert hosted.status == "pending"


def test_github_delivery_classifies_exact_job_failure(tmp_path):
    repository = _temporary_repository(tmp_path)
    candidate_sha = _git(repository, "rev-parse", "HEAD")
    delivery = _FakeGitHubDelivery(repository, candidate_sha)
    delivery.runs = [
        {
            "databaseId": 53,
            "status": "completed",
            "conclusion": "failure",
            "url": "https://github.invalid/actions/runs/53",
            "headSha": candidate_sha,
            "name": "GWO CI",
            "workflowName": "GWO CI",
        }
    ]
    delivery.jobs[53] = [
        {
            "status": "completed",
            "conclusion": "failure",
            "html_url": "https://github.invalid/actions/runs/53/job/1",
            "head_sha": candidate_sha,
            "name": "acceptance",
        }
    ]
    required = (
        {
            "check_id": "hosted-required",
            "hosted_name": "GWO CI / acceptance",
            "definition_digest": "d" * 64,
        },
    )

    hosted = delivery.read_hosted_checks(
        "owner/repository",
        candidate_sha,
        required,
    )

    assert hosted.status == "code_failure"


def test_kernel_reuses_store_persisted_checks_after_adapter_restart(tmp_path):
    repository = _temporary_repository(tmp_path)
    compiled = PlanCompiler().compile(
        _plan_intent(),
        _ready_source(),
        _policy_snapshot(),
    )
    store_path = tmp_path / "v8.sqlite3"
    publication = LocalPlanPublication(store_path)
    publication.publish_and_activate(
        compiled,
        expected_active_digest=None,
        writer_generation="phase-3",
    )
    client = InMemoryPaseoClient()
    first_adapter = PaseoRuntimeAdapter(client)
    delivery = InMemoryDeliveryControl(hosted_outcomes=("pending", "passed"))
    kernel = Kernel(
        store_path=store_path,
        publication=publication,
        runtime=first_adapter,
        verifier=EvidenceVerifier(),
        repository_path=repository,
        integration_branch="main",
        writer_generation="phase-3",
        runtime_profile=RuntimeProfile(
            name="worker",
            provider="kimi-cli",
            model="kimi-code/kimi-for-coding",
            thinking="max",
            mode="yolo",
            features={},
        ),
        delivery_control=delivery,
    )
    worker_wait = kernel.reconcile_once("local/phase-three")
    worker = client.find_by_labels({"gwo.admission": worker_wait.admission_id})[0]
    workspace = Path(worker.workspace)
    subprocess.run(
        [
            "git",
            "-C",
            str(repository),
            "worktree",
            "add",
            "--detach",
            str(workspace),
            "HEAD",
        ],
        check=True,
        capture_output=True,
    )
    (workspace / "result.txt").write_text("phase-3\n", encoding="utf-8")
    _git(workspace, "add", "result.txt")
    _git(workspace, "commit", "-m", "restart-safe Candidate")
    candidate_sha = _git(workspace, "rev-parse", "HEAD")
    client.set_output(
        worker.agent_id,
        "GWO_RESULT "
        + json.dumps(
            {
                "schema_version": 1,
                "action_key": worker_wait.node_key,
                "candidate_sha": candidate_sha,
            },
            separators=(",", ":"),
        ),
    )
    pending = kernel.reconcile_once("local/phase-three")
    assert pending.wait_condition == "hosted_ci"
    persisted = kernel._read_state("local/phase-three", compiled.digest)
    persisted_check_digest = next(
        item["content_digest"]
        for item in persisted["candidate_observation"]["evidence"]
        if item["kind"] == "check"
    )

    restarted_adapter = PaseoRuntimeAdapter(client)
    restarted_kernel = Kernel(
        store_path=store_path,
        publication=publication,
        runtime=restarted_adapter,
        verifier=EvidenceVerifier(),
        repository_path=repository,
        integration_branch="main",
        writer_generation="phase-3",
        runtime_profile=RuntimeProfile(
            name="worker",
            provider="kimi-cli",
            model="kimi-code/kimi-for-coding",
            thinking="max",
            mode="yolo",
            features={},
        ),
        delivery_control=delivery,
    )

    completed = restarted_kernel.reconcile_once("local/phase-three")

    assert completed.status == "complete"
    completed_state = restarted_kernel._read_state(
        "local/phase-three",
        compiled.digest,
    )
    assert (
        next(
            item["content_digest"]
            for item in completed_state["candidate_observation"]["evidence"]
            if item["kind"] == "check"
        )
        == persisted_check_digest
    )


def test_changed_candidate_review_request_carries_bounded_prior_context(
    tmp_path,
):
    _node, _runtime, binding, observation = _execute_candidate(
        tmp_path,
        risk="standard",
    )
    base_sha = _git(Path(binding.workspace), "rev-parse", "HEAD^")
    request = Kernel._review_request(
        state={
            "repository": "local/phase-three",
            "base_sha": base_sha,
            "prior_review_context": {
                "candidate_sha": base_sha,
                "findings": [
                    {
                        "severity": "hard",
                        "code": "OLD",
                        "message": "prior blocker",
                    }
                ],
            },
        },
        goal={"acceptance": ["result.txt contains phase-3"]},
        work_item={
            "source_ref": "synthetic://issue/45",
            "outcome_contract": {"path": "result.txt"},
        },
        binding=binding,
        observation=observation,
        axis="standards",
        recovery_ordinal=0,
    )

    assert request.prior_findings[0]["code"] == "OLD"
    assert request.candidate_delta is not None
    assert "result.txt" in request.candidate_delta
    assert len(request.candidate_delta) <= 4000


def test_review_request_references_large_outcome_contents_by_digest(tmp_path):
    _node, _runtime, binding, observation = _execute_candidate(
        tmp_path,
        risk="standard",
    )
    content = "large frozen source payload\n" * 20_000
    request = Kernel._review_request(
        state={
            "repository": "local/phase-three",
            "base_sha": _git(Path(binding.workspace), "rev-parse", "HEAD^"),
        },
        goal={"acceptance": ["materialize the exact declared file"]},
        work_item={
            "source_ref": "synthetic://issue/large",
            "outcome_contract": {
                "file_changes": [
                    {"path": "result.txt", "content": content},
                ]
            },
        },
        binding=binding,
        observation=observation,
        axis="spec",
        recovery_ordinal=0,
    )

    spec = json.loads(request.spec_text)
    reference = spec["outcome_contract"]["file_changes"][0]
    assert reference == {
        "path": "result.txt",
        "content_digest": hashlib.sha256(content.encode("utf-8")).hexdigest(),
        "content_bytes": len(content.encode("utf-8")),
    }
    assert content not in request.to_prompt().text


def test_strict_review_adds_specialist_then_waits_for_bound_human_decision(
    tmp_path,
):
    repository = _temporary_repository(tmp_path)
    policy = _policy_snapshot()
    policy["strict_review"]["specialist_requirements"] = ["security"]
    compiled = PlanCompiler().compile(
        _plan_intent(risk="strict"),
        _ready_source(),
        policy,
    )
    store_path = tmp_path / "v8.sqlite3"
    publication = LocalPlanPublication(store_path)
    publication.publish_and_activate(
        compiled,
        expected_active_digest=None,
        writer_generation="phase-3",
    )
    client = InMemoryPaseoClient()
    kernel = Kernel(
        store_path=store_path,
        publication=publication,
        runtime=PaseoRuntimeAdapter(client),
        verifier=EvidenceVerifier(),
        repository_path=repository,
        integration_branch="main",
        writer_generation="phase-3",
        runtime_profile=RuntimeProfile(
            name="worker",
            provider="kimi-cli",
            model="kimi-code/kimi-for-coding",
            thinking="max",
            mode="yolo",
            features={},
        ),
        runtime_config=_runtime_config(),
        delivery_control=InMemoryDeliveryControl(hosted_outcomes=("passed",)),
    )
    worker_wait = kernel.reconcile_once("local/phase-three")
    worker = client.find_by_labels({"gwo.admission": worker_wait.admission_id})[0]
    workspace = Path(worker.workspace)
    subprocess.run(
        [
            "git",
            "-C",
            str(repository),
            "worktree",
            "add",
            "--detach",
            str(workspace),
            "HEAD",
        ],
        check=True,
        capture_output=True,
    )
    (workspace / "result.txt").write_text("phase-3\n", encoding="utf-8")
    _git(workspace, "add", "result.txt")
    _git(workspace, "commit", "-m", "strict Candidate")
    candidate_sha = _git(workspace, "rev-parse", "HEAD")
    client.set_output(
        worker.agent_id,
        "GWO_RESULT "
        + json.dumps(
            {
                "schema_version": 1,
                "action_key": worker_wait.node_key,
                "candidate_sha": candidate_sha,
            },
            separators=(",", ":"),
        ),
    )

    review_wait = kernel.reconcile_once("local/phase-three")
    assert review_wait.wait_condition == "review_axis"
    review_agents = client.find_by_labels({"gwo.review_candidate": candidate_sha})
    assert {agent.labels["gwo.review_axis"] for agent in review_agents} == {
        "standards",
        "spec",
        "specialist:security",
    }
    specialist = next(
        agent
        for agent in review_agents
        if agent.labels["gwo.review_axis"] == "specialist:security"
    )
    assert specialist.thinking == "max"
    for agent in review_agents:
        client.set_output(
            agent.agent_id,
            "GWO_REVIEW_AXIS "
            + json.dumps(
                {
                    "schema_version": 1,
                    "action_key": agent.labels["gwo.action_key"],
                    "candidate_sha": candidate_sha,
                    "axis": agent.labels["gwo.review_axis"],
                    "fixed_input_digest": agent.labels["gwo.review_input"],
                    "findings": [],
                },
                separators=(",", ":"),
            ),
        )

    decision_wait = kernel.reconcile_once("local/phase-three")
    assert decision_wait.wait_condition == "human_decision"
    assert decision_wait.active_worker_turns == 0
    kernel.record_human_decision(
        repository="local/phase-three",
        candidate_sha=candidate_sha,
        approved=True,
        source_ref="github://decision/strict-1",
    )

    completed = kernel.reconcile_once("local/phase-three")

    assert completed.status == "complete"
    assert completed.candidate_sha == candidate_sha


class _RuntimeMustNotRun:
    adapter_name = "must-not-run"

    def __getattr__(self, name):
        raise AssertionError(f"Result Adoption must not call Runtime.{name}")


def test_unchanged_node_and_contract_adopt_verified_result_without_runtime(
    tmp_path,
):
    repository = _temporary_repository(tmp_path)
    first_plan = PlanCompiler().compile(
        _plan_intent(),
        _ready_source(),
        _policy_snapshot(),
    )
    store_path = tmp_path / "v8.sqlite3"
    publication = LocalPlanPublication(store_path)
    publication.publish_and_activate(
        first_plan,
        expected_active_digest=None,
        writer_generation="phase-3",
    )
    first_kernel = Kernel(
        store_path=store_path,
        publication=publication,
        runtime=InMemoryRuntimeAdapter(tmp_path / "runtime"),
        verifier=EvidenceVerifier(),
        repository_path=repository,
        integration_branch="main",
        writer_generation="phase-3",
        delivery_control=InMemoryDeliveryControl(hosted_outcomes=("passed",)),
    )
    first = first_kernel.reconcile_once("local/phase-three")
    assert first.status == "complete"

    changed_source = _ready_source()
    changed_source["work_items"][0]["title"] = "Same contract, fresher tracker title"
    second_plan = PlanCompiler().compile(
        _plan_intent(),
        changed_source,
        _policy_snapshot(),
    )
    assert second_plan.digest != first_plan.digest
    first_node = _compiled_work_node()
    second_node = next(
        node
        for node in json.loads(second_plan.canonical_bytes)["nodes"]
        if node["kind"] == "work"
    )
    assert second_node["node_key"] == first_node["node_key"]
    assert second_node["contract_digest"] == first_node["contract_digest"]
    publication.publish_and_activate(
        second_plan,
        expected_active_digest=first_plan.digest,
        writer_generation="phase-3",
    )
    second_kernel = Kernel(
        store_path=store_path,
        publication=publication,
        runtime=_RuntimeMustNotRun(),
        verifier=EvidenceVerifier(),
        repository_path=repository,
        integration_branch="main",
        writer_generation="phase-3",
        delivery_control=InMemoryDeliveryControl(hosted_outcomes=("passed",)),
    )

    adopted = second_kernel.reconcile_once("local/phase-three")

    assert adopted.status == "complete"
    assert adopted.attempt_state == "adopted"
    assert adopted.attempt_id is None
    assert adopted.candidate_sha == first.candidate_sha
    assert adopted.result_digest == first.result_digest


def test_result_adoption_refreshes_only_base_sensitive_checks(tmp_path):
    repository = _temporary_repository(tmp_path)
    policy = _policy_snapshot()
    policy["check_definitions"][0]["base_sensitive"] = True
    first_plan = PlanCompiler().compile(
        _plan_intent(),
        _ready_source(),
        policy,
    )
    store_path = tmp_path / "v8.sqlite3"
    publication = LocalPlanPublication(store_path)
    publication.publish_and_activate(
        first_plan,
        expected_active_digest=None,
        writer_generation="phase-3",
    )
    first_kernel = Kernel(
        store_path=store_path,
        publication=publication,
        runtime=InMemoryRuntimeAdapter(tmp_path / "runtime"),
        verifier=EvidenceVerifier(),
        repository_path=repository,
        integration_branch="main",
        writer_generation="phase-3",
        delivery_control=InMemoryDeliveryControl(hosted_outcomes=("passed",)),
    )
    first = first_kernel.reconcile_once("local/phase-three")
    assert first.status == "complete"

    changed_source = _ready_source()
    changed_source["work_items"][0]["title"] = (
        "Same contract after the integration base advanced"
    )
    second_plan = PlanCompiler().compile(
        _plan_intent(),
        changed_source,
        policy,
    )
    publication.publish_and_activate(
        second_plan,
        expected_active_digest=first_plan.digest,
        writer_generation="phase-3",
    )
    second_kernel = Kernel(
        store_path=store_path,
        publication=publication,
        runtime=_RuntimeMustNotRun(),
        verifier=EvidenceVerifier(),
        repository_path=repository,
        integration_branch="main",
        writer_generation="phase-3",
        delivery_control=InMemoryDeliveryControl(hosted_outcomes=("passed",)),
    )

    adopted = second_kernel.reconcile_once("local/phase-three")

    assert adopted.status == "complete"
    assert adopted.attempt_state == "adopted"
    assert adopted.attempt_id is None
    assert adopted.candidate_sha == first.candidate_sha
    persisted = second_kernel._read_state(
        "local/phase-three",
        second_plan.digest,
    )
    assert persisted is not None
    assert len(persisted["base_sensitive_refresh_evidence_digests"]) == 1
    assert adopted.result_digest != first.result_digest


def test_explicit_result_supersession_excludes_historical_adoption(tmp_path):
    repository = _temporary_repository(tmp_path)
    first_plan = PlanCompiler().compile(
        _plan_intent(),
        _ready_source(),
        _policy_snapshot(),
    )
    store_path = tmp_path / "v8.sqlite3"
    publication = LocalPlanPublication(store_path)
    publication.publish_and_activate(
        first_plan,
        expected_active_digest=None,
        writer_generation="phase-3",
    )
    first_kernel = Kernel(
        store_path=store_path,
        publication=publication,
        runtime=InMemoryRuntimeAdapter(tmp_path / "runtime"),
        verifier=EvidenceVerifier(),
        repository_path=repository,
        integration_branch="main",
        writer_generation="phase-3",
        delivery_control=InMemoryDeliveryControl(hosted_outcomes=("passed",)),
    )
    completed = first_kernel.reconcile_once("local/phase-three")
    first_node = _compiled_work_node()
    first_kernel.supersede_verified_result(
        repository="local/phase-three",
        plan_digest=first_plan.digest,
        node_key=first_node["node_key"],
        candidate_sha=completed.candidate_sha,
        source_ref="github://decision/supersede-result",
    )
    changed_source = _ready_source()
    changed_source["work_items"][0]["title"] = "Replacement after supersession"
    second_plan = PlanCompiler().compile(
        _plan_intent(),
        changed_source,
        _policy_snapshot(),
    )
    publication.publish_and_activate(
        second_plan,
        expected_active_digest=first_plan.digest,
        writer_generation="phase-3",
    )
    second_kernel = Kernel(
        store_path=store_path,
        publication=publication,
        runtime=_RuntimeMustNotRun(),
        verifier=EvidenceVerifier(),
        repository_path=repository,
        integration_branch="main",
        writer_generation="phase-3",
    )

    with pytest.raises(AssertionError, match="must not call Runtime.read_binding"):
        second_kernel.reconcile_once("local/phase-three")


def test_replan_hold_blocks_new_admission_until_active_writer_clears_it(
    tmp_path,
):
    repository = _temporary_repository(tmp_path)
    compiled = PlanCompiler().compile(
        _plan_intent(),
        _ready_source(),
        _policy_snapshot(),
    )
    store_path = tmp_path / "v8.sqlite3"
    publication = LocalPlanPublication(store_path)
    publication.publish_and_activate(
        compiled,
        expected_active_digest=None,
        writer_generation="phase-3",
    )
    kernel = Kernel(
        store_path=store_path,
        publication=publication,
        runtime=InMemoryRuntimeAdapter(tmp_path / "runtime"),
        verifier=EvidenceVerifier(),
        repository_path=repository,
        integration_branch="main",
        writer_generation="phase-3",
        delivery_control=InMemoryDeliveryControl(hosted_outcomes=("passed",)),
    )
    kernel.place_replan_hold(
        repository="local/phase-three",
        goal_key="goal:phase-3",
        reason="replacement revision is not active",
    )

    with pytest.raises(KernelError) as held:
        kernel.reconcile_once("local/phase-three")
    assert held.value.code == "GOAL_ON_REPLAN_HOLD"

    kernel.clear_replan_hold(
        repository="local/phase-three",
        goal_key="goal:phase-3",
    )
    assert kernel.reconcile_once("local/phase-three").status == "complete"


class _RepairCapturePaseoClient(InMemoryPaseoClient):
    def __init__(self):
        super().__init__()
        self.repair_prompts = []

    def send_prompt(self, agent_id, prompt, *, action_key):
        if ":repair:" in action_key:
            self.repair_prompts.append((agent_id, prompt, action_key))
        return super().send_prompt(
            agent_id,
            prompt,
            action_key=action_key,
        )


def test_dual_axis_review_blockers_produce_exact_restart_stable_repair_prompt(
    tmp_path,
):
    repository = _temporary_repository(tmp_path)
    compiled = PlanCompiler().compile(
        _plan_intent(risk="standard"),
        _ready_source(),
        _policy_snapshot(),
    )
    store_path = tmp_path / "v8.sqlite3"
    publication = LocalPlanPublication(store_path)
    publication.publish_and_activate(
        compiled,
        expected_active_digest=None,
        writer_generation="phase-3",
    )
    client = _RepairCapturePaseoClient()
    worker_profile = RuntimeProfile(
        name="worker-standard",
        provider="kimi-cli",
        model="kimi-code/kimi-for-coding",
        thinking="max",
        mode="yolo",
        features={},
    )
    frontier_profile = RuntimeProfile(
        name="worker-frontier",
        provider="codex",
        model="gpt-5.6-sol",
        thinking="xhigh",
        mode="full-access",
        features={},
    )
    kernel = Kernel(
        store_path=store_path,
        publication=publication,
        runtime=PaseoRuntimeAdapter(client),
        verifier=EvidenceVerifier(),
        repository_path=repository,
        integration_branch="main",
        writer_generation="phase-3",
        runtime_profile=worker_profile,
        frontier_runtime_profile=frontier_profile,
        runtime_config=_runtime_config(),
    )
    waiting_for_worker = kernel.reconcile_once("local/phase-three")
    worker = client.find_by_labels({"gwo.admission": waiting_for_worker.admission_id})[
        0
    ]
    workspace = Path(worker.workspace)
    subprocess.run(
        [
            "git",
            "-C",
            str(repository),
            "worktree",
            "add",
            "--detach",
            str(workspace),
            "HEAD",
        ],
        check=True,
        capture_output=True,
    )
    (workspace / "result.txt").write_text("phase-3\n", encoding="utf-8")
    _git(workspace, "add", "result.txt")
    _git(workspace, "commit", "-m", "rejected Candidate")
    candidate_sha = _git(workspace, "rev-parse", "HEAD")
    client.set_output(
        worker.agent_id,
        "GWO_RESULT "
        + json.dumps(
            {
                "schema_version": 1,
                "action_key": waiting_for_worker.node_key,
                "candidate_sha": candidate_sha,
            },
            separators=(",", ":"),
        ),
    )
    kernel.reconcile_once("local/phase-three")
    review_agents = client.find_by_labels({"gwo.review_candidate": candidate_sha})
    findings_by_axis = {
        "standards": {
            "severity": "hard",
            "code": "STANDARD_BROKEN",
            "source": "CONTEXT.md",
            "location": "result.txt:1",
            "message": "Candidate violates the documented standard.",
        },
        "spec": {
            "severity": "hard",
            "code": "SPEC_MISSING",
            "source": "synthetic://issue/45",
            "location": "result.txt:1",
            "message": "Candidate does not satisfy the Spec.",
        },
    }
    for agent in review_agents:
        axis = agent.labels["gwo.review_axis"]
        client.set_output(
            agent.agent_id,
            "GWO_REVIEW_AXIS "
            + json.dumps(
                {
                    "schema_version": 1,
                    "action_key": agent.labels["gwo.action_key"],
                    "candidate_sha": candidate_sha,
                    "axis": axis,
                    "fixed_input_digest": agent.labels["gwo.review_input"],
                    "findings": [findings_by_axis[axis]],
                },
                separators=(",", ":"),
            ),
        )

    repairing = kernel.reconcile_once("local/phase-three")

    assert repairing.status == "waiting"
    assert repairing.wait_condition == "runtime_result"
    assert repairing.attempt_state == "repairing"
    assert repairing.attempt_ordinal == 1
    assert repairing.repair_rounds_used == 1
    assert repairing.attempt_id == waiting_for_worker.attempt_id
    assert client.create_count == 3
    assert client.inspect(worker.agent_id).lifecycle == "running"
    assert client.read_output(worker.agent_id) is None
    assert all(client.inspect(agent.agent_id).archived for agent in review_agents)
    assert len(client.repair_prompts) == 1
    repair_prompt = client.repair_prompts[0][1]
    repair_round = json.loads(repair_prompt.text)["repair_round"]
    assert repair_round["causes"] == [
        {
            "type": "review_blocker",
            "axis": axis,
            "finding": findings_by_axis[axis],
        }
        for axis in ("standards", "spec")
    ]
    assert repair_round["changed_files"] == ["result.txt"]
    assert len(repair_round["acceptance_digest"]) == 64
    assert len(repair_prompt.text.encode("utf-8")) <= 64 * 1024
    assert "required check did not pass" not in repair_prompt.text
    assert "result-hosted" not in repair_prompt.text
    assert "hosted_only" not in repair_prompt.text

    repair_state = kernel._read_state(
        "local/phase-three",
        compiled.digest,
        repairing.node_key,
    )
    prompt_digest = repair_state["repair_prompt"]["prompt_digest"]
    payload_digest = repair_state["repair_prompt"]["payload_digest"]
    send_count = client.send_count
    restarted_kernel = Kernel(
        store_path=store_path,
        publication=publication,
        runtime=PaseoRuntimeAdapter(client),
        verifier=EvidenceVerifier(),
        repository_path=repository,
        integration_branch="main",
        writer_generation="phase-3",
        runtime_profile=worker_profile,
        frontier_runtime_profile=frontier_profile,
        runtime_config=_runtime_config(),
    )

    restarted = restarted_kernel.reconcile_once("local/phase-three")

    assert restarted.wait_condition == "runtime_result"
    assert client.send_count == send_count
    restarted_state = restarted_kernel._read_state(
        "local/phase-three",
        compiled.digest,
        repairing.node_key,
    )
    assert restarted_state["repair_prompt"]["prompt_digest"] == prompt_digest
    assert restarted_state["repair_prompt"]["payload_digest"] == payload_digest

    _git(workspace, "commit", "--allow-empty", "-m", "repaired Candidate")
    repaired_sha = _git(workspace, "rev-parse", "HEAD")
    client.set_output(
        worker.agent_id,
        "GWO_RESULT "
        + json.dumps(
            {
                "schema_version": 1,
                "action_key": repairing.node_key,
                "candidate_sha": repaired_sha,
            },
            separators=(",", ":"),
        ),
    )
    second_review_wait = restarted_kernel.reconcile_once("local/phase-three")
    assert second_review_wait.wait_condition == "review_axis"
    second_review_agents = client.find_by_labels({"gwo.review_candidate": repaired_sha})
    assert len(second_review_agents) == 2
    for agent in second_review_agents:
        client.set_output(
            agent.agent_id,
            "GWO_REVIEW_AXIS "
            + json.dumps(
                {
                    "schema_version": 1,
                    "action_key": agent.labels["gwo.action_key"],
                    "candidate_sha": repaired_sha,
                    "axis": agent.labels["gwo.review_axis"],
                    "fixed_input_digest": agent.labels["gwo.review_input"],
                    "findings": [
                        {
                            "severity": "hard",
                            "code": "STILL_REJECTED",
                            "source": "synthetic://issue/45",
                            "location": "result.txt:1",
                            "message": "Repair still does not satisfy the Spec.",
                        }
                    ]
                    if agent.labels["gwo.review_axis"] == "spec"
                    else [],
                },
                separators=(",", ":"),
            ),
        )

    escalating = restarted_kernel.reconcile_once("local/phase-three")

    assert escalating.status == "running"
    assert escalating.directive == "run_again"
    assert escalating.attempt_ordinal == 2
    assert escalating.attempt_id is None
    assert client.inspect(worker.agent_id).archived is True

    frontier_waiting = restarted_kernel.reconcile_once("local/phase-three")
    frontier = client.find_by_labels({"gwo.admission": frontier_waiting.admission_id})[
        0
    ]
    assert frontier.provider == "codex"
    assert frontier.model == "gpt-5.6-sol"
    assert frontier.thinking == "xhigh"
    assert frontier_waiting.attempt_ordinal == 2
    assert frontier_waiting.attempt_id.endswith(":2")
