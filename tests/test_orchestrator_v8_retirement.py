from __future__ import annotations

from dataclasses import asdict, replace
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys

import pytest

SCRIPTS = (
    Path(__file__).resolve().parents[1] / "skills" / "orchestrator" / "scripts"
)
sys.path.insert(0, str(SCRIPTS))

from gwo_v8.runtime import (  # noqa: E402
    InMemoryRuntimeAdapter,
    PaseoAgentRecord,
    PaseoCliClient,
    PaseoRuntimeAdapter,
    ReviewAxisBinding,
    RuntimeAdmission,
    RuntimeAdapterError,
    RuntimeBinding,
    RuntimePrompt,
)
from gwo_v8 import (  # noqa: E402
    EvidenceVerifier,
    InMemoryDeliveryControl,
    Kernel,
    LocalPlanPublication,
    PlanCompiler,
    RuntimeProfile,
)

def _git(repository: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return result.stdout.strip()


def _repository(tmp_path: Path) -> tuple[Path, str]:
    repository = tmp_path / "repository"
    repository.mkdir()
    _git(repository, "init", "-b", "main")
    _git(repository, "config", "user.email", "gwo@example.test")
    _git(repository, "config", "user.name", "GWO Test")
    (repository / "base.txt").write_text("base\n", encoding="utf-8")
    _git(repository, "add", "base.txt")
    _git(repository, "commit", "-m", "base")
    return repository, _git(repository, "rev-parse", "HEAD")


def _candidate(
    repository: Path,
    tmp_path: Path,
    base_sha: str,
) -> tuple[Path, str]:
    workspace = tmp_path / "gwo-retirement-worker"
    _git(
        repository,
        "worktree",
        "add",
        "-b",
        "gwo/attempt/retirement-worker",
        str(workspace),
        base_sha,
    )
    (workspace / "candidate.txt").write_text("candidate\n", encoding="utf-8")
    _git(workspace, "add", "candidate.txt")
    _git(workspace, "commit", "-m", "candidate")
    return workspace, _git(workspace, "rev-parse", "HEAD")


def _binding(repository: Path, workspace: Path) -> RuntimeBinding:
    return RuntimeBinding(
        adapter="paseo",
        runtime_id="agent:retirement",
        repository="owner/repository",
        plan_digest="1" * 64,
        node_key="node:retirement",
        admission_id="admission:retirement",
        repository_path=str(repository.resolve()),
        workspace=str(workspace.resolve()),
        prompt_accepted=True,
        prompt_digest="2" * 64,
        attempt_id="attempt:retirement:1",
        agent_id="agent:retirement",
        session_id="session:retirement",
        workspace_id=workspace.name,
        base_sha=_git(repository, "rev-parse", "main"),
    )


def _compiled_retirement_plan():
    check_command = [
        "python",
        "-c",
        "from pathlib import Path; assert Path('result.txt').read_text() == 'done\\n'",
    ]
    intent = {
        "parent_plan_digest": None,
        "goals": [
            {
                "goal_key": "goal:retirement",
                "objective": "Integrate and retire one exact Candidate.",
                "acceptance": ["result.txt contains done"],
            }
        ],
        "nodes": [
            {
                "goal_key": "goal:retirement",
                "work_item_key": "issue:88",
                "kind": "work",
                "inputs": {
                    "file_changes": [{"path": "result.txt", "content": "done\n"}]
                },
                "output_contract": {
                    "required_evidence": [
                        {"kind": "candidate"},
                        {"kind": "check", "check_id": "result-content"},
                    ],
                    "checks": [
                        {
                            "check_id": "result-content",
                            "command": check_command,
                        }
                    ],
                },
                "effect_contract": {
                    "write_scopes": ["result.txt"],
                    "external_effects": [],
                },
                "resource_claims": [],
                "runtime_requirements": {
                    "capabilities": ["git", "local_check"]
                },
                "difficulty": "standard",
                "risk": "low",
                "recovery_policy": {
                    "semantic_attempts": 2,
                    "repair_rounds": 1,
                },
                "skill_reference": None,
            }
        ],
        "edges": [],
    }
    source = {
        "repository": "local/retirement",
        "work_items": [
            {
                "work_item_key": "issue:88",
                "tracker_state": "ready-for-agent",
                "source_ref": "synthetic://issue/88",
                "title": "Retire an integrated Candidate",
                "outcome_contract": {
                    "path": "result.txt",
                    "content": "done\n",
                },
            }
        ],
    }
    policy = {
        "version": 3,
        "low_risk_allowlist": ["result.txt"],
        "check_definitions": [
            {
                "check_id": "result-content",
                "version": 1,
                "command": check_command,
                "hosted_name": None,
                "environment_requirements": ["python"],
                "input_selector": ["result.txt"],
                "base_sensitive": False,
                "risk": "low",
                "hosted_only": False,
                "suite": "repository",
            },
            {
                "check_id": "result-hosted",
                "version": 1,
                "command": ["python", "-c", "raise SystemExit(0)"],
                "hosted_name": "Retirement CI",
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
    return PlanCompiler().compile(intent, source, policy)


def _compiled_multi_member_retirement_plan():
    nodes = []
    work_items = []
    check_definitions = []
    paths = []
    for ordinal in (1, 2):
        path = f"result-{ordinal}.txt"
        content = f"done-{ordinal}\n"
        check_id = f"result-{ordinal}-content"
        check_command = [
            "python",
            "-c",
            (
                "from pathlib import Path; "
                f"assert Path('{path}').read_text() == {content!r}"
            ),
        ]
        paths.append(path)
        nodes.append(
            {
                "goal_key": "goal:retirement",
                "work_item_key": f"issue:88:{ordinal}",
                "kind": "work",
                "inputs": {
                    "file_changes": [{"path": path, "content": content}]
                },
                "output_contract": {
                    "required_evidence": [
                        {"kind": "candidate"},
                        {"kind": "check", "check_id": check_id},
                    ],
                    "checks": [
                        {"check_id": check_id, "command": check_command}
                    ],
                },
                "effect_contract": {
                    "write_scopes": [path],
                    "external_effects": [],
                },
                "resource_claims": [],
                "runtime_requirements": {
                    "capabilities": ["git", "local_check"]
                },
                "difficulty": "standard",
                "risk": "low",
                "recovery_policy": {
                    "semantic_attempts": 2,
                    "repair_rounds": 1,
                },
                "skill_reference": None,
            }
        )
        work_items.append(
            {
                "work_item_key": f"issue:88:{ordinal}",
                "tracker_state": "ready-for-agent",
                "source_ref": f"synthetic://issue/88/{ordinal}",
                "title": f"Retire integrated Candidate {ordinal}",
                "outcome_contract": {"path": path, "content": content},
            }
        )
        check_definitions.append(
            {
                "check_id": check_id,
                "version": 1,
                "command": check_command,
                "hosted_name": None,
                "environment_requirements": ["python"],
                "input_selector": [path],
                "base_sensitive": False,
                "risk": "low",
                "hosted_only": False,
                "suite": "repository",
            }
        )
    check_definitions.append(
        {
            "check_id": "retirement-hosted",
            "version": 1,
            "command": ["python", "-c", "raise SystemExit(0)"],
            "hosted_name": "Retirement CI",
            "environment_requirements": [],
            "input_selector": paths,
            "base_sensitive": False,
            "risk": "low",
            "hosted_only": True,
            "suite": "hosted",
        }
    )
    return PlanCompiler().compile(
        {
            "parent_plan_digest": None,
            "goals": [
                {
                    "goal_key": "goal:retirement",
                    "objective": "Integrate and retire two exact Candidates.",
                    "acceptance": ["both Candidates are integrated and retired"],
                }
            ],
            "nodes": nodes,
            "edges": [],
        },
        {
            "repository": "local/retirement",
            "work_items": work_items,
        },
        {
            "version": 3,
            "low_risk_allowlist": paths,
            "check_definitions": check_definitions,
            "strict_review": {
                "specialist_requirements": [],
                "human_decision_required": True,
            },
        },
    )


def _kernel_with_runtime(
    tmp_path: Path,
    runtime: InMemoryRuntimeAdapter,
    *,
    compiled=None,
) -> tuple[Kernel, Path]:
    repository, _base_sha = _repository(tmp_path)
    compiled = compiled or _compiled_retirement_plan()
    store_path = tmp_path / "v8.sqlite3"
    publication = LocalPlanPublication(store_path)
    publication.publish_and_activate(
        compiled,
        expected_active_digest=None,
        writer_generation="retirement-test",
    )
    profile = RuntimeProfile(
        name="worker-standard",
        provider="kimi-cli",
        model="kimi-code/kimi-for-coding",
        thinking="on",
        mode="yolo",
        features={},
    )
    kernel = Kernel(
        store_path=store_path,
        publication=publication,
        runtime=runtime,
        verifier=EvidenceVerifier(),
        repository_path=repository,
        integration_branch="main",
        writer_generation="retirement-test",
        runtime_profile=profile,
        frontier_runtime_profile=profile,
        delivery_control=InMemoryDeliveryControl(
            hosted_outcomes=("pending", "passed")
        ),
    )
    return kernel, repository


def _runtime_workspace(
    runtime: InMemoryRuntimeAdapter,
    admission_id: str,
) -> Path:
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "-", admission_id).strip("-")
    return runtime.workspace_root / slug


def test_kernel_parks_batch_ready_then_completes_only_after_retirement(tmp_path):
    runtime = InMemoryRuntimeAdapter(tmp_path / "runtime-workspaces")
    kernel, repository = _kernel_with_runtime(tmp_path, runtime)

    batch_ready = kernel.reconcile_once("local/retirement")
    workspace = _runtime_workspace(runtime, str(batch_ready.admission_id))

    assert batch_ready.status == "waiting"
    assert batch_ready.hosted_check_state == "pending"
    assert workspace.exists()
    assert _git(repository, "rev-parse", "HEAD") != batch_ready.candidate_sha

    completed = kernel.reconcile_once("local/retirement")

    assert completed.status == "complete"
    assert completed.retirement_state == "complete"
    assert completed.candidate_sha == _git(repository, "rev-parse", "HEAD")
    assert not workspace.exists()


class _FailRetirementOnce(InMemoryRuntimeAdapter):
    def __init__(self, workspace_root: Path):
        super().__init__(workspace_root)
        self.retirement_attempts = 0

    def retire_after_integration(self, binding, authorization):
        self.retirement_attempts += 1
        if self.retirement_attempts == 1:
            raise RuntimeAdapterError(
                "RETIREMENT_SYNTHETIC_FAILURE",
                "synthetic physical cleanup failure",
                failure_class="transient",
            )
        return super().retire_after_integration(binding, authorization)


def test_kernel_persists_retirement_error_and_retries_idempotently(tmp_path):
    runtime = _FailRetirementOnce(tmp_path / "runtime-workspaces")
    kernel, _repository_path = _kernel_with_runtime(tmp_path, runtime)
    batch_ready = kernel.reconcile_once("local/retirement")
    workspace = _runtime_workspace(runtime, str(batch_ready.admission_id))

    failed_retirement = kernel.reconcile_once("local/retirement")

    assert failed_retirement.status == "waiting"
    assert failed_retirement.attempt_state == "retirement_pending"
    assert failed_retirement.retirement_state == "error"
    assert failed_retirement.last_retirement_error == {
        "code": "RETIREMENT_SYNTHETIC_FAILURE",
        "failure_class": "transient",
    }
    assert runtime.retirement_attempts == 1
    assert workspace.exists()

    completed = kernel.reconcile_once("local/retirement")

    assert completed.status == "complete"
    assert completed.retirement_state == "complete"
    assert runtime.retirement_attempts == 2
    assert not workspace.exists()


def test_multi_member_batch_retires_each_candidate_ancestor_before_goal_complete(
    tmp_path,
):
    runtime = InMemoryRuntimeAdapter(tmp_path / "runtime-workspaces")
    kernel, repository = _kernel_with_runtime(
        tmp_path,
        runtime,
        compiled=_compiled_multi_member_retirement_plan(),
    )

    waiting = kernel.reconcile_once("local/retirement")
    waiting_members = waiting.node_outcomes
    candidates = {
        str(member.candidate_sha)
        for member in waiting_members
        if member.candidate_sha is not None
    }
    workspaces = {
        _runtime_workspace(runtime, member.admission_id)
        for member in waiting_members
    }

    assert len(candidates) == 2
    assert all(workspace.exists() for workspace in workspaces)

    completed = kernel.reconcile_once("local/retirement")
    integrated_sha = _git(repository, "rev-parse", "main")

    assert completed.status == "complete"
    assert len(completed.completed_work_item_keys) == 2
    assert all(
        member.retirement_state == "complete"
        for member in completed.node_outcomes
    )
    assert all(
        subprocess.run(
            ["git", "merge-base", "--is-ancestor", candidate, integrated_sha],
            cwd=repository,
        ).returncode
        == 0
        for candidate in candidates
    )
    assert integrated_sha not in candidates
    assert all(not workspace.exists() for workspace in workspaces)


def test_batch_ready_cannot_authorize_or_delete_candidate_worktree(tmp_path):
    from gwo_v8.retirement import (
        RetirementError,
        authorize_after_integration,
    )

    repository, base_sha = _repository(tmp_path)
    workspace, candidate_sha = _candidate(repository, tmp_path, base_sha)
    binding = _binding(repository, workspace)

    with pytest.raises(RetirementError) as error:
        authorize_after_integration(
            binding=binding,
            candidate_sha=candidate_sha,
            integrated_sha=candidate_sha,
            target_branch="main",
        )

    assert error.value.code == "INTEGRATION_READBACK_MISMATCH"
    assert workspace.exists()
    assert workspace.resolve().as_posix() in _git(
        repository,
        "worktree",
        "list",
        "--porcelain",
    )


def test_exact_integration_issues_path_free_bound_authorization(tmp_path):
    from gwo_v8.retirement import (
        authorize_after_integration,
    )

    repository, base_sha = _repository(tmp_path)
    workspace, candidate_sha = _candidate(repository, tmp_path, base_sha)
    binding = _binding(repository, workspace)
    _git(repository, "merge", "--ff-only", candidate_sha)

    authorization = authorize_after_integration(
        binding=binding,
        candidate_sha=candidate_sha,
        integrated_sha=candidate_sha,
        target_branch="main",
    )

    assert authorization.repository == binding.repository
    assert authorization.admission_id == binding.admission_id
    assert authorization.attempt_id == binding.attempt_id
    assert authorization.agent_id == binding.agent_id
    assert authorization.workspace_id == binding.workspace_id
    assert authorization.candidate_sha == candidate_sha
    assert authorization.integrated_sha == candidate_sha
    assert authorization.target_branch == "main"
    assert all(
        not Path(str(value)).is_absolute()
        for value in asdict(authorization).values()
    )


def test_retire_after_integration_physically_removes_ignored_cache_worktree(
    tmp_path,
):
    from gwo_v8.retirement import authorize_after_integration

    repository, base_sha = _repository(tmp_path)
    runtime = InMemoryRuntimeAdapter(tmp_path / "runtime-workspaces")
    admission = RuntimeAdmission(
        repository="owner/repository",
        plan_digest="1" * 64,
        node_key="node:retirement",
        admission_id="admission:retirement",
        repository_path=repository,
        base_sha=base_sha,
    )
    binding = runtime.materialize(admission)
    prompt_text = json.dumps({"node": {"node_key": admission.node_key}})
    prompt = RuntimePrompt(
        text=prompt_text,
        digest=hashlib.sha256(prompt_text.encode("utf-8")).hexdigest(),
    )
    runtime.accept_prompt(binding, prompt)
    binding = runtime.read_binding(admission.admission_id)
    assert binding is not None
    binding = runtime.attach_attempt(binding, "attempt:retirement:1")
    workspace = Path(binding.workspace)
    (workspace / ".gitignore").write_text(".cache/\n", encoding="utf-8")
    (workspace / "candidate.txt").write_text("candidate\n", encoding="utf-8")
    _git(workspace, "add", ".gitignore", "candidate.txt")
    _git(workspace, "commit", "-m", "candidate")
    candidate_sha = _git(workspace, "rev-parse", "HEAD")
    ignored_cache = workspace / ".cache" / "large.bin"
    ignored_cache.parent.mkdir()
    ignored_cache.write_bytes(b"x" * 1024)
    _git(repository, "merge", "--ff-only", candidate_sha)
    authorization = authorize_after_integration(
        binding=binding,
        candidate_sha=candidate_sha,
        integrated_sha=candidate_sha,
        target_branch="main",
    )

    readback = runtime.retire_after_integration(binding, authorization)

    assert readback.agent_archived is True
    assert readback.directory_absent is True
    assert readback.worktree_absent is True
    assert not workspace.exists()
    assert workspace.resolve().as_posix() not in _git(
        repository,
        "worktree",
        "list",
        "--porcelain",
    )
    assert runtime.retire_after_integration(binding, authorization).complete is True


def test_paseo_client_archives_only_a_repository_bound_parsed_worktree_name(
    tmp_path,
    monkeypatch,
):
    client = PaseoCliClient("paseo")
    repository = (tmp_path / "repository").resolve()
    workspace = (tmp_path / "gwo-0123456789abcdef").resolve()
    commands: list[tuple[str, dict[str, object]]] = []

    monkeypatch.setattr(
        client,
        "list_worktrees",
        lambda repository_path: (
            {
                "path": str(workspace),
                "head": "a" * 40,
                "branch_name": "gwo/temporary",
                "native_name": workspace.name,
            },
        )
        if Path(repository_path).resolve() == repository
        else (),
    )

    def _record(operation, payload, **_kwargs):
        commands.append((operation, payload))
        return {"success": True, "error": None}

    monkeypatch.setattr(client, "_run_daemon_request", _record)

    client.archive_worktree(str(repository), workspace.name)

    assert commands[0][0] == "archive"
    assert commands[0][1]["repoRoot"] == str(repository)
    assert commands[0][1]["worktreePath"] == str(workspace)


def test_cli_maps_real_wks_agent_payload_to_native_worktree_list_name(
    tmp_path,
    monkeypatch,
):
    from gwo_v8.retirement import resolve_native_worktree_name

    repository = tmp_path / "repository"
    workspace = tmp_path / "gwo-issue-88"
    repository.mkdir()
    workspace.mkdir()
    client = PaseoCliClient("paseo")
    requests: list[tuple[str, dict[str, object]]] = []

    def _request(operation, payload, **_kwargs):
        requests.append((operation, payload))
        return {
            "worktrees": [
                {
                    "worktreePath": str(workspace),
                    "head": "a" * 40,
                    "branchName": "gwo/issue-88-review",
                    "createdAt": "2026-07-26T00:00:00.000Z",
                }
            ]
        }

    monkeypatch.setattr(client, "_run_daemon_request", _request)
    agent = client._agent(
        {
            "Id": "agent_01k1x88",
            "SessionId": "session_01k1x88",
            "Worktree": {
                "Id": "wks_01k1x88",
                "Path": str(workspace),
            },
            "Cwd": str(workspace),
            "Provider": "kimi",
            "Model": "kimi-code/k3",
            "Thinking": "high",
            "Mode": "yolo",
            "Status": "idle",
        }
    )

    worktrees = client.list_worktrees(str(repository))
    native_name = resolve_native_worktree_name(
        workspace=Path(agent.workspace),
        candidate_sha="a" * 40,
        temporary_branch="gwo/issue-88-review",
        worktrees=worktrees,
    )

    assert agent.workspace_id == "wks_01k1x88"
    assert native_name == "gwo-issue-88"
    assert native_name != agent.workspace_id
    assert requests == [
        ("list", {"repoRoot": str(repository.resolve())})
    ]


def test_cli_repository_bound_list_reaches_real_paseo_daemon():
    repository = Path(__file__).resolve().parents[1]
    client = PaseoCliClient("paseo")

    try:
        worktrees = client.list_worktrees(str(repository))
    except RuntimeAdapterError as error:
        if error.code == "PASEO_NATIVE_TRANSPORT_UNAVAILABLE":
            pytest.skip("installed Paseo exposes no daemon-native transport")
        raise

    current = next(
        (
            record
            for record in worktrees
            if Path(str(record["path"])).resolve() == repository.resolve()
        ),
        None,
    )
    if current is None and os.environ.get("GITHUB_ACTIONS") == "true":
        pytest.skip("GitHub Actions checkout is not a Paseo-managed workspace")
    assert current is not None
    assert current["native_name"] == repository.name
    assert re.fullmatch(r"[0-9a-f]{40}", str(current["head"]))
    assert current["branch_name"] == _git(repository, "branch", "--show-current")


def test_cli_archive_request_binds_the_listed_repository_identity(
    tmp_path,
    monkeypatch,
):
    repository = (tmp_path / "repository").resolve()
    workspace = (tmp_path / "gwo-retirement-transport").resolve()
    repository.mkdir()
    workspace.mkdir()
    client = PaseoCliClient("paseo")
    requests: list[tuple[str, dict[str, object]]] = []

    monkeypatch.setattr(
        client,
        "list_worktrees",
        lambda repository_path: (
            {
                "path": str(workspace),
                "head": "a" * 40,
                "branch_name": "gwo/retirement-transport",
                "native_name": workspace.name,
            },
        )
        if Path(repository_path).resolve() == repository
        else (),
    )
    monkeypatch.setattr(
        client,
        "_run_daemon_request",
        lambda operation, payload, **_kwargs: requests.append(
            (operation, payload)
        )
        or {"success": True, "removedAgents": [], "error": None},
        raising=False,
    )

    client.archive_worktree(str(repository), workspace.name)

    assert requests == [
        (
            "archive",
            {
                "repoRoot": str(repository),
                "worktreePath": str(workspace),
                "branchName": "gwo/retirement-transport",
                "scope": "worktree",
            },
        )
    ]
    assert workspace.is_dir()


class _NativeArchivingPaseoClient:
    native_finish_notification_supported = True

    def __init__(
        self,
        repository: Path,
        record: PaseoAgentRecord,
        extra_records: tuple[PaseoAgentRecord, ...] = (),
    ):
        self.repository = repository
        self.record = record
        self.extra_records = extra_records
        self.archived_worktree_names: list[str] = []

    def list_worktrees(self, repository_path):
        assert Path(repository_path).resolve() == self.repository.resolve()
        return (
            {
                "path": self.record.workspace,
                "head": _git(Path(self.record.workspace), "rev-parse", "HEAD"),
                "branch_name": _git(
                    Path(self.record.workspace),
                    "branch",
                    "--show-current",
                ),
                "native_name": Path(self.record.workspace).name,
            },
        )

    def find_by_labels(self, labels):
        return tuple(
            record
            for record in (self.record, *self.extra_records)
            if not record.archived
            and all(record.labels.get(key) == value for key, value in labels.items())
        )

    def inspect(self, agent_id):
        return next(
            record
            for record in (self.record, *self.extra_records)
            if record.agent_id == agent_id
        )

    def archive(self, agent_id):
        assert agent_id == self.record.agent_id
        self.record = replace(
            self.record,
            lifecycle="archived",
            archived=True,
        )

    def archive_worktree(self, repository_path, worktree_name):
        assert Path(repository_path).resolve() == self.repository.resolve()
        assert worktree_name == Path(self.record.workspace).name
        self.archived_worktree_names.append(worktree_name)
        _git(
            self.repository,
            "worktree",
            "remove",
            "--force",
            self.record.workspace,
        )


def _paseo_record(binding: RuntimeBinding) -> PaseoAgentRecord:
    return PaseoAgentRecord(
        agent_id=str(binding.agent_id),
        session_id=str(binding.session_id),
        workspace_id=str(binding.workspace_id),
        workspace=binding.workspace,
        parent_agent_id=None,
        provider="kimi-cli",
        model="kimi-code/k3",
        profile_digest="profile",
        thinking="high",
        mode="yolo",
        features={},
        labels={
            "gwo.repository": binding.repository,
            "gwo.plan": binding.plan_digest,
            "gwo.node": binding.node_key,
            "gwo.admission": binding.admission_id,
            "gwo.repository_path": binding.repository_path,
            "gwo.base_sha": binding.base_sha,
            "gwo.prompt_digest": binding.prompt_digest,
            "gwo.attempt": binding.attempt_id,
        },
        lifecycle="idle",
    )


def test_paseo_adapter_consumes_authorization_at_only_destructive_seam(tmp_path):
    from gwo_v8.retirement import authorize_after_integration

    repository, base_sha = _repository(tmp_path)
    workspace, candidate_sha = _candidate(repository, tmp_path, base_sha)
    binding = _binding(repository, workspace)
    _git(repository, "merge", "--ff-only", candidate_sha)
    record = _paseo_record(binding)
    client = _NativeArchivingPaseoClient(repository, record)
    runtime = PaseoRuntimeAdapter(client)
    authorization = authorize_after_integration(
        binding=binding,
        candidate_sha=candidate_sha,
        integrated_sha=candidate_sha,
        target_branch="main",
    )

    readback = runtime.retire_after_integration(binding, authorization)

    assert readback.complete is True
    assert client.archived_worktree_names == [workspace.name]
    assert not workspace.exists()
    assert (
        subprocess.run(
            [
                "git",
                "show-ref",
                "--verify",
                "--quiet",
                "refs/heads/gwo/attempt/retirement-worker",
            ],
            cwd=repository,
        ).returncode
        != 0
    )


def test_paseo_retirement_resolves_wks_id_to_native_worktree_name(tmp_path):
    from gwo_v8.retirement import authorize_after_integration

    repository, base_sha = _repository(tmp_path)
    workspace, candidate_sha = _candidate(repository, tmp_path, base_sha)
    binding = replace(
        _binding(repository, workspace),
        workspace_id="wks_01k1x88nativeidentity",
    )
    _git(repository, "merge", "--ff-only", candidate_sha)
    record = _paseo_record(binding)
    client = _NativeArchivingPaseoClient(repository, record)
    runtime = PaseoRuntimeAdapter(client)
    authorization = authorize_after_integration(
        binding=binding,
        candidate_sha=candidate_sha,
        integrated_sha=candidate_sha,
        target_branch="main",
    )

    readback = runtime.retire_after_integration(binding, authorization)

    assert readback.complete is True
    assert client.archived_worktree_names == [workspace.name]
    assert workspace.name != binding.workspace_id
    assert not workspace.exists()


@pytest.mark.parametrize(
    ("field", "wrong_value"),
    [
        ("repository", "owner/wrong"),
        ("plan_digest", "0" * 64),
        ("node_key", "node:wrong"),
        ("admission_id", "admission:wrong"),
        ("attempt_id", "attempt:wrong"),
        ("agent_id", "agent:wrong"),
        ("workspace_id", "wks_wrong"),
        ("candidate_sha", "0" * 40),
        ("integrated_sha", "f" * 40),
        ("target_branch", "release"),
        ("temporary_branch", "gwo/wrong"),
    ],
)
def test_completed_retirement_rejects_wrong_receipt_identity(
    tmp_path,
    field,
    wrong_value,
):
    from gwo_v8.retirement import (
        RetirementError,
        RetirementReadback,
        authorize_after_integration,
        completed_retirement,
    )

    repository, base_sha = _repository(tmp_path)
    workspace, candidate_sha = _candidate(repository, tmp_path, base_sha)
    binding = _binding(repository, workspace)
    _git(repository, "merge", "--ff-only", candidate_sha)
    authorization = authorize_after_integration(
        binding=binding,
        candidate_sha=candidate_sha,
        integrated_sha=candidate_sha,
        target_branch="main",
    )
    receipt = RetirementReadback(
        repository=authorization.repository,
        plan_digest=authorization.plan_digest,
        node_key=authorization.node_key,
        admission_id=authorization.admission_id,
        attempt_id=authorization.attempt_id,
        agent_id=authorization.agent_id,
        workspace_id=authorization.workspace_id,
        candidate_sha=authorization.candidate_sha,
        integrated_sha=authorization.integrated_sha,
        target_branch=authorization.target_branch,
        temporary_branch=authorization.temporary_branch,
        authorization_digest=authorization.authorization_digest,
        agent_archived=True,
        directory_absent=True,
        worktree_absent=True,
        branch_deleted=True,
    )

    with pytest.raises(RetirementError) as error:
        completed_retirement(
            authorization,
            replace(receipt, **{field: wrong_value}),
        )

    assert error.value.code == "RETIREMENT_READBACK_IDENTITY_MISMATCH"


def test_in_memory_missing_state_returns_typed_runtime_error(tmp_path):
    from gwo_v8.retirement import authorize_after_integration

    repository, base_sha = _repository(tmp_path)
    workspace, candidate_sha = _candidate(repository, tmp_path, base_sha)
    binding = _binding(repository, workspace)
    _git(repository, "merge", "--ff-only", candidate_sha)
    authorization = authorize_after_integration(
        binding=binding,
        candidate_sha=candidate_sha,
        integrated_sha=candidate_sha,
        target_branch="main",
    )
    restarted_runtime = InMemoryRuntimeAdapter(tmp_path / "restarted-runtime")

    with pytest.raises(RuntimeAdapterError) as error:
        restarted_runtime.retire_after_integration(binding, authorization)

    assert error.value.code == "RUNTIME_IDENTITY_MISMATCH"
    assert error.value.failure_class == "ambiguous"
    assert workspace.exists()


def test_runtime_interface_has_no_legacy_retire_entrypoint(tmp_path):
    from gwo_v8.runtime import InMemoryPaseoClient, RuntimeAdapter

    assert "retire" not in RuntimeAdapter.__dict__
    assert not hasattr(PaseoRuntimeAdapter(InMemoryPaseoClient()), "retire")
    assert not hasattr(
        InMemoryRuntimeAdapter(tmp_path / "runtime"),
        "retire",
    )


def _review_evidence(binding: RuntimeBinding, candidate_sha: str):
    from gwo_v8 import TypedEvidence

    return TypedEvidence._capture(
        kind="review",
        subject=candidate_sha,
        observer_type="kernel",
        observer_id=str(binding.runtime_id),
        observed_at="2026-07-26T00:00:00+00:00",
        source_ref=f"github://review/{candidate_sha}",
        payload={
            "attempt_id": binding.attempt_id,
            "candidate_sha": candidate_sha,
            "axes": [],
        },
    )


def _review_binding(
    record: PaseoAgentRecord,
    worker: RuntimeBinding,
    candidate_sha: str,
) -> ReviewAxisBinding:
    return ReviewAxisBinding(
        action_key="review:retirement",
        axis="spec",
        candidate_sha=candidate_sha,
        fixed_input_digest="3" * 64,
        runtime_id=record.agent_id,
        agent_id=record.agent_id,
        session_id=record.session_id,
        workspace_id=record.workspace_id,
        workspace=record.workspace,
        parent_agent_id=worker.agent_id,
        runtime_profile="reviewer-standard",
        profile_digest=record.profile_digest,
        provider=record.provider,
        model=record.model,
        thinking=record.thinking,
        mode=record.mode,
        prompt_digest="4" * 64,
    )


def _review_record(
    worker: RuntimeBinding,
    *,
    workspace: Path,
    workspace_id: str,
    candidate_sha: str,
) -> PaseoAgentRecord:
    return replace(
        _paseo_record(worker),
        agent_id="agent:review-child",
        session_id="session:review-child",
        workspace_id=workspace_id,
        workspace=str(workspace),
        labels={
            "gwo.action_key": "review:retirement",
            "gwo.repository": worker.repository,
            "gwo.review_attempt": str(worker.attempt_id),
            "gwo.review_candidate": candidate_sha,
            "gwo.review_axis": "spec",
            "gwo.parent_agent": str(worker.agent_id),
        },
    )


def test_review_retirement_removes_independent_disposable_worktree(tmp_path):
    from gwo_v8.retirement import authorize_review_after_evidence

    repository, base_sha = _repository(tmp_path)
    candidate_workspace, candidate_sha = _candidate(repository, tmp_path, base_sha)
    worker = _binding(repository, candidate_workspace)
    review_workspace = tmp_path / "gwo-review-spec"
    _git(
        repository,
        "worktree",
        "add",
        "-b",
        "gwo/review/spec",
        str(review_workspace),
        candidate_sha,
    )
    review_record = _review_record(
        worker,
        workspace=review_workspace,
        workspace_id="wks_01reviewindependent",
        candidate_sha=candidate_sha,
    )
    client = _NativeArchivingPaseoClient(
        repository,
        review_record,
        (_paseo_record(worker),),
    )
    runtime = PaseoRuntimeAdapter(client)
    review_binding = _review_binding(review_record, worker, candidate_sha)
    authorization = authorize_review_after_evidence(
        worker_binding=worker,
        review_binding=review_binding,
        review_evidence=_review_evidence(worker, candidate_sha),
    )

    receipt = runtime.retire_review_after_evidence(
        review_binding,
        authorization,
    )

    assert receipt.complete is True
    assert receipt.workspace_disposition == "disposable_removed"
    assert client.archived_worktree_names == [review_workspace.name]
    assert not review_workspace.exists()
    assert candidate_workspace.exists()


def test_review_retirement_preserves_shared_candidate_workspace(tmp_path):
    from gwo_v8.retirement import authorize_review_after_evidence

    repository, base_sha = _repository(tmp_path)
    candidate_workspace, candidate_sha = _candidate(repository, tmp_path, base_sha)
    worker = _binding(repository, candidate_workspace)
    review_record = _review_record(
        worker,
        workspace=candidate_workspace,
        workspace_id=str(worker.workspace_id),
        candidate_sha=candidate_sha,
    )
    client = _NativeArchivingPaseoClient(
        repository,
        review_record,
        (_paseo_record(worker),),
    )
    runtime = PaseoRuntimeAdapter(client)
    review_binding = _review_binding(review_record, worker, candidate_sha)
    authorization = authorize_review_after_evidence(
        worker_binding=worker,
        review_binding=review_binding,
        review_evidence=_review_evidence(worker, candidate_sha),
    )

    receipt = runtime.retire_review_after_evidence(
        review_binding,
        authorization,
    )

    assert receipt.complete is True
    assert receipt.workspace_disposition == "shared_preserved"
    assert client.inspect(review_record.agent_id).archived is True
    assert client.archived_worktree_names == []
    assert candidate_workspace.exists()


@pytest.mark.parametrize(
    ("unsafe_kind", "expected_code"),
    [
        ("stable", "REVIEW_RETIREMENT_STABLE_WORKSPACE"),
        ("dirty", "REVIEW_RETIREMENT_WORKTREE_DIRTY"),
    ],
)
def test_review_retirement_fails_closed_for_unsafe_workspace(
    tmp_path,
    unsafe_kind,
    expected_code,
):
    from gwo_v8.retirement import authorize_review_after_evidence

    repository, base_sha = _repository(tmp_path)
    candidate_workspace, candidate_sha = _candidate(repository, tmp_path, base_sha)
    worker = _binding(repository, candidate_workspace)
    if unsafe_kind == "stable":
        review_workspace = repository
        workspace_id = "wks_01reviewstable"
    else:
        review_workspace = tmp_path / "gwo-review-dirty"
        workspace_id = "wks_01reviewdirty"
        _git(
            repository,
            "worktree",
            "add",
            "-b",
            "gwo/review/dirty",
            str(review_workspace),
            candidate_sha,
        )
        (review_workspace / "untracked-wip.txt").write_text(
            "wip\n",
            encoding="utf-8",
        )
    review_record = _review_record(
        worker,
        workspace=review_workspace,
        workspace_id=workspace_id,
        candidate_sha=candidate_sha,
    )
    client = _NativeArchivingPaseoClient(
        repository,
        review_record,
        (_paseo_record(worker),),
    )
    runtime = PaseoRuntimeAdapter(client)
    review_binding = _review_binding(review_record, worker, candidate_sha)
    authorization = authorize_review_after_evidence(
        worker_binding=worker,
        review_binding=review_binding,
        review_evidence=_review_evidence(worker, candidate_sha),
    )

    with pytest.raises(RuntimeAdapterError) as error:
        runtime.retire_review_after_evidence(
            review_binding,
            authorization,
        )

    assert error.value.code == expected_code
    assert client.inspect(review_record.agent_id).archived is False
    assert client.archived_worktree_names == []
    assert review_workspace.exists()


def test_review_retirement_rejects_wrong_child_authorization(tmp_path):
    from gwo_v8._canonical import digest_value
    from gwo_v8.retirement import (
        ReviewRetirementAuthorization,
        authorize_review_after_evidence,
    )

    repository, base_sha = _repository(tmp_path)
    candidate_workspace, candidate_sha = _candidate(repository, tmp_path, base_sha)
    worker = _binding(repository, candidate_workspace)
    review_workspace = tmp_path / "gwo-review-wrong-child"
    _git(
        repository,
        "worktree",
        "add",
        "-b",
        "gwo/review/wrong-child",
        str(review_workspace),
        candidate_sha,
    )
    review_record = _review_record(
        worker,
        workspace=review_workspace,
        workspace_id="wks_01reviewwrongchild",
        candidate_sha=candidate_sha,
    )
    client = _NativeArchivingPaseoClient(
        repository,
        review_record,
        (_paseo_record(worker),),
    )
    runtime = PaseoRuntimeAdapter(client)
    review_binding = _review_binding(review_record, worker, candidate_sha)
    authorization = authorize_review_after_evidence(
        worker_binding=worker,
        review_binding=review_binding,
        review_evidence=_review_evidence(worker, candidate_sha),
    )
    identity = {
        **authorization.identity,
        "agent_id": "agent:wrong-review-child",
    }
    wrong = ReviewRetirementAuthorization(
        **identity,
        authorization_digest=digest_value(identity),
    )

    with pytest.raises(RuntimeAdapterError) as error:
        runtime.retire_review_after_evidence(review_binding, wrong)

    assert (
        error.value.code
        == "REVIEW_RETIREMENT_AUTHORIZATION_IDENTITY_MISMATCH"
    )
    assert review_workspace.exists()
    assert client.inspect(review_record.agent_id).archived is False


def test_kernel_persists_authorization_failure_then_retries(tmp_path, monkeypatch):
    import gwo_v8.kernel as kernel_module
    from gwo_v8.retirement import RetirementError

    runtime = InMemoryRuntimeAdapter(tmp_path / "runtime-workspaces")
    kernel, repository = _kernel_with_runtime(tmp_path, runtime)
    batch_ready = kernel.reconcile_once("local/retirement")
    assert batch_ready.candidate_sha is not None
    _git(repository, "merge", "--ff-only", batch_ready.candidate_sha)
    original = kernel_module.authorize_after_integration

    def _fail_authorization(**_kwargs):
        raise RetirementError(
            "RETIREMENT_WORKTREE_AMBIGUOUS",
            "synthetic authorization readback gap",
        )

    monkeypatch.setattr(
        kernel_module,
        "authorize_after_integration",
        _fail_authorization,
    )

    waiting = kernel.reconcile_once("local/retirement")

    assert waiting.status == "waiting"
    assert waiting.retirement_state == "error"
    assert waiting.last_retirement_error == {
        "code": "RETIREMENT_WORKTREE_AMBIGUOUS",
        "failure_class": "ambiguous",
    }
    monkeypatch.setattr(
        kernel_module,
        "authorize_after_integration",
        original,
    )

    completed = kernel.reconcile_once("local/retirement")

    assert completed.status == "complete"
    assert completed.retirement_state == "complete"
    assert runtime.read_binding(waiting.admission_id) is None


def test_issue_88_retirement_adr_explicitly_supersedes_adr_0029_cleanup():
    root = Path(__file__).resolve().parents[1]
    adr = (
        root
        / "docs"
        / "adr"
        / "0041-require-read-backed-post-integration-retirement.md"
    )

    text = adr.read_text(encoding="utf-8")

    assert "status: accepted" in text
    assert "supersedes: 0029" in text
    assert "Resource cleanup is Kernel-owned follow-up and does not hold the Goal open." in text
    assert "retirement complete" in text


@pytest.mark.parametrize(
    ("unsafe_kind", "expected_code"),
    [
        ("dirty", "RETIREMENT_WORKTREE_DIRTY"),
        ("shared", "RETIREMENT_SHARED_WORKSPACE"),
        ("stable", "RETIREMENT_STABLE_WORKSPACE"),
    ],
)
def test_paseo_retirement_fails_closed_for_unsafe_workspaces(
    tmp_path,
    unsafe_kind,
    expected_code,
):
    from gwo_v8.retirement import authorize_after_integration

    repository, base_sha = _repository(tmp_path)
    if unsafe_kind == "stable":
        (repository / "candidate.txt").write_text("candidate\n", encoding="utf-8")
        _git(repository, "add", "candidate.txt")
        _git(repository, "commit", "-m", "candidate")
        candidate_sha = _git(repository, "rev-parse", "HEAD")
        binding = _binding(repository, repository)
        binding = replace(
            binding,
            workspace_id=repository.name,
            base_sha=base_sha,
        )
    else:
        workspace, candidate_sha = _candidate(repository, tmp_path, base_sha)
        binding = _binding(repository, workspace)
        _git(repository, "merge", "--ff-only", candidate_sha)
        if unsafe_kind == "dirty":
            (workspace / "untracked-wip.txt").write_text("wip\n", encoding="utf-8")
    record = _paseo_record(binding)
    extras: tuple[PaseoAgentRecord, ...] = ()
    if unsafe_kind == "shared":
        extras = (
            replace(
                record,
                agent_id="agent:review-child",
                session_id="session:review-child",
                labels={"gwo.repository": binding.repository},
            ),
        )
    client = _NativeArchivingPaseoClient(repository, record, extras)
    runtime = PaseoRuntimeAdapter(client)
    authorization = authorize_after_integration(
        binding=binding,
        candidate_sha=candidate_sha,
        integrated_sha=candidate_sha,
        target_branch="main",
    )

    with pytest.raises(RuntimeAdapterError) as error:
        runtime.retire_after_integration(binding, authorization)

    assert error.value.code == expected_code
    assert client.record.archived is False
    assert client.archived_worktree_names == []


def test_paseo_partial_archive_failure_retries_exact_branch_cas(
    tmp_path,
    monkeypatch,
):
    import gwo_v8.retirement as retirement

    repository, base_sha = _repository(tmp_path)
    workspace, candidate_sha = _candidate(repository, tmp_path, base_sha)
    binding = _binding(repository, workspace)
    _git(repository, "merge", "--ff-only", candidate_sha)
    client = _NativeArchivingPaseoClient(repository, _paseo_record(binding))
    runtime = PaseoRuntimeAdapter(client)
    authorization = retirement.authorize_after_integration(
        binding=binding,
        candidate_sha=candidate_sha,
        integrated_sha=candidate_sha,
        target_branch="main",
    )
    original_cas = retirement.delete_temporary_branch_cas

    def _fail_after_native_archive(*_args, **_kwargs):
        raise retirement.RetirementError(
            "RETIREMENT_BRANCH_SYNTHETIC_FAILURE",
            "synthetic failure after worktree archive",
        )

    monkeypatch.setattr(
        retirement,
        "delete_temporary_branch_cas",
        _fail_after_native_archive,
    )

    with pytest.raises(RuntimeAdapterError):
        runtime.retire_after_integration(binding, authorization)

    assert client.inspect(str(binding.agent_id)).archived is True
    assert not workspace.exists()
    assert _git(
        repository,
        "rev-parse",
        "refs/heads/gwo/attempt/retirement-worker",
    ) == candidate_sha

    monkeypatch.setattr(
        retirement,
        "delete_temporary_branch_cas",
        original_cas,
    )

    readback = runtime.retire_after_integration(binding, authorization)

    assert readback.complete is True
    assert (
        subprocess.run(
            [
                "git",
                "show-ref",
                "--verify",
                "--quiet",
                "refs/heads/gwo/attempt/retirement-worker",
            ],
            cwd=repository,
        ).returncode
        != 0
    )


def test_branch_cas_accepts_native_archive_already_deleted_exact_branch(
    tmp_path,
):
    from gwo_v8.retirement import (
        WorktreeRegistration,
        delete_temporary_branch_cas,
    )

    repository, base_sha = _repository(tmp_path)
    workspace, candidate_sha = _candidate(repository, tmp_path, base_sha)
    registration = WorktreeRegistration(
        head=candidate_sha,
        branch="gwo/attempt/retirement-worker",
    )
    _git(repository, "worktree", "remove", "--force", str(workspace))
    _git(
        repository,
        "update-ref",
        "-d",
        "refs/heads/gwo/attempt/retirement-worker",
        candidate_sha,
    )

    delete_temporary_branch_cas(
        repository,
        registration,
        candidate_sha=candidate_sha,
    )

    assert (
        subprocess.run(
            [
                "git",
                "show-ref",
                "--verify",
                "--quiet",
                "refs/heads/gwo/attempt/retirement-worker",
            ],
            cwd=repository,
        ).returncode
        != 0
    )


@pytest.mark.parametrize(
    ("tamper", "expected_code"),
    [
        ("digest", "RETIREMENT_AUTHORIZATION_DIGEST_MISMATCH"),
        ("ownership", "RETIREMENT_AUTHORIZATION_IDENTITY_MISMATCH"),
        ("candidate", "RETIREMENT_CANDIDATE_MISMATCH"),
    ],
)
def test_retirement_rejects_digest_ownership_and_candidate_mismatch(
    tmp_path,
    tamper,
    expected_code,
):
    from gwo_v8._canonical import digest_value
    from gwo_v8.retirement import (
        RetirementAuthorization,
        authorize_after_integration,
    )

    repository, base_sha = _repository(tmp_path)
    workspace, candidate_sha = _candidate(repository, tmp_path, base_sha)
    binding = _binding(repository, workspace)
    _git(repository, "merge", "--ff-only", candidate_sha)
    client = _NativeArchivingPaseoClient(repository, _paseo_record(binding))
    runtime = PaseoRuntimeAdapter(client)
    authorization = authorize_after_integration(
        binding=binding,
        candidate_sha=candidate_sha,
        integrated_sha=candidate_sha,
        target_branch="main",
    )
    if tamper == "digest":
        authorization = replace(
            authorization,
            authorization_digest="0" * 64,
        )
    else:
        identity = {
            **authorization.identity,
            (
                "agent_id" if tamper == "ownership" else "candidate_sha"
            ): (
                "agent:other" if tamper == "ownership" else "0" * 40
            ),
        }
        authorization = RetirementAuthorization(
            **identity,
            authorization_digest=digest_value(identity),
        )

    with pytest.raises(RuntimeAdapterError) as error:
        runtime.retire_after_integration(binding, authorization)

    assert error.value.code == expected_code
    assert client.inspect(str(binding.agent_id)).archived is False
    assert workspace.exists()
