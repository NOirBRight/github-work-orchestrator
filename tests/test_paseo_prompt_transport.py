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
    InMemoryPaseoClient,
    PaseoAgentRecord,
    PaseoCliClient,
    PaseoRuntimeAdapter,
    ReviewAxisRequest,
    RuntimeAdapterError,
    RuntimeAdmission,
    RuntimeProfile,
    RuntimePrompt,
)
from gwo_v8.review_convergence import ReviewConvergence  # noqa: E402
from gwo_v8.runtime import (  # noqa: E402
    PASEO_INLINE_PROMPT_MAX_BYTES,
    _paseo_bootstrap_prompt,
)
from scripts.run_paseo_transport_e2e import _archive_agents  # noqa: E402


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
    declared_parent_agent_id: str | None = None,
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
        declared_parent_agent_id=declared_parent_agent_id,
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

    admission = RuntimeAdmission(
        repository="local/large",
        plan_digest="p" * 64,
        node_key="node:large",
        admission_id="admission-1",
        repository_path=Path("C:/repository"),
        base_sha="b" * 40,
        runtime_profile=profile,
    )
    adapter = PaseoRuntimeAdapter(client)
    pending = adapter.materialize(admission, prompt)
    assert pending.prompt_accepted is False
    assert all(command[0] != "send" for command in commands)

    adapter.accept_prompt(pending, prompt)
    accepted = adapter.read_binding(admission, prompt)

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
    assert accepted is not None
    assert accepted.prompt_digest == prompt.digest
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


def test_cli_parent_readback_preserves_declared_owner_without_native_finish(
    monkeypatch,
):
    client = PaseoCliClient(executable="paseo")
    profile = _profile()
    declared_parent = "coordinator-agent"
    labels = {
        "gwo.repository": "local/parent-readback",
        "gwo.plan": "p" * 64,
        "gwo.node": "node:parent-readback",
        "gwo.admission": "admission:parent-readback",
        "gwo.repository_path": "C:/repository",
        "gwo.runtime_profile": profile.name,
        "gwo.profile_digest": profile.digest,
        "gwo.parent_agent": declared_parent,
        "gwo.base_sha": "b" * 40,
    }
    payload = {
        "Id": "detached-child",
        "SessionId": "session-detached-child",
        "WorkspaceId": "workspace-detached-child",
        "Cwd": "C:/workspace",
        "ParentAgentId": None,
        "Provider": profile.provider,
        "Model": profile.model,
        "Thinking": profile.thinking,
        "Mode": profile.mode,
        "RuntimeSettings": {"features": {}},
        "Status": "idle",
        "Labels": {},
    }

    def run(command, **_kwargs):
        if command[0] == "ls":
            return [{"id": "detached-child"}]
        if command[0] == "inspect":
            return payload
        raise AssertionError(command)

    monkeypatch.setattr(client, "_run", run)

    records = client.find_by_labels(labels)
    assert len(records) == 1
    record = records[0]
    binding = PaseoRuntimeAdapter(client)._binding(record)

    assert record.parent_agent_id is None
    assert record.declared_parent_agent_id == declared_parent
    assert binding.parent_agent_id is None
    assert binding.declared_parent_agent_id == declared_parent
    assert binding.native_finish_notification_supported is False


def test_review_axis_materialization_cli_label_readback_excludes_archived_agents(
    monkeypatch,
):
    client = PaseoCliClient(executable="paseo")
    profile = _profile()
    labels = {
        "gwo.action_key": "review-action",
        "gwo.profile_digest": profile.digest,
    }

    def payload(agent_id, *, archived):
        return {
            "Id": agent_id,
            "SessionId": f"session-{agent_id}",
            "WorkspaceId": f"workspace-{agent_id}",
            "Cwd": f"C:/workspace/{agent_id}",
            "Provider": profile.provider,
            "Model": profile.model,
            "Thinking": profile.thinking,
            "Mode": profile.mode,
            "RuntimeSettings": {"features": {}},
            "Status": "idle",
            "Archived": archived,
            "Labels": labels,
        }

    payloads = {
        "active-review": payload("active-review", archived=False),
        "archived-review": payload("archived-review", archived=True),
    }
    commands = []

    def run(command, **_kwargs):
        commands.append(command)
        if command[0] == "ls":
            return [{"id": agent_id} for agent_id in payloads]
        if command[0] == "inspect":
            return payloads[command[1]]
        raise AssertionError(command)

    monkeypatch.setattr(client, "_run", run)

    records = client.find_by_labels({"gwo.action_key": "review-action"})

    assert [record.agent_id for record in records] == ["active-review"]
    assert records[0].archived is False
    assert "--all" not in commands[0]


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


