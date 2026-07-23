"""Capability-oriented Runtime seam and explicit in-memory Phase 1 fake."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
from typing import Any, Protocol

from ._canonical import canonical_bytes, digest_bytes
from ._effects import (
    EffectContractError,
    authorized_file_changes,
    normalized_relative_path,
)
from .evidence import ResultClaim, TypedEvidence


class RuntimeAdapterError(RuntimeError):
    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class RuntimeAdmission:
    repository: str
    plan_digest: str
    node_key: str
    admission_id: str
    repository_path: Path
    base_sha: str


@dataclass(frozen=True)
class RuntimeBinding:
    adapter: str
    runtime_id: str
    repository: str
    plan_digest: str
    node_key: str
    admission_id: str
    repository_path: str
    workspace: str
    prompt_accepted: bool = False
    prompt_digest: str | None = None
    attempt_id: str | None = None


@dataclass(frozen=True)
class RuntimePrompt:
    text: str
    digest: str

    @classmethod
    def from_node(cls, node: dict[str, Any]) -> RuntimePrompt:
        text = canonical_bytes({"node": node}).decode("utf-8")
        return cls(text=text, digest=digest_bytes(text.encode("utf-8")))


@dataclass(frozen=True)
class RuntimeObservation:
    binding: RuntimeBinding
    lifecycle: str
    result_claim: ResultClaim | None
    evidence: tuple[TypedEvidence, ...]


class RuntimeAdapter(Protocol):
    """Evolvable Paseo-shaped capabilities consumed by the Phase 1 Kernel."""

    adapter_name: str

    def materialize(self, admission: RuntimeAdmission) -> RuntimeBinding: ...

    def read_binding(self, admission_id: str) -> RuntimeBinding | None: ...

    def accept_prompt(
        self, binding: RuntimeBinding, prompt: RuntimePrompt
    ) -> None: ...

    def attach_attempt(self, binding: RuntimeBinding, attempt_id: str) -> None: ...

    def resume(self, binding: RuntimeBinding) -> None: ...

    def observe(self, binding: RuntimeBinding) -> RuntimeObservation: ...

    def retire(self, binding: RuntimeBinding) -> None: ...


@dataclass
class _RuntimeState:
    binding: RuntimeBinding
    prompt: RuntimePrompt | None = None
    node: dict[str, Any] | None = None
    result_claim: ResultClaim | None = None
    evidence: tuple[TypedEvidence, ...] = ()


def _run(command: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


def _git(repository: Path, *args: str) -> str:
    result = _run(["git", *args], cwd=repository)
    if result.returncode != 0:
        raise RuntimeAdapterError(
            "GIT_OPERATION_FAILED",
            result.stderr.strip() or result.stdout.strip() or "git failed",
        )
    return result.stdout.strip()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class InMemoryRuntimeAdapter:
    """Deterministic contract fake; it is not a universal production Adapter."""

    adapter_name = "in-memory"

    def __init__(self, workspace_root: Path):
        self.workspace_root = Path(workspace_root)
        self.workspace_root.mkdir(parents=True, exist_ok=True)
        self._states: dict[str, _RuntimeState] = {}

    def materialize(self, admission: RuntimeAdmission) -> RuntimeBinding:
        existing = self._states.get(admission.admission_id)
        if existing is not None:
            return existing.binding
        slug = re.sub(r"[^A-Za-z0-9_.-]+", "-", admission.admission_id).strip("-")
        workspace = self.workspace_root / (slug or "admission")
        if workspace.exists():
            raise RuntimeAdapterError(
                "WORKSPACE_CONFLICT", "workspace exists without a matching binding"
            )
        repository_path = Path(admission.repository_path).resolve()
        result = _run(
            [
                "git",
                "worktree",
                "add",
                "--detach",
                str(workspace),
                admission.base_sha,
            ],
            cwd=repository_path,
        )
        if result.returncode != 0:
            raise RuntimeAdapterError(
                "MATERIALIZATION_FAILED",
                result.stderr.strip() or result.stdout.strip(),
            )
        runtime_id = (
            "runtime:"
            + hashlib.sha256(admission.admission_id.encode("utf-8")).hexdigest()[:20]
        )
        binding = RuntimeBinding(
            adapter=self.adapter_name,
            runtime_id=runtime_id,
            repository=admission.repository,
            plan_digest=admission.plan_digest,
            node_key=admission.node_key,
            admission_id=admission.admission_id,
            repository_path=str(repository_path),
            workspace=str(workspace.resolve()),
        )
        self._states[admission.admission_id] = _RuntimeState(binding=binding)
        return binding

    def read_binding(self, admission_id: str) -> RuntimeBinding | None:
        state = self._states.get(admission_id)
        return None if state is None else state.binding

    def _state_for(self, binding: RuntimeBinding) -> _RuntimeState:
        state = self._states.get(binding.admission_id)
        if state is None or state.binding != binding:
            raise RuntimeAdapterError(
                "RUNTIME_BINDING_UNKNOWN", "binding identity did not round-trip"
            )
        return state

    def accept_prompt(self, binding: RuntimeBinding, prompt: RuntimePrompt) -> None:
        state = self._state_for(binding)
        if digest_bytes(prompt.text.encode("utf-8")) != prompt.digest:
            raise RuntimeAdapterError(
                "PROMPT_DIGEST_MISMATCH", "Prompt bytes do not match their digest"
            )
        try:
            payload = json.loads(prompt.text)
        except json.JSONDecodeError as error:
            raise RuntimeAdapterError("PROMPT_INVALID", "Prompt is not readable") from error
        node = payload.get("node") if isinstance(payload, dict) else None
        if not isinstance(node, dict) or node.get("node_key") != binding.node_key:
            raise RuntimeAdapterError(
                "PROMPT_IDENTITY_MISMATCH", "Prompt does not describe this Plan Node"
            )
        state.prompt = prompt
        state.node = node
        state.binding = replace(
            binding,
            prompt_accepted=True,
            prompt_digest=prompt.digest,
        )

    def attach_attempt(self, binding: RuntimeBinding, attempt_id: str) -> None:
        state = self._state_for(binding)
        if not binding.prompt_accepted:
            raise RuntimeAdapterError(
                "PROMPT_NOT_ACCEPTED", "Attempt cannot bind before Prompt readback"
            )
        if not isinstance(attempt_id, str) or not attempt_id:
            raise RuntimeAdapterError("ATTEMPT_ID_INVALID", "Attempt ID is required")
        state.binding = replace(binding, attempt_id=attempt_id)

    def resume(self, binding: RuntimeBinding) -> None:
        state = self._state_for(binding)
        if binding.attempt_id is None or state.node is None:
            raise RuntimeAdapterError(
                "ATTEMPT_NOT_READY", "Runtime has no accepted Prompt-bound Attempt"
            )
        if state.result_claim is not None:
            return
        state.result_claim, state.evidence = self._execute(binding, state.node)

    def observe(self, binding: RuntimeBinding) -> RuntimeObservation:
        state = self._state_for(binding)
        return RuntimeObservation(
            binding=state.binding,
            lifecycle="completed" if state.result_claim is not None else "idle",
            result_claim=state.result_claim,
            evidence=state.evidence,
        )

    def _execute(
        self, binding: RuntimeBinding, node: dict[str, Any]
    ) -> tuple[ResultClaim, tuple[TypedEvidence, ...]]:
        if (
            binding.repository != node.get("_repository", binding.repository)
            or binding.plan_digest != node.get("_plan_digest", binding.plan_digest)
            or binding.node_key != node.get("node_key")
        ):
            raise RuntimeAdapterError(
                "RUNTIME_IDENTITY_MISMATCH", "node and Runtime Binding do not agree"
            )
        workspace = Path(binding.workspace).resolve()
        try:
            changes = authorized_file_changes(node)
        except EffectContractError as error:
            raise RuntimeAdapterError(
                "EFFECT_CONTRACT_VIOLATION", str(error)
            ) from error
        for change in changes:
            relative = normalized_relative_path(change.get("path"))
            target = (workspace / relative).resolve()
            try:
                target.relative_to(workspace)
            except ValueError as error:
                raise RuntimeAdapterError(
                    "EFFECT_CONTRACT_VIOLATION", "write path escapes the workspace"
                ) from error
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(str(change.get("content") or ""), encoding="utf-8")

        _git(workspace, "add", "--all")
        _git(workspace, "commit", "-m", f"candidate for {binding.node_key}")
        candidate_sha = _git(workspace, "rev-parse", "HEAD")
        tree_sha = _git(workspace, "rev-parse", "HEAD^{tree}")
        evidence: list[TypedEvidence] = [
            TypedEvidence._capture(
                kind="candidate",
                subject=candidate_sha,
                observer_type="runtime_adapter",
                observer_id=binding.runtime_id,
                observed_at=_now(),
                source_ref=f"runtime-in-memory://{binding.runtime_id}/candidate",
                payload={"tree_sha": tree_sha},
            )
        ]
        checks = (node.get("output_contract") or {}).get("checks")
        if not isinstance(checks, list):
            raise RuntimeAdapterError(
                "RUNTIME_CHECK_INVALID", "output contract checks must be a list"
            )
        for check in checks:
            if not isinstance(check, dict) or not isinstance(
                check.get("command"), list
            ):
                raise RuntimeAdapterError(
                    "RUNTIME_CHECK_INVALID", "check command must be an argument list"
                )
            command = [str(part) for part in check["command"]]
            result = _run(command, cwd=workspace)
            log = f"{result.stdout}\n{result.stderr}".encode("utf-8")
            environment = {"python": sys.version, "platform": sys.platform}
            evidence.append(
                TypedEvidence._capture(
                    kind="check",
                    subject=candidate_sha,
                    observer_type="runtime_adapter",
                    observer_id=binding.runtime_id,
                    observed_at=_now(),
                    source_ref=(
                        f"runtime-in-memory://{binding.runtime_id}/check/"
                        f"{check.get('check_id')}"
                    ),
                    payload={
                        "check_id": check.get("check_id"),
                        "command": command,
                        "outcome": "passed" if result.returncode == 0 else "failed",
                        "exit_code": result.returncode,
                        "environment_digest": digest_bytes(
                            canonical_bytes(environment)
                        ),
                        "log_digest": hashlib.sha256(log).hexdigest(),
                    },
                )
            )
        claim = ResultClaim(
            attempt_id=str(binding.attempt_id),
            node_key=binding.node_key,
            candidate_sha=candidate_sha,
            assertions={
                "done": True,
                "checks": [
                    check.get("check_id")
                    for check in checks
                    if isinstance(check, dict)
                ],
            },
        )
        return claim, tuple(evidence)

    def retire(self, binding: RuntimeBinding) -> None:
        state = self._states.get(binding.admission_id)
        if state is None:
            return
        if state.binding != binding:
            raise RuntimeAdapterError(
                "RUNTIME_IDENTITY_MISMATCH", "refusing to retire another binding"
            )
        repository = Path(binding.repository_path)
        workspace = Path(binding.workspace)
        if workspace.exists():
            _git(repository, "worktree", "remove", "--force", str(workspace))
        self._states.pop(binding.admission_id, None)
