from __future__ import annotations

from dataclasses import replace
import errno
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest


SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "orchestrator" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from gwo_v8 import (  # noqa: E402
    Kernel,
    PaseoAgentRecord,
    PaseoCliClient,
    PaseoCreateRequest,
    PaseoRuntimeAdapter,
    ReviewAxisRequest,
    RuntimeAdapterError,
    RuntimeAdmission,
    RuntimeProfile,
    RuntimePrompt,
)
from gwo_v8.runtime import PASEO_INLINE_PROMPT_MAX_BYTES  # noqa: E402


def _prompt(byte_floor: int, *, name: str = "transport") -> RuntimePrompt:
    text = json.dumps(
        {
            "instruction": f"{name} probe",
            "payload": "x" * byte_floor,
        },
        separators=(",", ":"),
    )
    return RuntimePrompt(
        text=text,
        digest=hashlib.sha256(text.encode("utf-8")).hexdigest(),
    )


def _profile() -> RuntimeProfile:
    return RuntimeProfile(
        name="transport-test",
        provider="codex",
        model="test-model",
        thinking="high",
        mode="full-access",
        features={},
    )


def _record(
    profile: RuntimeProfile,
    *,
    agent_id: str = "agent-1",
    workspace: str = "C:/workspace",
    lifecycle: str = "idle",
    parent_agent_id: str | None = None,
) -> PaseoAgentRecord:
    return PaseoAgentRecord(
        agent_id=agent_id,
        session_id=f"session-{agent_id}",
        workspace_id=f"workspace-{agent_id}",
        workspace=workspace,
        parent_agent_id=parent_agent_id,
        provider=profile.provider,
        model=profile.model,
        profile_digest=profile.digest,
        thinking=profile.thinking,
        mode=profile.mode,
        features=dict(profile.features),
        labels={},
        lifecycle=lifecycle,
    )


def test_cli_large_create_uses_bootstrap_then_prompt_file(monkeypatch):
    client = PaseoCliClient(executable="paseo")
    prompt = _prompt(310_000)
    profile = _profile()
    record = _record(profile)
    labels: dict[str, str] = {}
    activity = ""
    commands: list[list[str]] = []
    prompt_paths: list[Path] = []
    sent_contents: list[str] = []

    def find_by_labels(expected):
        if all(labels.get(key) == value for key, value in expected.items()):
            return (
                replace(
                    record,
                    labels=dict(expected),
                    profile_digest=expected.get(
                        "gwo.profile_digest",
                        record.profile_digest,
                    ),
                ),
            )
        return ()

    def run(command, **_kwargs):
        nonlocal activity
        commands.append(command)
        if command[0] == "run":
            for index, part in enumerate(command):
                if part == "--label":
                    key, value = command[index + 1].split("=", 1)
                    labels[key] = value
            activity += f"[User] {command[-1]}\n"
            return {"agentId": record.agent_id}
        if command[0] == "send":
            path = Path(command[command.index("--prompt-file") + 1])
            prompt_paths.append(path)
            sent = path.read_text(encoding="utf-8")
            sent_contents.append(sent)
            activity += f"[User] {sent}\n"
            return {"status": "sent"}
        if command[:2] == ["agent", "update"]:
            for index, part in enumerate(command):
                if part == "--label":
                    key, value = command[index + 1].split("=", 1)
                    labels[key] = value
            return {"status": "updated"}
        raise AssertionError(command)

    monkeypatch.setattr(client, "_run", run)
    monkeypatch.setattr(client, "_run_text", lambda *_args, **_kwargs: activity)
    monkeypatch.setattr(client, "find_by_labels", find_by_labels)

    created = client.create(
        PaseoCreateRequest(
            action_key="worker:large",
            title="large worker",
            labels={
                "gwo.admission": "admission-1",
                "gwo.profile_digest": profile.digest,
            },
            prompt=prompt,
            repository_path="C:/repository",
            base_sha="b" * 40,
            profile=profile,
            parent_agent_id=None,
        )
    )

    run_command = commands[0]
    send_command = next(command for command in commands if command[0] == "send")
    assert prompt.text not in run_command
    assert "GWO transport bootstrap" in run_command[-1]
    assert max(len(part.encode("utf-8")) for part in run_command) <= (
        PASEO_INLINE_PROMPT_MAX_BYTES
    )
    assert "--prompt-file" in send_command
    assert sent_contents == [prompt.text]
    assert len(sent_contents[0].encode("utf-8")) > 300_000
    assert all(not path.exists() for path in prompt_paths)
    assert created.labels["gwo.prompt_digest"] == prompt.digest
    assert client.prompt_acceptance_count(record.agent_id, prompt) == 1


def test_cli_activity_readback_requires_an_exact_user_message_boundary(
    monkeypatch,
):
    client = PaseoCliClient(executable="paseo")
    prompt = _prompt(1_024)
    monkeypatch.setattr(
        client,
        "_run_text",
        lambda *_args, **_kwargs: (
            f"prefix [User] {prompt.text}\n"
            f"[User] {prompt.text} suffix\n"
            f"[User] {prompt.text}\n"
        ),
    )

    assert client.prompt_acceptance_count("agent-1", prompt) == 1