def test_cli_only_classifies_explicit_no_enqueue_as_retryable(monkeypatch):
    client = PaseoCliClient(executable="paseo")
    prompt = _prompt(310_000)
    monkeypatch.setattr(
        client,
        "_run",
        lambda *_args, **_kwargs: {
            "status": "rejected",
            "enqueued": False,
        },
    )

    with pytest.raises(RuntimeAdapterError) as rejected:
        client.send_prompt("agent-1", prompt, action_key="action-1")

    assert rejected.value.code == "PASEO_PROMPT_REJECTED"
    assert rejected.value.failure_class == "transient"

    monkeypatch.setattr(
        client,
        "_run",
        lambda *_args, **_kwargs: {"status": "rejected"},
    )
    with pytest.raises(RuntimeAdapterError) as ambiguous:
        client.send_prompt("agent-1", prompt, action_key="action-1")

    assert ambiguous.value.code == "PASEO_PROMPT_ACK_INVALID"
    assert ambiguous.value.failure_class == "ambiguous"


class RestartBlindPaseoClient:
    """Paseo-shaped fake whose inspect surface never returns labels."""

    def __init__(self, *, send_acceptances: tuple[bool, ...] = ()):
        self._records: dict[str, PaseoAgentRecord] = {}
        self._labels: dict[str, dict[str, str]] = {}
        self._accepted: dict[str, list[str]] = {}
        self._send_acceptances = list(send_acceptances)
        self._create_receipt: tuple[str, str] | None = None
        self.create_count = 0
        self.send_count = 0
        self.sent_action_keys: list[str] = []

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
                    declared_parent_agent_id=labels.get(
                        "gwo.parent_agent",
                        record.declared_parent_agent_id,
                    ),
                )
            )
        return tuple(matches)

    def create(self, request):
        self._create_receipt = None
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
            declared_parent_agent_id=request.parent_agent_id,
        )
        self._records[agent_id] = record
        self._labels[agent_id] = {
            **request.labels,
            "gwo.action_key": request.action_key,
        }
        self._accepted[agent_id] = []
        self._create_receipt = (request.action_key, agent_id)
        return replace(record, labels=dict(request.labels))

    def consume_create_receipt(self, action_key, agent_id):
        expected = (action_key, agent_id)
        created = self._create_receipt == expected
        self._create_receipt = None
        return created

    def inspect(self, agent_id):
        return replace(self._records[agent_id], labels={}, profile_digest="")

    def send_prompt(self, agent_id, prompt, *, action_key):
        self.sent_action_keys.append(action_key)
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

    def accept_prompt(self, agent_id, prompt):
        self._accepted[agent_id].append(prompt.digest)
        self._records[agent_id] = replace(
            self._records[agent_id],
            lifecycle="running",
        )

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


class ArchiveBlindPaseoClient(RestartBlindPaseoClient):
    def archive(self, agent_id):
        self.inspect(agent_id)


def test_live_e2e_archive_requires_authoritative_readback(tmp_path):
    prompt = _prompt(1_024, name="archive")
    profile = _profile()
    admission = RuntimeAdmission(
        repository="local/archive",
        plan_digest="a" * 64,
        node_key="node:archive",
        admission_id="admission:archive",
        repository_path=tmp_path,
        base_sha="b" * 40,
        runtime_profile=profile,
    )
    client = ArchiveBlindPaseoClient()
    binding = PaseoRuntimeAdapter(client).materialize(admission, prompt)

    with pytest.raises(AssertionError, match="archive did not read back"):
        _archive_agents(
            client,
            admission.repository,
            expected_agent_ids={binding.agent_id},
        )


