from __future__ import annotations

from dataclasses import asdict, replace
import hashlib
import json
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


def test_paseo_client_archives_only_a_parsed_worktree_name(monkeypatch):
    client = PaseoCliClient("paseo")
    commands: list[list[str]] = []

    def _record(command, **_kwargs):
        commands.append(command)
        return {}

    monkeypatch.setattr(client, "_run", _record)

    client.archive_worktree("gwo-0123456789abcdef")

    assert commands == [
        ["worktree", "archive", "gwo-0123456789abcdef", "--json"]
    ]


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

    def archive_worktree(self, worktree_name):
        assert worktree_name == self.record.workspace_id
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


def test_review_child_retirement_archives_identity_without_shared_workspace_delete(
    tmp_path,
):
    repository, base_sha = _repository(tmp_path)
    workspace, candidate_sha = _candidate(repository, tmp_path, base_sha)
    worker_binding = _binding(repository, workspace)
    record = replace(
        _paseo_record(worker_binding),
        agent_id="agent:review-child",
        session_id="session:review-child",
    )
    client = _NativeArchivingPaseoClient(repository, record)
    runtime = PaseoRuntimeAdapter(client)
    review_binding = ReviewAxisBinding(
        action_key="review:retirement",
        axis="spec",
        candidate_sha=candidate_sha,
        fixed_input_digest="3" * 64,
        runtime_id=record.agent_id,
        agent_id=record.agent_id,
        session_id=record.session_id,
        workspace_id=record.workspace_id,
        workspace=record.workspace,
        parent_agent_id=worker_binding.agent_id,
        runtime_profile="reviewer-standard",
        profile_digest=record.profile_digest,
        provider=record.provider,
        model=record.model,
        thinking=record.thinking,
        mode=record.mode,
        prompt_digest="4" * 64,
    )

    runtime.retire_review_axis(review_binding)

    assert client.inspect(record.agent_id).archived is True
    assert client.archived_worktree_names == []
    assert workspace.exists()
    assert workspace.resolve().as_posix() in _git(
        repository,
        "worktree",
        "list",
        "--porcelain",
    )


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