def test_windows_command_line_overflow_is_not_executable_absence(monkeypatch):
    client = PaseoCliClient(executable="paseo")

    def overflow(*_args, **_kwargs):
        raise OSError(errno.E2BIG, "The filename or extension is too long")

    monkeypatch.setattr("gwo_v8.runtime.subprocess.run", overflow)
    with pytest.raises(RuntimeAdapterError) as captured:
        client._run(["inspect", "agent-1", "--json"])
    assert captured.value.code == "PASEO_COMMAND_LINE_OVERFLOW"

    def missing(*_args, **_kwargs):
        raise FileNotFoundError(errno.ENOENT, "missing executable")

    monkeypatch.setattr("gwo_v8.runtime.subprocess.run", missing)
    with pytest.raises(RuntimeAdapterError) as captured:
        client._run(["inspect", "agent-1", "--json"])
    assert captured.value.code == "PASEO_EXECUTABLE_UNAVAILABLE"


class RestartBlindPaseoClient:
    """Paseo-shaped fake whose inspect surface never returns labels."""

    def __init__(self, *, send_acceptances: tuple[bool, ...] = ()):
        self._records: dict[str, PaseoAgentRecord] = {}
        self._labels: dict[str, dict[str, str]] = {}
        self._accepted: dict[str, list[str]] = {}
        self._send_acceptances = list(send_acceptances)
        self.create_count = 0
        self.send_count = 0

    def observed_worker_turn_capacity(self, _profile):
        return None

    def find_by_labels(self, labels):
        matches = []
        for agent_id, hidden in self._labels.items():
            record = self._records[agent_id]
            if record.archived or not all(
                hidden.get(key) == value for key, value in labels.items()
            ):
                continue
            matches.append(
                replace(
                    record,
                    labels=dict(labels),
                    profile_digest=labels.get(
                        "gwo.profile_digest",
                        record.profile_digest,
                    ),
                    parent_agent_id=labels.get(
                        "gwo.parent_agent",
                        record.parent_agent_id,
                    ),
                )
            )
        return tuple(matches)

    def create(self, request):
        existing = self.find_by_labels({"gwo.action_key": request.action_key})
        if existing:
            return existing[0]
        self.create_count += 1
        agent_id = f"agent-{self.create_count}"
        record = _record(
            request.profile,
            agent_id=agent_id,
            workspace=str(
                Path(request.repository_path) / f".paseo-{self.create_count}"
            ),
            lifecycle="idle",
            parent_agent_id=request.parent_agent_id,
        )
        self._records[agent_id] = record
        self._labels[agent_id] = {
            **request.labels,
            "gwo.action_key": request.action_key,
        }
        self._accepted[agent_id] = []
        return replace(record, labels=dict(request.labels))

    def inspect(self, agent_id):
        return replace(self._records[agent_id], labels={}, profile_digest="")

    def send_prompt(self, agent_id, prompt, *, action_key):
        del action_key
        self.send_count += 1
        accepted = True if not self._send_acceptances else self._send_acceptances.pop(0)
        if accepted:
            self._accepted[agent_id].append(prompt.digest)
        self._records[agent_id] = replace(
            self._records[agent_id],
            lifecycle="running" if accepted else "idle",
        )

    def prompt_acceptance_count(self, agent_id, prompt):
        return self._accepted[agent_id].count(prompt.digest)

    def update_labels(self, agent_id, labels):
        self._labels[agent_id].update(labels)

    def read_output(self, _agent_id):
        return None

    def stop(self, agent_id):
        self._records[agent_id] = replace(
            self._records[agent_id],
            lifecycle="idle",
        )

    def resume(self, agent_id):
        self._records[agent_id] = replace(
            self._records[agent_id],
            lifecycle="running",
        )

    def archive(self, agent_id):
        self._records[agent_id] = replace(
            self._records[agent_id],
            lifecycle="archived",
            archived=True,
        )