def test_worker_restart_never_replays_an_acknowledged_send(tmp_path, monkeypatch):
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
    client = RestartBlindPaseoClient(send_acceptances=(False,))
    first_adapter = PaseoRuntimeAdapter(client)

    pending = first_adapter.materialize(admission, prompt)
    assert pending.prompt_accepted is False
    action_key = f"{admission.admission_id}:prompt"
    client.send_prompt(pending.agent_id, prompt, action_key=action_key)
    client.update_labels(
        pending.agent_id,
        {
            "gwo.prompt_delivery": first_adapter._delivery_label_value(
                "acked",
                0,
                action_key,
            )
        },
    )
    assert client.create_count == 1
    assert client.send_count == 1

    restarted = PaseoRuntimeAdapter(client)
    adopted = restarted.read_binding(admission, prompt)
    assert adopted is not None
    assert adopted.agent_id == pending.agent_id
    assert adopted.prompt_accepted is False
    assert client.inspect(adopted.agent_id).labels == {}

    monkeypatch.setattr("gwo_v8.runtime.PASEO_BOOTSTRAP_WAIT_SECONDS", 0.0)
    with pytest.raises(RuntimeAdapterError) as ambiguous:
        restarted.accept_prompt(adopted, prompt)
    assert ambiguous.value.code == "PROMPT_DELIVERY_AMBIGUOUS"
    assert ambiguous.value.failure_class == "ambiguous"
    assert client.create_count == 1
    assert client.send_count == 1
    assert client.sent_action_keys == [action_key]

    client.accept_prompt(adopted.agent_id, prompt)
    accepted = restarted.read_binding(admission, prompt)
    assert accepted is not None
    assert accepted.agent_id == pending.agent_id
    assert accepted.prompt_accepted is True
    assert accepted.prompt_digest == prompt.digest
    assert client.create_count == 1
    assert client.send_count == 1
    assert client.sent_action_keys == [action_key]
    assert client.prompt_acceptance_count(accepted.agent_id, prompt) == 1

    second_restart = PaseoRuntimeAdapter(client)
    readback = second_restart.read_binding(admission, prompt)
    assert readback is not None
    assert readback.agent_id == pending.agent_id
    assert readback.prompt_accepted is True
    assert client.send_count == 1


class AckLabelGapPaseoClient(RestartBlindPaseoClient):
    """Fresh-client fake with a durable ACK hidden from filtered readback."""

    def __init__(self, previous=None):
        if previous is None:
            super().__init__(send_acceptances=(False,))
        else:
            self._records = previous._records
            self._labels = previous._labels
            self._accepted = previous._accepted
            self._send_acceptances = previous._send_acceptances
            self._create_receipt = None
            self.create_count = previous.create_count
            self.send_count = previous.send_count
            self.sent_action_keys = previous.sent_action_keys
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


class CreateRacePaseoClient(RestartBlindPaseoClient):
    """Client that idempotently adopts an Agent created after lookup."""

    def create(self, request):
        raced_request = replace(
            request,
            labels={
                key: value
                for key, value in request.labels.items()
                if key != "gwo.create_receipt"
            },
        )
        record = super().create(raced_request)
        self.consume_create_receipt(request.action_key, record.agent_id)
        return record


class AmbiguousBootstrapOnlyReviewClient(RestartBlindPaseoClient):
    """Lost create response after the exact bootstrap was accepted."""

    def __init__(self):
        super().__init__()
        self._lose_first_create_response = True

    def create(self, request):
        record = super().create(request)
        if self._lose_first_create_response:
            self._lose_first_create_response = False
            bootstrap = _paseo_bootstrap_prompt(request.action_key)
            self._accepted[record.agent_id].append(bootstrap.digest)
            self._create_receipt = None
            raise RuntimeAdapterError(
                "PASEO_CREATE_AMBIGUOUS",
                "synthetic lost create response after bootstrap acceptance",
                failure_class="ambiguous",
            )
        return record


