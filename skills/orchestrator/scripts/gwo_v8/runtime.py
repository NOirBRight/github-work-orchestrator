"""Paseo-shaped Runtime seam with an explicit in-memory Phase 1 test fake."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
from typing import Any, Protocol

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
    attempt_id: str
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
    attempt_id: str
    repository_path: str
    workspace: str


@dataclass(frozen=True)
class RuntimeExecution:
    result_claim: ResultClaim
    evidence: tuple[TypedEvidence, ...]


class RuntimeAdapter(Protocol):
    def materialize(self, admission: RuntimeAdmission) -> RuntimeBinding: ...

    def execute(
        self, binding: RuntimeBinding, node: dict[str, Any]
    ) -> RuntimeExecution: ...

    def retire(self, binding: RuntimeBinding) -> None: ...


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
        self._bindings: dict[str, RuntimeBinding] = {}

    def materialize(self, admission: RuntimeAdmission) -> RuntimeBinding:
        existing = self._bindings.get(admission.admission_id)
        if existing is not None:
            return existing
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
            attempt_id=admission.attempt_id,
            repository_path=str(repository_path),
            workspace=str(workspace.resolve()),
        )
        self._bindings[admission.admission_id] = binding
        return binding

    def read_binding(self, admission_id: str) -> RuntimeBinding | None:
        return self._bindings.get(admission_id)

    def execute(
        self, binding: RuntimeBinding, node: dict[str, Any]
    ) -> RuntimeExecution:
        if self.read_binding(binding.admission_id) != binding:
            raise RuntimeAdapterError(
                "RUNTIME_BINDING_UNKNOWN", "binding identity did not round-trip"
            )
        if (
            binding.repository != node.get("_repository", binding.repository)
            or binding.plan_digest != node.get("_plan_digest", binding.plan_digest)
            or binding.node_key != node.get("node_key")
        ):
            raise RuntimeAdapterError(
                "RUNTIME_IDENTITY_MISMATCH", "node and Runtime Binding do not agree"
            )

        workspace = Path(binding.workspace).resolve()
        changes = (node.get("inputs") or {}).get("file_changes")
        if not isinstance(changes, list) or not changes:
            raise RuntimeAdapterError(
                "RUNTIME_INPUT_INVALID", "work node has no file changes"
            )
        for change in changes:
            if not isinstance(change, dict):
                raise RuntimeAdapterError(
                    "RUNTIME_INPUT_INVALID", "file change must be an object"
                )
            target = (workspace / str(change.get("path") or "")).resolve()
            try:
                target.relative_to(workspace)
            except ValueError as error:
                raise RuntimeAdapterError(
                    "EFFECT_CONTRACT_VIOLATION", "file change escapes the workspace"
                ) from error
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(str(change.get("content") or ""), encoding="utf-8")

        _git(workspace, "add", "--all")
        _git(workspace, "commit", "-m", f"candidate for {binding.node_key}")
        candidate_sha = _git(workspace, "rev-parse", "HEAD")
        tree_sha = _git(workspace, "rev-parse", "HEAD^{tree}")
        evidence: list[TypedEvidence] = [
            TypedEvidence.observe(
                kind="candidate",
                subject=candidate_sha,
                observer_type="runtime_adapter",
                observer_id=binding.runtime_id,
                observed_at=_now(),
                source_ref=f"runtime-memory://{binding.runtime_id}/candidate",
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
            environment = {
                "python": sys.version,
                "platform": sys.platform,
            }
            evidence.append(
                TypedEvidence.observe(
                    kind="check",
                    subject=candidate_sha,
                    observer_type="runtime_adapter",
                    observer_id=binding.runtime_id,
                    observed_at=_now(),
                    source_ref=(
                        f"runtime-memory://{binding.runtime_id}/check/"
                        f"{check.get('check_id')}"
                    ),
                    payload={
                        "check_id": check.get("check_id"),
                        "command": command,
                        "outcome": "passed" if result.returncode == 0 else "failed",
                        "exit_code": result.returncode,
                        "environment_digest": hashlib.sha256(
                            json.dumps(
                                environment,
                                sort_keys=True,
                                separators=(",", ":"),
                            ).encode("utf-8")
                        ).hexdigest(),
                        "log_digest": hashlib.sha256(log).hexdigest(),
                    },
                )
            )
        claim = ResultClaim(
            attempt_id=binding.attempt_id,
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
        return RuntimeExecution(result_claim=claim, evidence=tuple(evidence))

    def retire(self, binding: RuntimeBinding) -> None:
        current = self._bindings.get(binding.admission_id)
        if current is None:
            return
        if current != binding:
            raise RuntimeAdapterError(
                "RUNTIME_IDENTITY_MISMATCH", "refusing to retire another binding"
            )
        repository = Path(binding.repository_path)
        workspace = Path(binding.workspace)
        if workspace.exists():
            _git(repository, "worktree", "remove", "--force", str(workspace))
        self._bindings.pop(binding.admission_id, None)