def test_worker_restart_replays_dropped_ack_on_same_agent_exactly_once(tmp_path):
    prompt = _prompt(310_000, name="worker")
    profile = _profile()
    admission = RuntimeAdmission(
        repository="local/transport",
        plan_digest="p" * 64,
        node_key="node:transport",
        admission_id="admission:transport",
        repository_path=tmp_path,
        base_sha="b" * 40,
        runtime_profile=profile,
    )
    client = RestartBlindPaseoClient(send_acceptances=(False, True))
    first_adapter = PaseoRuntimeAdapter(client)

    pending = first_adapter.materialize(admission, prompt)
    assert pending.prompt_accepted is False
    with pytest.raises(RuntimeAdapterError) as dropped:
        first_adapter.accept_prompt(pending, prompt)
    assert dropped.value.code == "PROMPT_ACCEPTANCE_AMBIGUOUS"
    assert client.create_count == 1
    assert client.send_count == 1

    restarted = PaseoRuntimeAdapter(client)
    adopted = restarted.read_binding(admission, prompt)
    assert adopted is not None
    assert adopted.agent_id == pending.agent_id
    assert adopted.prompt_accepted is False
    assert client.inspect(adopted.agent_id).labels == {}

    restarted.accept_prompt(adopted, prompt)
    accepted = restarted.read_binding(admission, prompt)
    assert accepted is not None
    assert accepted.agent_id == pending.agent_id
    assert accepted.prompt_accepted is True
    assert accepted.prompt_digest == prompt.digest
    assert client.create_count == 1
    assert client.send_count == 2
    assert client.prompt_acceptance_count(accepted.agent_id, prompt) == 1

    second_restart = PaseoRuntimeAdapter(client)
    readback = second_restart.read_binding(admission, prompt)
    assert readback is not None
    assert readback.agent_id == pending.agent_id
    assert readback.prompt_accepted is True
    assert client.send_count == 2


def _git(repository: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repository), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _repository(tmp_path: Path) -> tuple[Path, str]:
    repository = tmp_path / "repository"
    subprocess.run(
        ["git", "init", "-b", "main", str(repository)],
        check=True,
        capture_output=True,
    )
    _git(repository, "config", "user.name", "Transport Test")
    _git(repository, "config", "user.email", "transport@example.test")
    (repository / "README.md").write_text("transport\n", encoding="utf-8")
    _git(repository, "add", "README.md")
    _git(repository, "commit", "-m", "transport base")
    return repository, _git(repository, "rev-parse", "HEAD")


def test_dual_review_axes_above_mcp_limit_adopt_after_restart(tmp_path):
    repository, candidate_sha = _repository(tmp_path)
    profile = _profile()
    client = RestartBlindPaseoClient()
    adapter = PaseoRuntimeAdapter(client)
    parent_agent_id = "worker-parent"
    requests = [
        ReviewAxisRequest(
            repository="local/transport",
            attempt_id="attempt:transport",
            candidate_sha=candidate_sha,
            base_sha=candidate_sha,
            axis=axis,
            recovery_ordinal=0,
            workspace=repository,
            diff_command=("git", "diff", f"{candidate_sha}...{candidate_sha}"),
            commit_list=("transport candidate",),
            spec_source_ref="synthetic://issue/63",
            spec_text=json.dumps(
                {
                    "axis": axis,
                    "payload": axis[0] * 180_000,
                },
                separators=(",", ":"),
            ),
            standards_sources=("AGENTS.md", "CONTEXT.md"),
            check_manifest_digest="c" * 64,
        )
        for axis in ("standards", "spec")
    ]
    assert all(
        len(request.to_prompt().text.encode("utf-8")) > 170_000 for request in requests
    )

    bindings = {
        request.axis: adapter.materialize_review_axis(
            request,
            profile,
            parent_agent_id=parent_agent_id,
        )
        for request in requests
    }
    assert client.create_count == 2
    assert client.send_count == 2
    assert all(
        client.prompt_acceptance_count(
            bindings[request.axis].agent_id,
            request.to_prompt(),
        )
        == 1
        for request in requests
    )

    restarted = PaseoRuntimeAdapter(client)
    adopted = {
        request.axis: restarted.materialize_review_axis(
            request,
            profile,
            parent_agent_id=parent_agent_id,
        )
        for request in requests
    }
    assert client.create_count == 2
    assert client.send_count == 2
    assert {axis: binding.agent_id for axis, binding in adopted.items()} == {
        axis: binding.agent_id for axis, binding in bindings.items()
    }


def test_review_prompts_keep_spec_authority_without_repeating_file_contents():
    content = "frozen-content-" * 25_000
    compacted = Kernel._compact_review_contract(
        {
            "file_changes": [
                {
                    "path": "src/generated.txt",
                    "content": content,
                }
            ]
        }
    )
    reference = compacted["file_changes"][0]
    assert reference == {
        "path": "src/generated.txt",
        "content_digest": hashlib.sha256(content.encode("utf-8")).hexdigest(),
        "content_bytes": len(content.encode("utf-8")),
    }
    spec_text = json.dumps(compacted, separators=(",", ":"))
    requests = [
        ReviewAxisRequest(
            repository="local/transport",
            attempt_id="attempt:transport",
            candidate_sha="a" * 40,
            base_sha="b" * 40,
            axis=axis,
            recovery_ordinal=0,
            workspace=Path("C:/candidate"),
            diff_command=("git", "diff", "b...a"),
            commit_list=("candidate",),
            spec_source_ref="synthetic://issue/63",
            spec_text=spec_text,
            standards_sources=("AGENTS.md",),
            check_manifest_digest="c" * 64,
        )
        for axis in ("standards", "spec")
    ]

    for request in requests:
        rendered = request.to_prompt().text
        assert content not in rendered
        assert "src/generated.txt" in rendered
        assert reference["content_digest"] in rendered