def test_worker_lookup_create_race_does_not_authorize_first_send(
    tmp_path,
    monkeypatch,
):
    prompt = _prompt(310_000, name="worker-create-race")
    admission = RuntimeAdmission(
        repository="local/worker-create-race",
        plan_digest="w" * 64,
        node_key="node:worker-create-race",
        admission_id="admission:worker-create-race",
        repository_path=tmp_path,
        base_sha="b" * 40,
        runtime_profile=_profile(),
    )
    client = CreateRacePaseoClient()
    adapter = PaseoRuntimeAdapter(client)
    pending = adapter.materialize(admission, prompt)

    monkeypatch.setattr("gwo_v8.runtime.PASEO_BOOTSTRAP_WAIT_SECONDS", 0.0)
    with pytest.raises(RuntimeAdapterError) as ambiguous:
        adapter.accept_prompt(pending, prompt)

    assert ambiguous.value.code == "PROMPT_DELIVERY_AMBIGUOUS"
    assert pending.prompt_accepted is False
    assert client.create_count == 1
    assert client.send_count == 0
    assert client.sent_action_keys == []
    assert "gwo.create_receipt" not in client._labels[pending.agent_id]


def test_ack_label_readback_gap_never_replays_across_fresh_client(
    tmp_path,
    monkeypatch,
):
    prompt = _prompt(310_000, name="ack-gap")
    admission = RuntimeAdmission(
        repository="local/ack-gap",
        plan_digest="g" * 64,
        node_key="node:ack-gap",
        admission_id="admission:ack-gap",
        repository_path=tmp_path,
        base_sha="b" * 40,
        runtime_profile=_profile(),
    )
    first_client = AckLabelGapPaseoClient()
    first_adapter = PaseoRuntimeAdapter(first_client)
    pending = first_adapter.materialize(admission, prompt)
    action_key = f"{admission.admission_id}:prompt"

    with pytest.raises(RuntimeAdapterError) as ack_gap:
        first_adapter.accept_prompt(pending, prompt)

    assert ack_gap.value.code == "PROMPT_DELIVERY_AMBIGUOUS"
    assert first_client.send_count == 1
    assert first_client.sent_action_keys == [action_key]
    assert first_client._labels[pending.agent_id]["gwo.prompt_delivery"] == (
        first_adapter._delivery_label_value("acked", 0, action_key)
    )

    restarted_client = AckLabelGapPaseoClient(first_client)
    restarted = PaseoRuntimeAdapter(restarted_client)
    adopted = restarted.read_binding(admission, prompt)
    assert adopted is not None
    assert adopted.agent_id == pending.agent_id
    assert adopted.prompt_accepted is False

    monkeypatch.setattr("gwo_v8.runtime.PASEO_BOOTSTRAP_WAIT_SECONDS", 0.0)
    with pytest.raises(RuntimeAdapterError) as hidden_receipt:
        restarted.accept_prompt(adopted, prompt)

    assert hidden_receipt.value.code == "PROMPT_DELIVERY_AMBIGUOUS"
    assert restarted_client.send_count == 1
    assert restarted_client.sent_action_keys == [action_key]

    restarted_client.ack_receipt_visible = True
    assert restarted._read_delivery_state(
        adopted.agent_id,
        {"gwo.admission": admission.admission_id},
        action_key,
    ) == ("acked", 0)
    with pytest.raises(RuntimeAdapterError) as visible_receipt:
        restarted.accept_prompt(adopted, prompt)

    assert visible_receipt.value.code == "PROMPT_DELIVERY_AMBIGUOUS"
    assert restarted_client.send_count == 1
    assert restarted_client.sent_action_keys == [action_key]

    restarted_client.accept_prompt(adopted.agent_id, prompt)
    accepted = PaseoRuntimeAdapter(
        AckLabelGapPaseoClient(restarted_client)
    ).read_binding(admission, prompt)

    assert accepted is not None
    assert accepted.agent_id == pending.agent_id
    assert accepted.prompt_accepted is True
    assert accepted.prompt_digest == prompt.digest
    assert restarted_client.send_count == 1
    assert restarted_client.prompt_acceptance_count(
        accepted.agent_id,
        prompt,
    ) == 1


def test_delayed_inline_create_boundary_never_authorizes_send(
    tmp_path,
    monkeypatch,
):
    prompt = _prompt(1_024, name="inline")
    profile = _profile()
    admission = RuntimeAdmission(
        repository="local/inline",
        plan_digest="i" * 64,
        node_key="node:inline",
        admission_id="admission:inline",
        repository_path=tmp_path,
        base_sha="b" * 40,
        runtime_profile=profile,
    )
    client = RestartBlindPaseoClient()
    adapter = PaseoRuntimeAdapter(client)

    pending = adapter.materialize(admission, prompt)
    assert pending.prompt_accepted is False
    assert client.send_count == 0
    action_key = f"{admission.admission_id}:prompt"
    expected_receipt = adapter._delivery_label_value(
        "acked",
        0,
        action_key,
    )
    assert client.find_by_labels(
        {
            "gwo.admission": admission.admission_id,
            "gwo.prompt_delivery": expected_receipt,
        }
    )

    monkeypatch.setattr("gwo_v8.runtime.PASEO_BOOTSTRAP_WAIT_SECONDS", 0.0)
    with pytest.raises(RuntimeAdapterError) as ambiguous:
        adapter.accept_prompt(pending, prompt)

    assert ambiguous.value.code == "PROMPT_DELIVERY_AMBIGUOUS"
    assert client.send_count == 0

    client.accept_prompt(pending.agent_id, prompt)
    accepted = adapter.read_binding(admission, prompt)
    assert accepted is not None
    assert accepted.prompt_accepted is True
    assert client.send_count == 0
    assert client.prompt_acceptance_count(accepted.agent_id, prompt) == 1


class DelayedAcceptancePaseoClient(RestartBlindPaseoClient):
    def __init__(self, *, visibility_reads: int):
        super().__init__()
        self._visibility_reads = visibility_reads
        self._pending: dict[str, tuple[str, int]] = {}

    def send_prompt(self, agent_id, prompt, *, action_key):
        self.sent_action_keys.append(action_key)
        self.send_count += 1
        self._pending[agent_id] = (
            prompt.digest,
            self._visibility_reads,
        )
        self._records[agent_id] = replace(
            self._records[agent_id],
            lifecycle="idle",
        )

    def prompt_acceptance_count(self, agent_id, prompt):
        pending = self._pending.get(agent_id)
        if pending is not None and pending[0] == prompt.digest:
            remaining = pending[1] - 1
            if remaining <= 0:
                self._accepted[agent_id].append(prompt.digest)
                self._pending.pop(agent_id)
            else:
                self._pending[agent_id] = (prompt.digest, remaining)
        return super().prompt_acceptance_count(agent_id, prompt)


def test_arbitrarily_delayed_first_boundary_never_replays(tmp_path, monkeypatch):
    prompt = _prompt(310_000, name="delayed")
    profile = _profile()
    admission = RuntimeAdmission(
        repository="local/delayed",
        plan_digest="d" * 64,
        node_key="node:delayed",
        admission_id="admission:delayed",
        repository_path=tmp_path,
        base_sha="b" * 40,
        runtime_profile=profile,
    )
    client = DelayedAcceptancePaseoClient(visibility_reads=100)
    adapter = PaseoRuntimeAdapter(client)
    pending = adapter.materialize(admission, prompt)

    monkeypatch.setattr("gwo_v8.runtime.PASEO_PROMPT_SETTLE_SECONDS", 0.0)
    adapter.accept_prompt(pending, prompt)
    accepted = adapter.read_binding(admission, prompt)

    assert accepted is not None
    assert accepted.prompt_accepted is True
    assert client.send_count == 1
    assert client.prompt_acceptance_count(accepted.agent_id, prompt) == 1


class RejectedOncePaseoClient(RestartBlindPaseoClient):
    def __init__(self):
        super().__init__()
        self._rejected = False

    def send_prompt(self, agent_id, prompt, *, action_key):
        if not self._rejected:
            self._rejected = True
            self.sent_action_keys.append(action_key)
            self.send_count += 1
            raise RuntimeAdapterError(
                "PASEO_PROMPT_REJECTED",
                "synthetic explicit pre-enqueue rejection",
                failure_class="transient",
            )
        super().send_prompt(
            agent_id,
            prompt,
            action_key=action_key,
        )


def test_explicit_pre_enqueue_rejection_permits_one_retry(tmp_path):
    prompt = _prompt(310_000, name="rejected")
    profile = _profile()
    admission = RuntimeAdmission(
        repository="local/rejected",
        plan_digest="r" * 64,
        node_key="node:rejected",
        admission_id="admission:rejected",
        repository_path=tmp_path,
        base_sha="b" * 40,
        runtime_profile=profile,
    )
    client = RejectedOncePaseoClient()
    adapter = PaseoRuntimeAdapter(client)
    pending = adapter.materialize(admission, prompt)

    adapter.accept_prompt(pending, prompt)
    accepted = adapter.read_binding(admission, prompt)

    assert accepted is not None
    assert accepted.prompt_accepted is True
    assert client.send_count == 2
    assert client.sent_action_keys == [
        f"{admission.admission_id}:prompt",
        f"{admission.admission_id}:prompt",
    ]
    assert client.prompt_acceptance_count(accepted.agent_id, prompt) == 1


def test_late_duplicate_boundary_overrides_published_digest(tmp_path):
    prompt = _prompt(4_096, name="duplicate")
    profile = _profile()
    admission = RuntimeAdmission(
        repository="local/duplicate",
        plan_digest="e" * 64,
        node_key="node:duplicate",
        admission_id="admission:duplicate",
        repository_path=tmp_path,
        base_sha="b" * 40,
        runtime_profile=profile,
    )
    client = InMemoryPaseoClient()
    adapter = PaseoRuntimeAdapter(client)
    accepted = adapter.materialize(admission, prompt)
    assert accepted.prompt_accepted is True
    client.send_prompt(
        accepted.agent_id,
        prompt,
        action_key="external:duplicate",
    )
    assert client.prompt_acceptance_count(accepted.agent_id, prompt) == 2

    with pytest.raises(RuntimeAdapterError) as duplicate:
        adapter.read_binding(admission, prompt)

    assert duplicate.value.code == "PROMPT_ACCEPTANCE_DUPLICATE"
    assert duplicate.value.failure_class == "ambiguous"


def test_worker_observation_recounts_late_duplicate_boundary(tmp_path):
    prompt = _prompt(4_096, name="observe-duplicate")
    profile = _profile()
    admission = RuntimeAdmission(
        repository="local/observe-duplicate",
        plan_digest="o" * 64,
        node_key="node:observe-duplicate",
        admission_id="admission:observe-duplicate",
        repository_path=tmp_path,
        base_sha="b" * 40,
        runtime_profile=profile,
    )
    client = InMemoryPaseoClient()
    adapter = PaseoRuntimeAdapter(client)
    accepted = adapter.materialize(admission, prompt)
    attached = adapter.attach_attempt(
        accepted,
        "attempt:observe-duplicate",
    )
    client.send_prompt(
        attached.agent_id,
        prompt,
        action_key="external:observe-duplicate",
    )

    with pytest.raises(RuntimeAdapterError) as duplicate:
        adapter.observe(attached)

    assert duplicate.value.code == "PROMPT_ACCEPTANCE_DUPLICATE"
    assert duplicate.value.failure_class == "ambiguous"


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


def test_review_lookup_create_race_does_not_authorize_first_send(
    tmp_path,
    monkeypatch,
):
    repository, candidate_sha = _repository(tmp_path)
    request = ReviewAxisRequest(
        repository="local/review-create-race",
        attempt_id="attempt:review-create-race",
        candidate_sha=candidate_sha,
        base_sha=candidate_sha,
        axis="standards",
        recovery_ordinal=0,
        workspace=repository,
        diff_command=("git", "diff", f"{candidate_sha}...{candidate_sha}"),
        commit_list=("transport candidate",),
        spec_source_ref="synthetic://issue/63",
        spec_text=json.dumps(
            {"payload": "r" * 20_000},
            separators=(",", ":"),
        ),
        standards_sources=("AGENTS.md", "CONTEXT.md"),
        check_manifest_digest="c" * 64,
    )
    prompt = request.to_prompt()
    assert len(prompt.text.encode("utf-8")) > PASEO_INLINE_PROMPT_MAX_BYTES
    client = CreateRacePaseoClient()
    adapter = PaseoRuntimeAdapter(client)

    monkeypatch.setattr("gwo_v8.runtime.PASEO_BOOTSTRAP_WAIT_SECONDS", 0.0)
    with pytest.raises(RuntimeAdapterError) as ambiguous:
        adapter.materialize_review_axis(
            request,
            _profile(),
            parent_agent_id="worker-parent",
        )

    assert ambiguous.value.code == "PROMPT_DELIVERY_AMBIGUOUS"
    agents = client.find_by_labels({"gwo.action_key": request.action_key})
    assert len(agents) == 1
    assert client.create_count == 1
    assert client.send_count == 0
    assert client.sent_action_keys == []
    assert "gwo.create_receipt" not in client._labels[agents[0].agent_id]


def test_review_axis_prompt_acceptance_adopts_after_restart_without_duplicate_delivery(
    tmp_path,
):
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

    duplicate_request = requests[0]
    duplicate_binding = adopted[duplicate_request.axis]
    client.send_prompt(
        duplicate_binding.agent_id,
        duplicate_request.to_prompt(),
        action_key=duplicate_request.action_key,
    )
    with pytest.raises(RuntimeAdapterError) as duplicate:
        restarted.observe_review_axis(
            duplicate_request,
            duplicate_binding,
        )
    assert duplicate.value.code == "PROMPT_ACCEPTANCE_DUPLICATE"


def test_review_axis_ambiguous_bootstrap_child_delivers_exact_prompt_once(
    tmp_path,
):
    repository, candidate_sha = _repository(tmp_path)
    profile = _profile()
    request = ReviewAxisRequest(
        repository="local/review-bootstrap-recovery",
        attempt_id="attempt:review-bootstrap-recovery",
        candidate_sha=candidate_sha,
        base_sha=candidate_sha,
        axis="standards",
        recovery_ordinal=0,
        workspace=repository,
        diff_command=("git", "diff", f"{candidate_sha}...{candidate_sha}"),
        commit_list=("transport candidate",),
        spec_source_ref="synthetic://issue/77",
        spec_text=json.dumps(
            {"payload": "b" * 20_000},
            separators=(",", ":"),
        ),
        standards_sources=("AGENTS.md", "CONTEXT.md"),
        check_manifest_digest="c" * 64,
    )
    prompt = request.to_prompt()
    bootstrap = _paseo_bootstrap_prompt(request.action_key)
    assert len(prompt.text.encode("utf-8")) > PASEO_INLINE_PROMPT_MAX_BYTES
    client = AmbiguousBootstrapOnlyReviewClient()

    binding = PaseoRuntimeAdapter(client).materialize_review_axis(
        request,
        profile,
        parent_agent_id="worker-parent",
    )

    assert client.create_count == 1
    assert client.send_count == 1
    assert client.prompt_acceptance_count(binding.agent_id, bootstrap) == 1
    assert client.prompt_acceptance_count(binding.agent_id, prompt) == 1

    adopted = PaseoRuntimeAdapter(client).materialize_review_axis(
        request,
        profile,
        parent_agent_id="worker-parent",
    )
    assert adopted.agent_id == binding.agent_id
    assert client.create_count == 1
    assert client.send_count == 1
    assert client.prompt_acceptance_count(binding.agent_id, bootstrap) == 1
    assert client.prompt_acceptance_count(binding.agent_id, prompt) == 1


def test_review_prompts_keep_spec_authority_without_repeating_file_contents():
    content = "frozen-content-" * 25_000
    compacted = ReviewConvergence._compact_review_contract(
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
