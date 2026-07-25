"""Capability-oriented Runtime seam and explicit deterministic test fake."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
import errno
from fnmatch import fnmatchcase
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
from typing import Any, Protocol

from ._canonical import canonical_bytes, digest_bytes, digest_value
from ._effects import (
    EffectContractError,
    authorized_file_changes,
    normalized_relative_path,
)
from .evidence import ResultClaim, TypedEvidence


PASEO_INLINE_PROMPT_MAX_BYTES = 8_192
PASEO_BOOTSTRAP_WAIT_SECONDS = 30.0
PASEO_BOOTSTRAP_POLL_SECONDS = 0.25
PASEO_PROMPT_SETTLE_SECONDS = 1.0
PASEO_PROMPT_DELIVERY_ATTEMPTS = 3
PASEO_PROMPT_DELIVERY_PHASES = (
    "prepared",
    "acked",
    "rejected",
    # Read legacy phases conservatively. Neither phase proves that an
    # acknowledged asynchronous send was absent from Paseo's queue.
    "idle",
    "dropped",
)


class RuntimeAdapterError(RuntimeError):
    def __init__(
        self,
        code: str,
        detail: str,
        *,
        failure_class: str = "permanent",
    ):
        super().__init__(detail)
        self.code = code
        self.detail = detail
        self.failure_class = failure_class


@dataclass(frozen=True)
class RuntimeProfile:
    name: str
    provider: str
    model: str
    thinking: str
    mode: str
    features: dict[str, Any]

    @property
    def digest(self) -> str:
        return digest_value(
            {
                "name": self.name,
                "provider": self.provider,
                "model": self.model,
                "thinking": self.thinking,
                "mode": self.mode,
                "features": self.features,
            }
        )


@dataclass(frozen=True)
class ActiveTurnPools:
    workers: int
    coordinators: int


def resolve_active_turn_pools(
    config: dict[str, Any] | None,
    *,
    repository: str,
) -> ActiveTurnPools:
    value = {} if config is None else config
    if not isinstance(value, dict):
        raise RuntimeAdapterError(
            "ACTIVE_TURN_CONFIG_INVALID",
            "Runtime configuration must be an object",
        )
    global_pools = value.get("active_turn_pools")
    if global_pools is None:
        global_pools = {"workers": 8, "coordinators": 1}
    repositories = value.get("repositories")
    if repositories is None:
        repositories = {}
    if not isinstance(global_pools, dict) or not isinstance(repositories, dict):
        raise RuntimeAdapterError(
            "ACTIVE_TURN_CONFIG_INVALID",
            "Active Turn pools and repository overrides must be objects",
        )
    repository_config = repositories.get(repository)
    if repository_config is None:
        repository_config = {}
    if not isinstance(repository_config, dict):
        raise RuntimeAdapterError(
            "ACTIVE_TURN_CONFIG_INVALID",
            "repository Runtime configuration must be an object",
        )
    repository_pools = repository_config.get("active_turn_pools")
    if repository_pools is None:
        repository_pools = {}
    if not isinstance(repository_pools, dict):
        raise RuntimeAdapterError(
            "ACTIVE_TURN_CONFIG_INVALID",
            "repository Active Turn pools must be an object",
        )
    workers = repository_pools.get("workers", global_pools.get("workers"))
    coordinators = repository_pools.get(
        "coordinators",
        global_pools.get("coordinators"),
    )
    if (
        not isinstance(workers, int)
        or isinstance(workers, bool)
        or workers < 1
        or not isinstance(coordinators, int)
        or isinstance(coordinators, bool)
        or coordinators < 1
    ):
        raise RuntimeAdapterError(
            "ACTIVE_TURN_CONFIG_INVALID",
            "Worker and Coordinator Active Turn capacities must be positive",
        )
    return ActiveTurnPools(
        workers=workers,
        coordinators=coordinators,
    )


REVIEW_PROFILE_SELECTORS = {
    "standard_axis",
    "recovery_axis",
    "strict_specialist",
}
REVIEW_AXES = {"standards", "spec", "specialist"}


def _valid_review_axis(value: str) -> bool:
    return value in {"standards", "spec"} or (
        re.fullmatch(r"specialist:[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", value) is not None
    )


def resolve_review_profile(
    config: dict[str, Any],
    *,
    repository: str,
    selector: str,
) -> RuntimeProfile:
    """Resolve one host-local Review Profile without creating a Role Binding."""

    if selector not in REVIEW_PROFILE_SELECTORS:
        raise RuntimeAdapterError(
            "REVIEW_PROFILE_SELECTOR_INVALID",
            f"unknown Review Profile selector: {selector}",
        )
    if not isinstance(config, dict):
        raise RuntimeAdapterError(
            "REVIEW_PROFILE_CONFIG_INVALID",
            "Runtime configuration must be an object",
        )
    repositories = config.get("repositories") or {}
    if not isinstance(repositories, dict):
        raise RuntimeAdapterError(
            "REVIEW_PROFILE_CONFIG_INVALID",
            "repository Runtime configuration must be an object",
        )
    repository_config = repositories.get(repository) or {}
    if not isinstance(repository_config, dict):
        raise RuntimeAdapterError(
            "REVIEW_PROFILE_CONFIG_INVALID",
            f"repository Runtime configuration is invalid: {repository}",
        )
    repository_selectors = repository_config.get("review_profiles") or {}
    global_selectors = config.get("review_profiles") or {}
    if not isinstance(repository_selectors, dict) or not isinstance(
        global_selectors,
        dict,
    ):
        raise RuntimeAdapterError(
            "REVIEW_PROFILE_CONFIG_INVALID",
            "Review Profile selectors must be objects",
        )
    profile_id = repository_selectors.get(
        selector,
        global_selectors.get(selector),
    )
    if not isinstance(profile_id, str) or not profile_id:
        raise RuntimeAdapterError(
            "REVIEW_PROFILE_MISSING",
            f"Review Profile selector has no Runtime Profile: {selector}",
        )
    repository_profiles = repository_config.get("runtime_profiles")
    global_profiles = config.get("runtime_profiles")
    if repository_profiles is None:
        repository_profiles = repository_config.get("role_profiles") or {}
    if global_profiles is None:
        global_profiles = config.get("role_profiles") or {}
    if not isinstance(repository_profiles, dict) or not isinstance(
        global_profiles,
        dict,
    ):
        raise RuntimeAdapterError(
            "REVIEW_PROFILE_CONFIG_INVALID",
            "Runtime Profiles must be objects",
        )
    mapping = repository_profiles.get(
        profile_id,
        global_profiles.get(profile_id),
    )
    if not isinstance(mapping, dict):
        raise RuntimeAdapterError(
            "REVIEW_PROFILE_MISSING",
            f"Runtime Profile is missing: {profile_id}",
        )
    provider = mapping.get("provider")
    settings = mapping.get("settings")
    if (
        not isinstance(provider, str)
        or not provider
        or not isinstance(
            settings,
            dict,
        )
    ):
        raise RuntimeAdapterError(
            "REVIEW_PROFILE_INVALID",
            f"Runtime Profile is invalid: {profile_id}",
        )
    model = settings.get("model")
    thinking = settings.get("thinkingOptionId")
    mode = settings.get("modeId")
    features = settings.get("features", {})
    if (
        not isinstance(model, str)
        or not model
        or not isinstance(thinking, str)
        or not thinking
        or not isinstance(mode, str)
        or not mode
        or not isinstance(features, dict)
    ):
        raise RuntimeAdapterError(
            "REVIEW_PROFILE_INVALID",
            f"Runtime Profile settings are incomplete: {profile_id}",
        )
    return RuntimeProfile(
        name=profile_id,
        provider=provider,
        model=model,
        thinking=thinking,
        mode=mode,
        features=dict(features),
    )


class SkillCatalog(Protocol):
    def resolve(self, name: str) -> str | None: ...


class InMemorySkillCatalog:
    """Explicit mutable fake of the installed Skill catalog."""

    def __init__(self, guidance: dict[str, str]):
        self._guidance = dict(guidance)

    def resolve(self, name: str) -> str | None:
        return self._guidance.get(name)

    def set(self, name: str, guidance: str) -> None:
        self._guidance[name] = guidance


class InstalledSkillCatalog:
    """Read current Skill guidance from ordered installed Skill roots."""

    def __init__(self, roots: tuple[Path, ...]):
        self.roots = tuple(Path(root).resolve() for root in roots)

    def resolve(self, name: str) -> str | None:
        if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", name) is None:
            return None
        for root in self.roots:
            candidate = (root / name / "SKILL.md").resolve()
            try:
                candidate.relative_to(root)
            except ValueError:
                continue
            if candidate.is_file():
                return candidate.read_text(encoding="utf-8")
        return None


@dataclass(frozen=True)
class RuntimeAdmission:
    repository: str
    plan_digest: str
    node_key: str
    admission_id: str
    repository_path: Path
    base_sha: str
    runtime_profile: RuntimeProfile | None = None
    parent_agent_id: str | None = None


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
    agent_id: str | None = None
    session_id: str | None = None
    workspace_id: str | None = None
    parent_agent_id: str | None = None
    runtime_profile: str | None = None
    profile_digest: str | None = None
    provider: str | None = None
    model: str | None = None
    thinking: str | None = None
    mode: str | None = None
    features_digest: str | None = None
    base_sha: str | None = None


@dataclass(frozen=True)
class RuntimePrompt:
    text: str
    digest: str
    authority_digest: str | None = None
    skill_name: str | None = None
    skill_digest: str | None = None
    warnings: tuple[str, ...] = ()
    contract_node: dict[str, Any] | None = None

    @classmethod
    def from_node(
        cls,
        node: dict[str, Any],
        *,
        skill_catalog: SkillCatalog | None = None,
    ) -> RuntimePrompt:
        skill_name = node.get("skill_reference")
        guidance = None
        warnings: tuple[str, ...] = ()
        if isinstance(skill_name, str) and skill_name:
            guidance = (
                None if skill_catalog is None else skill_catalog.resolve(skill_name)
            )
            if guidance is None:
                warnings = ("SKILL_GUIDANCE_MISSING",)
        else:
            skill_name = None
        skill_digest = (
            None if guidance is None else digest_bytes(guidance.encode("utf-8"))
        )
        contract_node = json.loads(canonical_bytes(node))
        worker_node = json.loads(canonical_bytes(node))
        output_contract = worker_node.get("output_contract")
        if isinstance(output_contract, dict):
            worker_node["output_contract"] = {
                "checks": [
                    check
                    for check in output_contract.get("checks") or ()
                    if isinstance(check, dict)
                    and check.get("hosted_only") is not True
                    and check.get("suite") in {"affected", "local"}
                ],
                "note": (
                    "Worker-visible affected diagnostics only; Kernel retains "
                    "the full Evidence and Review contract."
                ),
            }
        text = canonical_bytes(
            {
                "execution_scope": {
                    "role": "worker",
                    "responsibilities": [
                        "implement the frozen Plan Node",
                        (
                            "use only narrow affected diagnostics needed while "
                            "implementing"
                        ),
                        (
                            "before GWO_RESULT, make candidate_sha the Workspace "
                            "HEAD and leave git status --porcelain empty"
                        ),
                    ],
                    "prohibited": [
                        "do not invoke review skills",
                        "do not create reviewer subagents",
                        (
                            "do not rerun repository-wide acceptance suites; "
                            "the Runtime Adapter captures contract Evidence"
                        ),
                        "do not run hosted-only checks",
                    ],
                    "review_owner": (
                        "Kernel materializes independent Review after Candidate "
                        "readback; review requirements are not Worker work."
                    ),
                },
                "node": worker_node,
                "result_protocol": {
                    "action_key": node.get("node_key"),
                    "instruction": (
                        "End with exactly one compact JSON line and no code "
                        "fence. Success schema: GWO_RESULT "
                        '{"schema_version":1,"action_key":"'
                        f'{node.get("node_key")}","candidate_sha":"<40-hex-sha>"'
                        "}. No-result schema: GWO_RESULT "
                        '{"schema_version":1,"action_key":"'
                        f'{node.get("node_key")}","terminal_reason":"no_result",'
                        '"reason":"<non-empty bounded reason>"}.'
                    ),
                    "marker": "GWO_RESULT",
                    "schema_version": 1,
                },
                "skill_guidance": guidance,
                "skill_name": skill_name,
            }
        ).decode("utf-8")
        return cls(
            text=text,
            digest=digest_bytes(text.encode("utf-8")),
            authority_digest=(
                str(node["contract_digest"])
                if isinstance(node.get("contract_digest"), str)
                else None
            ),
            skill_name=skill_name,
            skill_digest=skill_digest,
            warnings=warnings,
            contract_node=contract_node,
        )


def _contract_node_from_prompt(
    prompt: RuntimePrompt | None,
) -> dict[str, Any] | None:
    if prompt is None:
        return None
    if isinstance(prompt.contract_node, dict):
        return prompt.contract_node
    payload = json.loads(prompt.text)
    node = payload.get("node") if isinstance(payload, dict) else None
    return node if isinstance(node, dict) else None


@dataclass(frozen=True)
class ReviewAxisRequest:
    repository: str
    attempt_id: str
    candidate_sha: str
    base_sha: str
    axis: str
    recovery_ordinal: int
    workspace: Path
    diff_command: tuple[str, ...]
    commit_list: tuple[str, ...]
    spec_source_ref: str
    spec_text: str
    standards_sources: tuple[str, ...]
    check_manifest_digest: str
    prior_findings: tuple[dict[str, Any], ...] = ()
    candidate_delta: str | None = None

    def __post_init__(self) -> None:
        if (
            not self.repository
            or not self.attempt_id
            or not _valid_review_axis(self.axis)
            or re.fullmatch(r"[0-9a-f]{40}", self.candidate_sha) is None
            or re.fullmatch(r"[0-9a-f]{40}", self.base_sha) is None
            or not isinstance(self.recovery_ordinal, int)
            or isinstance(self.recovery_ordinal, bool)
            or self.recovery_ordinal < 0
            or not self.diff_command
            or any(not isinstance(part, str) or not part for part in self.diff_command)
            or not self.spec_source_ref
            or not self.spec_text
            or any(
                not isinstance(source, str) or not source
                for source in self.standards_sources
            )
            or re.fullmatch(r"[0-9a-f]{64}", self.check_manifest_digest) is None
        ):
            raise RuntimeAdapterError(
                "REVIEW_AXIS_REQUEST_INVALID",
                "Review axis request is incomplete or invalid",
            )

    @property
    def action_key(self) -> str:
        identity = {
            "attempt_id": self.attempt_id,
            "candidate_sha": self.candidate_sha,
            "axis": self.axis,
            "recovery_ordinal": self.recovery_ordinal,
        }
        return f"review:{digest_value(identity)[:32]}"

    @property
    def fixed_input(self) -> dict[str, Any]:
        return {
            "repository": self.repository,
            "attempt_id": self.attempt_id,
            "candidate_sha": self.candidate_sha,
            "base_sha": self.base_sha,
            "axis": self.axis,
            "recovery_ordinal": self.recovery_ordinal,
            "diff_command": list(self.diff_command),
            "commit_list": list(self.commit_list),
            "spec": {
                "source_ref": self.spec_source_ref,
                "text": self.spec_text,
            },
            "standards_sources": list(self.standards_sources),
            "check_manifest_digest": self.check_manifest_digest,
            "prior_findings": list(self.prior_findings),
            "candidate_delta": self.candidate_delta,
        }

    @property
    def fixed_input_digest(self) -> str:
        return digest_value(self.fixed_input)

    @property
    def spec_digest(self) -> str:
        return digest_value(
            {
                "source_ref": self.spec_source_ref,
                "text": self.spec_text,
            }
        )

    def to_prompt(self) -> RuntimePrompt:
        payload = {
            "skill_guidance": {
                "name": "code-review",
                "axis": self.axis,
                "instruction": (
                    "Apply the fixed-point Standards or Spec review axis "
                    "without merging or reranking the other axis."
                ),
            },
            "review_axis": {
                **self.fixed_input,
                "action_key": self.action_key,
                "rules": {
                    "authority": "read-only",
                    "history": "none",
                    "may_delegate": False,
                    "may_mutate": False,
                },
            },
            "output_protocol": {
                "marker": "GWO_REVIEW_AXIS",
                "schema_version": 1,
                "instruction": (
                    "End with exactly one compact JSON line and no code fence: "
                    "GWO_REVIEW_AXIS "
                    '{"schema_version":1,"action_key":"'
                    f'{self.action_key}","candidate_sha":"{self.candidate_sha}",'
                    f'"axis":"{self.axis}","fixed_input_digest":"'
                    f'{self.fixed_input_digest}","findings":[]}}. '
                    "Replace findings with schema-valid finding objects only "
                    "when evidence supports them."
                ),
                "required_fields": [
                    "schema_version",
                    "action_key",
                    "candidate_sha",
                    "axis",
                    "fixed_input_digest",
                    "findings",
                ],
                "finding_fields": [
                    "severity",
                    "code",
                    "source",
                    "location",
                    "message",
                ],
                "severity": ["hard", "advisory"],
            },
        }
        text = canonical_bytes(payload).decode("utf-8")
        return RuntimePrompt(
            text=text,
            digest=digest_bytes(text.encode("utf-8")),
            authority_digest=self.fixed_input_digest,
        )


@dataclass(frozen=True)
class ReviewAxisBinding:
    action_key: str
    axis: str
    candidate_sha: str
    fixed_input_digest: str
    runtime_id: str
    agent_id: str
    session_id: str
    workspace_id: str
    workspace: str
    parent_agent_id: str | None
    runtime_profile: str
    profile_digest: str
    provider: str
    model: str
    thinking: str
    mode: str
    prompt_digest: str


@dataclass(frozen=True)
class ReviewAxisObservation:
    lifecycle: str
    axis: str
    attempt_id: str
    candidate_sha: str
    base_sha: str
    recovery_ordinal: int
    spec_digest: str
    check_manifest_digest: str
    fixed_input_digest: str
    action_key: str
    runtime_id: str
    agent_id: str
    session_id: str
    profile_digest: str
    provider: str
    model: str
    thinking: str
    mode: str
    output_digest: str | None
    findings: tuple[dict[str, str], ...] = ()


@dataclass(frozen=True)
class RuntimeObservation:
    binding: RuntimeBinding
    lifecycle: str
    result_claim: ResultClaim | None
    evidence: tuple[TypedEvidence, ...]
    terminal_reason: str | None = None
    terminal_detail: str | None = None


class RuntimeAdapter(Protocol):
    """Evolvable Paseo-shaped capabilities consumed by the V8 Kernel."""

    adapter_name: str

    def materialize(
        self,
        admission: RuntimeAdmission,
        prompt: RuntimePrompt | None = None,
    ) -> RuntimeBinding: ...

    def read_binding(
        self,
        admission: RuntimeAdmission | str,
        prompt: RuntimePrompt | None = None,
    ) -> RuntimeBinding | None: ...

    def accept_prompt(self, binding: RuntimeBinding, prompt: RuntimePrompt) -> None: ...

    def attach_attempt(
        self,
        binding: RuntimeBinding,
        attempt_id: str,
    ) -> RuntimeBinding: ...

    def resume(self, binding: RuntimeBinding) -> None: ...

    def observe(self, binding: RuntimeBinding) -> RuntimeObservation: ...

    def defer_repository_checks(self, binding: RuntimeBinding) -> None: ...

    def capture_deferred_checks(
        self,
        binding: RuntimeBinding,
        observation: RuntimeObservation,
    ) -> RuntimeObservation: ...

    def repair(self, binding: RuntimeBinding, prompt: RuntimePrompt) -> None: ...

    def interrupt(self, binding: RuntimeBinding) -> None: ...

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


def _environment_snapshot(
    requirements: tuple[str, ...],
    *,
    cwd: Path,
) -> dict[str, Any]:
    snapshot: dict[str, Any] = {"platform": sys.platform}
    executable_aliases = {
        "python": sys.executable,
        "rust": "rustc",
        "nodejs": "node",
    }
    for requirement in requirements:
        tool = re.split(r"[\s<>=!~]", requirement, maxsplit=1)[0].lower()
        executable = executable_aliases.get(tool, tool)
        try:
            result = _run([executable, "--version"], cwd=cwd)
        except OSError as error:
            raise RuntimeAdapterError(
                "CHECK_ENVIRONMENT_UNAVAILABLE",
                f"required check toolchain is unavailable: {requirement}",
            ) from error
        version = (result.stdout or result.stderr).strip()
        if result.returncode != 0 or not version:
            raise RuntimeAdapterError(
                "CHECK_ENVIRONMENT_UNAVAILABLE",
                f"required check toolchain cannot be identified: {requirement}",
            )
        snapshot[requirement] = {
            "executable": executable,
            "version": version,
        }
    return snapshot


def _git(repository: Path, *args: str) -> str:
    result = _run(["git", *args], cwd=repository)
    if result.returncode != 0:
        raise RuntimeAdapterError(
            "GIT_OPERATION_FAILED",
            result.stderr.strip() or result.stdout.strip() or "git failed",
        )
    return result.stdout.strip()


def _input_projection_digest(
    repository: Path,
    candidate_sha: str,
    selectors: tuple[str, ...],
) -> str:
    entries: list[dict[str, str]] = []
    listing = _git(
        repository,
        "ls-tree",
        "-r",
        "--full-tree",
        candidate_sha,
    )
    for line in listing.splitlines():
        metadata, separator, path = line.partition("\t")
        parts = metadata.split()
        if not separator or len(parts) != 3:
            continue
        mode, kind, object_id = parts
        if any(fnmatchcase(path, selector) for selector in selectors):
            entries.append(
                {
                    "path": path,
                    "mode": mode,
                    "kind": kind,
                    "object_id": object_id,
                }
            )
    return digest_value(
        {
            "candidate_sha": candidate_sha,
            "selectors": list(selectors),
            "entries": entries,
        }
    )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_bounded_outcome(
    output: str | None,
    *,
    marker: str,
    action_key: str,
) -> dict[str, Any] | None:
    """Return the latest small, schema-bound outcome for one exact action."""

    if not output:
        return None
    prefix = f"{marker} "
    for line in reversed(output[-262_144:].splitlines()):
        if not line.startswith(prefix):
            continue
        encoded = line[len(prefix) :]
        if len(encoded.encode("utf-8")) > 16_384:
            continue
        try:
            value = json.loads(encoded)
        except json.JSONDecodeError:
            continue
        if (
            isinstance(value, dict)
            and value.get("schema_version") == 1
            and value.get("action_key") == action_key
        ):
            return value
    return None


@dataclass(frozen=True)
class PaseoCreateRequest:
    action_key: str
    title: str
    labels: dict[str, str]
    prompt: RuntimePrompt
    repository_path: str
    base_sha: str
    profile: RuntimeProfile
    parent_agent_id: str | None


@dataclass(frozen=True)
class PaseoAgentRecord:
    agent_id: str
    session_id: str
    workspace_id: str
    workspace: str
    parent_agent_id: str | None
    provider: str
    model: str
    profile_digest: str
    thinking: str
    mode: str
    features: dict[str, Any]
    labels: dict[str, str]
    lifecycle: str
    archived: bool = False
    result_claim: ResultClaim | None = None
    evidence: tuple[TypedEvidence, ...] = ()
    output_text: str | None = None


class PaseoClient(Protocol):
    """Host boundary used by the production Paseo Runtime Adapter."""

    def find_by_labels(
        self, labels: dict[str, str]
    ) -> tuple[PaseoAgentRecord, ...]: ...

    def create(self, request: PaseoCreateRequest) -> PaseoAgentRecord: ...

    def inspect(self, agent_id: str) -> PaseoAgentRecord: ...

    def send_prompt(
        self,
        agent_id: str,
        prompt: RuntimePrompt,
        *,
        action_key: str,
    ) -> None: ...

    def prompt_acceptance_count(
        self,
        agent_id: str,
        prompt: RuntimePrompt,
    ) -> int: ...

    def update_labels(self, agent_id: str, labels: dict[str, str]) -> None: ...

    def read_output(self, agent_id: str) -> str | None: ...

    def stop(self, agent_id: str) -> None: ...

    def resume(self, agent_id: str) -> None: ...

    def archive(self, agent_id: str) -> None: ...

    def observed_worker_turn_capacity(
        self,
        profile: RuntimeProfile | None,
    ) -> int | None: ...


class InMemoryPaseoClient:
    """Deterministic contract fake for Paseo lifecycle capabilities."""

    def __init__(
        self,
        *,
        create_failures: tuple[str, ...] = (),
        send_acceptances: tuple[bool, ...] = (),
        worker_turn_capacity: int | None = None,
    ):
        self._agents: dict[str, PaseoAgentRecord] = {}
        self._create_failures = list(create_failures)
        self._send_acceptances = list(send_acceptances)
        self._accepted_prompt_digests: dict[str, list[str]] = {}
        self._worker_turn_capacity = worker_turn_capacity
        self.create_count = 0
        self.send_count = 0
        self.create_prompt_digests: list[str] = []

    def observed_worker_turn_capacity(
        self,
        _profile: RuntimeProfile | None,
    ) -> int | None:
        return self._worker_turn_capacity

    def set_output(self, agent_id: str, output_text: str) -> None:
        record = self.inspect(agent_id)
        self._agents[agent_id] = replace(
            record,
            output_text=output_text,
            lifecycle="idle",
        )

    def find_by_labels(self, labels: dict[str, str]) -> tuple[PaseoAgentRecord, ...]:
        return tuple(
            agent
            for agent in self._agents.values()
            if not agent.archived
            and all(agent.labels.get(key) == value for key, value in labels.items())
        )

    def create(self, request: PaseoCreateRequest) -> PaseoAgentRecord:
        self.create_count += 1
        self.create_prompt_digests.append(request.prompt.digest)
        existing = self.find_by_labels({"gwo.action_key": request.action_key})
        if existing:
            if len(existing) != 1:
                raise RuntimeAdapterError(
                    "MATERIALIZATION_AMBIGUOUS",
                    "multiple Paseo Agents match one Admission",
                    failure_class="ambiguous",
                )
            return existing[0]
        failure = None if not self._create_failures else self._create_failures.pop(0)
        if failure == "transient":
            raise RuntimeAdapterError(
                "PASEO_CREATE_TRANSIENT",
                "synthetic transient Paseo creation failure",
                failure_class="transient",
            )
        if failure == "permanent":
            raise RuntimeAdapterError(
                "PASEO_CREATE_CONFIGURATION_REJECTED",
                "synthetic permanent Paseo configuration rejection",
                failure_class="permanent",
            )
        suffix = digest_bytes(request.action_key.encode("utf-8"))[:20]
        agent_id = f"agent:{suffix}"
        record = PaseoAgentRecord(
            agent_id=agent_id,
            session_id=f"session:{suffix}",
            workspace_id=f"workspace:{suffix}",
            workspace=str(
                (Path(request.repository_path) / f".paseo-{suffix}").resolve()
            ),
            parent_agent_id=request.parent_agent_id,
            provider=request.profile.provider,
            model=request.profile.model,
            profile_digest=request.profile.digest,
            thinking=request.profile.thinking,
            mode=request.profile.mode,
            features=dict(request.profile.features),
            labels={
                **request.labels,
                "gwo.action_key": request.action_key,
            },
            lifecycle="running",
        )
        self._agents[agent_id] = record
        self._accepted_prompt_digests[agent_id] = [request.prompt.digest]
        if failure == "ambiguous_after_create":
            raise RuntimeAdapterError(
                "PASEO_CREATE_AMBIGUOUS",
                "synthetic lost Paseo creation acknowledgement",
                failure_class="ambiguous",
            )
        return record

    def inspect(self, agent_id: str) -> PaseoAgentRecord:
        try:
            return self._agents[agent_id]
        except KeyError as error:
            raise RuntimeAdapterError(
                "RUNTIME_BINDING_UNKNOWN",
                "Paseo Agent does not exist",
                failure_class="ambiguous",
            ) from error

    def send_prompt(
        self,
        agent_id: str,
        prompt: RuntimePrompt,
        *,
        action_key: str,
    ) -> None:
        del action_key
        self.send_count += 1
        record = self.inspect(agent_id)
        accepted = (
            True
            if not self._send_acceptances
            else self._send_acceptances.pop(0)
        )
        if accepted:
            self._accepted_prompt_digests.setdefault(agent_id, []).append(
                prompt.digest
            )
        self._agents[agent_id] = replace(
            record,
            lifecycle="running" if accepted else "idle",
            output_text=None,
        )

    def prompt_acceptance_count(
        self,
        agent_id: str,
        prompt: RuntimePrompt,
    ) -> int:
        self.inspect(agent_id)
        return self._accepted_prompt_digests.get(agent_id, []).count(
            prompt.digest
        )

    def update_labels(self, agent_id: str, labels: dict[str, str]) -> None:
        record = self.inspect(agent_id)
        self._agents[agent_id] = replace(
            record,
            labels={**record.labels, **labels},
        )

    def read_output(self, agent_id: str) -> str | None:
        return self.inspect(agent_id).output_text

    def stop(self, agent_id: str) -> None:
        record = self.inspect(agent_id)
        self._agents[agent_id] = replace(record, lifecycle="idle")

    def resume(self, agent_id: str) -> None:
        record = self.inspect(agent_id)
        self._agents[agent_id] = replace(record, lifecycle="running")

    def archive(self, agent_id: str) -> None:
        record = self.inspect(agent_id)
        self._agents[agent_id] = replace(
            record,
            lifecycle="archived",
            archived=True,
        )


class PaseoCliClient:
    """Concrete Paseo client for the public CLI lifecycle surface."""

    def __init__(self, executable: str = "paseo"):
        self.executable = shutil.which(executable) or executable
        self._command_prefix = (self.executable,)
        self._command_environment: dict[str, str] | None = None
        if sys.platform == "win32" and Path(self.executable).suffix.lower() in {
            ".bat",
            ".cmd",
        }:
            install_root = Path(
                os.environ.get("ProgramFiles", r"C:\Program Files")
            ) / "Paseo"
            app_executable = install_root / "Paseo.exe"
            resources = install_root / "resources"
            runner = (
                resources
                / "app.asar.unpacked"
                / "dist"
                / "daemon"
                / "node-entrypoint-runner.js"
            )
            if app_executable.is_file() and runner.is_file():
                self._command_prefix = (
                    str(app_executable),
                    "--disable-warning=DEP0040",
                    str(runner),
                    "node-script",
                    str(
                        resources
                        / "app.asar"
                        / "node_modules"
                        / "@getpaseo"
                        / "cli"
                        / "dist"
                        / "index.js"
                    ),
                )
                self._command_environment = {
                    **os.environ,
                    "ELECTRON_RUN_AS_NODE": "1",
                    "PASEO_NODE_ENV": "production",
                    "PASEO_DESKTOP_MANAGED": "1",
                }
    @staticmethod
    def classify_failure(message: str, *, default: str = "transient") -> str:
        lowered = message.casefold()
        permanent_markers = (
            "unauthorized",
            "forbidden",
            "authentication",
            "permission denied",
            "invalid configuration",
            "unknown provider",
            "unknown model",
            "certificate",
            "tls",
        )
        if any(marker in lowered for marker in permanent_markers):
            return "permanent"
        transient_markers = (
            "temporarily unavailable",
            "timed out",
            "timeout",
            "connection reset",
            "connection refused",
            "busy",
            "rate limit",
        )
        if any(marker in lowered for marker in transient_markers):
            return "transient"
        return default

    def _raise_process_start_error(
        self,
        error: OSError,
        *,
        failure_class: str,
    ) -> None:
        detail = str(error).casefold()
        if (
            getattr(error, "winerror", None) == 206
            or error.errno == errno.E2BIG
            or "argument list too long" in detail
            or "command line is too long" in detail
            or "filename or extension is too long" in detail
        ):
            raise RuntimeAdapterError(
                "PASEO_COMMAND_LINE_OVERFLOW",
                "Paseo process command line exceeded the host limit",
                failure_class="permanent",
            ) from error
        if isinstance(error, FileNotFoundError) or getattr(
            error, "winerror", None
        ) in {2, 3}:
            raise RuntimeAdapterError(
                "PASEO_EXECUTABLE_UNAVAILABLE",
                f"Paseo executable cannot start: {self.executable}",
                failure_class="permanent",
            ) from error
        raise RuntimeAdapterError(
            "PASEO_PROCESS_START_FAILED",
            f"Paseo process could not start: {error}",
            failure_class=failure_class,
        ) from error

    def _run(
        self,
        args: list[str],
        *,
        failure_class: str = "transient",
    ) -> Any:
        try:
            result = subprocess.run(
                [*self._command_prefix, *args],
                capture_output=True,
                text=True,
                encoding="utf-8",
                env=self._command_environment,
            )
        except OSError as error:
            self._raise_process_start_error(
                error,
                failure_class=failure_class,
            )
        if result.returncode != 0:
            detail = (
                result.stderr.strip()
                or result.stdout.strip()
                or "Paseo operation failed"
            )
            raise RuntimeAdapterError(
                "PASEO_OPERATION_FAILED",
                detail,
                failure_class=self.classify_failure(
                    detail,
                    default=failure_class,
                ),
            )
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError as error:
            raise RuntimeAdapterError(
                "PASEO_READBACK_INVALID",
                "Paseo returned non-JSON lifecycle data",
                failure_class="ambiguous",
            ) from error

    def _run_text(
        self,
        args: list[str],
        *,
        failure_class: str = "transient",
    ) -> str:
        try:
            result = subprocess.run(
                [*self._command_prefix, *args],
                capture_output=True,
                text=True,
                encoding="utf-8",
                env=self._command_environment,
            )
        except OSError as error:
            self._raise_process_start_error(
                error,
                failure_class=failure_class,
            )
        if result.returncode != 0:
            detail = (
                result.stderr.strip()
                or result.stdout.strip()
                or "Paseo operation failed"
            )
            raise RuntimeAdapterError(
                "PASEO_OPERATION_FAILED",
                detail,
                failure_class=self.classify_failure(
                    detail,
                    default=failure_class,
                ),
            )
        return result.stdout

    @staticmethod
    def _labels(value: Any) -> dict[str, str]:
        if isinstance(value, dict):
            return {
                str(key): str(item)
                for key, item in value.items()
                if isinstance(key, str)
            }
        if isinstance(value, list):
            labels: dict[str, str] = {}
            for item in value:
                if isinstance(item, str) and "=" in item:
                    key, content = item.split("=", 1)
                    labels[key] = content
                elif isinstance(item, dict):
                    key = item.get("key") or item.get("Key")
                    content = item.get("value") or item.get("Value")
                    if isinstance(key, str) and isinstance(content, str):
                        labels[key] = content
            return labels
        return {}

    @classmethod
    def _agent(cls, payload: dict[str, Any]) -> PaseoAgentRecord:
        agent_id = payload.get("Id") or payload.get("id")
        session_id = (
            payload.get("SessionId")
            or payload.get("sessionId")
            or payload.get("ProviderSessionId")
            or agent_id
        )
        worktree = payload.get("Worktree") or payload.get("worktree") or {}
        workspace_id = (
            (
                worktree.get("Id") or worktree.get("id")
                if isinstance(worktree, dict)
                else worktree
            )
            or payload.get("WorkspaceId")
            or payload.get("workspaceId")
            or agent_id
        )
        workspace = (
            payload.get("Cwd")
            or payload.get("cwd")
            or (
                worktree.get("Path") or worktree.get("path")
                if isinstance(worktree, dict)
                else None
            )
        )
        provider = payload.get("Provider") or payload.get("provider")
        model = payload.get("Model") or payload.get("model")
        runtime_settings = (
            payload.get("RuntimeSettings") or payload.get("runtimeSettings") or {}
        )
        if not isinstance(runtime_settings, dict):
            runtime_settings = {}
        thinking = (
            payload.get("Thinking")
            or payload.get("thinking")
            or runtime_settings.get("thinkingOptionId")
        )
        mode = (
            payload.get("Mode") or payload.get("mode") or runtime_settings.get("modeId")
        )
        features = runtime_settings.get("features") or {}
        if not isinstance(features, dict):
            raise RuntimeAdapterError(
                "PASEO_READBACK_INVALID",
                "Paseo Runtime features are not an object",
                failure_class="ambiguous",
            )
        labels = cls._labels(payload.get("Labels") or payload.get("labels"))
        if provider == "kimi":
            provider = "kimi-cli"
        profile_digest = labels.get("gwo.profile_digest", "")
        if not all(
            isinstance(value, str) and value
            for value in (
                agent_id,
                session_id,
                workspace_id,
                workspace,
                provider,
                model,
                thinking,
                mode,
            )
        ):
            raise RuntimeAdapterError(
                "PASEO_READBACK_INVALID",
                "Paseo did not return Agent, session, Workspace, and runtime identity",
                failure_class="ambiguous",
            )
        lifecycle = str(
            payload.get("Status") or payload.get("status") or "unknown"
        ).casefold()
        archived = bool(payload.get("Archived") or payload.get("archived"))
        return PaseoAgentRecord(
            agent_id=agent_id,
            session_id=session_id,
            workspace_id=workspace_id,
            workspace=workspace,
            parent_agent_id=(
                payload.get("ParentAgentId") or payload.get("parentAgentId")
            ),
            provider=provider,
            model=model,
            profile_digest=profile_digest,
            thinking=thinking,
            mode=mode,
            features=features,
            labels=labels,
            lifecycle="archived" if archived else lifecycle,
            archived=archived,
        )

    def find_by_labels(self, labels: dict[str, str]) -> tuple[PaseoAgentRecord, ...]:
        command = ["ls", "--global", "--all"]
        for key, value in sorted(labels.items()):
            command.extend(["--label", f"{key}={value}"])
        command.append("--json")
        payload = self._run(command)
        if not isinstance(payload, list):
            raise RuntimeAdapterError(
                "PASEO_READBACK_INVALID",
                "Paseo Agent listing is not a list",
                failure_class="ambiguous",
            )
        records = []
        for item in payload:
            if not isinstance(item, dict):
                raise RuntimeAdapterError(
                    "PASEO_READBACK_INVALID",
                    "Paseo Agent listing contains an invalid identity",
                    failure_class="ambiguous",
                )
            agent_id = item.get("id") or item.get("Id")
            if not isinstance(agent_id, str) or not agent_id:
                raise RuntimeAdapterError(
                    "PASEO_READBACK_INVALID",
                    "Paseo Agent listing omitted an identity",
                    failure_class="ambiguous",
                )
            record = self.inspect(agent_id)
            records.append(
                replace(
                    record,
                    labels={
                        **record.labels,
                        **labels,
                    },
                    profile_digest=labels.get(
                        "gwo.profile_digest",
                        record.profile_digest,
                    ),
                    parent_agent_id=labels.get(
                        "gwo.parent_agent",
                        record.parent_agent_id,
                    ),
                ),
            )
        return tuple(records)

    def create(self, request: PaseoCreateRequest) -> PaseoAgentRecord:
        worktree = f"gwo-{digest_bytes(request.action_key.encode('utf-8'))[:16]}"
        creation_labels = {
            **request.labels,
            "gwo.action_key": request.action_key,
        }
        prompt_bytes = request.prompt.text.encode("utf-8")
        inline = len(prompt_bytes) <= PASEO_INLINE_PROMPT_MAX_BYTES
        initial_prompt = (
            request.prompt.text
            if inline
            else (
                "GWO transport bootstrap "
                f"{request.action_key}. Wait for the frozen Prompt; "
                "do not inspect or modify files yet."
            )
        )
        command = [
            "run",
            "--detach",
            "--title",
            request.title,
            "--provider",
            (
                "kimi"
                if request.profile.provider == "kimi-cli"
                else request.profile.provider
            ),
            "--model",
            request.profile.model,
            "--thinking",
            request.profile.thinking,
            "--mode",
            request.profile.mode,
            "--worktree",
            worktree,
            "--base",
            request.base_sha,
            "--cwd",
            request.repository_path,
        ]
        for key, value in sorted(creation_labels.items()):
            command.extend(["--label", f"{key}={value}"])
        command.extend(["--json", initial_prompt])
        payload = self._run(command, failure_class="ambiguous")
        if not isinstance(payload, dict):
            raise RuntimeAdapterError(
                "PASEO_READBACK_INVALID",
                "Paseo creation omitted the Agent identity",
                failure_class="ambiguous",
            )
        agent_payload = payload.get("agent") or payload.get("Agent") or payload
        if not isinstance(agent_payload, dict):
            raise RuntimeAdapterError(
                "PASEO_READBACK_INVALID",
                "Paseo creation returned an invalid Agent",
                failure_class="ambiguous",
            )
        agent_id = (
            agent_payload.get("id")
            or agent_payload.get("Id")
            or agent_payload.get("agentId")
            or agent_payload.get("AgentId")
        )
        if not isinstance(agent_id, str) or not agent_id:
            raise RuntimeAdapterError(
                "PASEO_READBACK_INVALID",
                "Paseo creation did not return an Agent ID",
                failure_class="ambiguous",
            )
        matches = self.find_by_labels(creation_labels)
        exact = [item for item in matches if item.agent_id == agent_id]
        if len(matches) != 1 or len(exact) != 1:
            raise RuntimeAdapterError(
                "PASEO_READBACK_INVALID",
                "Paseo creation did not read back exact identity labels",
                failure_class="ambiguous",
            )
        record = exact[0]
        if not inline:
            deadline = time.monotonic() + PASEO_BOOTSTRAP_WAIT_SECONDS
            while record.lifecycle != "idle":
                if time.monotonic() >= deadline:
                    break
                time.sleep(PASEO_BOOTSTRAP_POLL_SECONDS)
                record = self.inspect(agent_id)
        pending = self.find_by_labels(creation_labels)
        exact = [item for item in pending if item.agent_id == agent_id]
        if len(pending) != 1 or len(exact) != 1:
            raise RuntimeAdapterError(
                "PASEO_READBACK_INVALID",
                "Paseo creation identity became ambiguous",
                failure_class="ambiguous",
            )
        return exact[0]

    def inspect(self, agent_id: str) -> PaseoAgentRecord:
        payload = self._run(["inspect", agent_id, "--json"])
        if not isinstance(payload, dict):
            raise RuntimeAdapterError(
                "PASEO_READBACK_INVALID",
                "Paseo inspect returned an invalid Agent",
                failure_class="ambiguous",
            )
        return self._agent(payload)

    def send_prompt(
        self,
        agent_id: str,
        prompt: RuntimePrompt,
        *,
        action_key: str,
    ) -> None:
        del action_key
        descriptor, prompt_path = tempfile.mkstemp(
            prefix="gwo-prompt-",
            suffix=".txt",
        )
        try:
            with os.fdopen(
                descriptor,
                "w",
                encoding="utf-8",
                newline="",
            ) as stream:
                stream.write(prompt.text)
            payload = self._run(
                [
                    "send",
                    agent_id,
                    "--prompt-file",
                    prompt_path,
                    "--no-wait",
                    "--json",
                ],
                failure_class="ambiguous",
            )
        finally:
            Path(prompt_path).unlink(missing_ok=True)
        status = (
            payload.get("status") or payload.get("Status")
            if isinstance(payload, dict)
            else None
        )
        enqueued = (
            payload.get("enqueued", payload.get("Enqueued"))
            if isinstance(payload, dict)
            else None
        )
        if (
            isinstance(status, str)
            and status.casefold() in {"rejected", "not-sent", "not_sent"}
            and enqueued is False
        ):
            raise RuntimeAdapterError(
                "PASEO_PROMPT_REJECTED",
                "Paseo explicitly rejected the Prompt before enqueue",
                failure_class="transient",
            )
        if not isinstance(status, str) or status.casefold() != "sent":
            raise RuntimeAdapterError(
                "PASEO_PROMPT_ACK_INVALID",
                "Paseo did not acknowledge the bounded Prompt send",
                failure_class="ambiguous",
            )

    def prompt_acceptance_count(
        self,
        agent_id: str,
        prompt: RuntimePrompt,
    ) -> int:
        activity = self._run_text(
            ["logs", agent_id],
            failure_class="ambiguous",
        )
        needle = f"[User] {prompt.text}"
        count = 0
        offset = 0
        while True:
            index = activity.find(needle, offset)
            if index < 0:
                return count
            starts_at_boundary = index == 0 or activity[index - 1] in "\r\n"
            end = index + len(needle)
            ends_at_boundary = end == len(activity) or activity[end] in "\r\n"
            if starts_at_boundary and ends_at_boundary:
                count += 1
            offset = index + len("[User] ")

    def update_labels(self, agent_id: str, labels: dict[str, str]) -> None:
        command = ["agent", "update", agent_id]
        for key, value in sorted(labels.items()):
            command.extend(["--label", f"{key}={value}"])
        command.append("--json")
        self._run(command)
        matches = self.find_by_labels(labels)
        exact = [record for record in matches if record.agent_id == agent_id]
        if len(exact) != 1:
            raise RuntimeAdapterError(
                "PASEO_LABEL_READBACK_FAILED",
                "Paseo did not read back the exact Agent label update",
                failure_class="ambiguous",
            )

    def read_output(self, agent_id: str) -> str | None:
        output = self._run_text(
            ["logs", agent_id, "--tail", "200"],
        )
        return output[-262_144:] if output else None

    def stop(self, agent_id: str) -> None:
        self._run(["stop", agent_id, "--json"])

    def resume(self, agent_id: str) -> None:
        self._run(
            [
                "send",
                "--no-wait",
                "--json",
                agent_id,
                "Continue the accepted GWO Plan Node from current state.",
            ]
        )

    def archive(self, agent_id: str) -> None:
        self._run(["archive", agent_id, "--json"])


class PaseoRuntimeAdapter:
    """Paseo resident-Agent lifecycle with Admission-rooted adoption."""

    adapter_name = "paseo"

    def __init__(self, client: PaseoClient):
        self.client = client
        self._prompts: dict[str, RuntimePrompt] = {}
        self._worker_observations: dict[
            str,
            tuple[str, RuntimeObservation],
        ] = {}
        self._deferred_repository_checks: set[str] = set()

    def observed_worker_turn_capacity(
        self,
        profile: RuntimeProfile | None,
    ) -> int | None:
        observe = getattr(
            self.client,
            "observed_worker_turn_capacity",
            None,
        )
        return None if not callable(observe) else observe(profile)

    @staticmethod
    def _identity_labels(admission: RuntimeAdmission) -> dict[str, str]:
        return {
            "gwo.repository": admission.repository,
            "gwo.plan": admission.plan_digest,
            "gwo.node": admission.node_key,
            "gwo.admission": admission.admission_id,
            "gwo.base_sha": admission.base_sha,
        }

    def _find_one(
        self,
        labels: dict[str, str],
    ) -> PaseoAgentRecord | None:
        matches = self.client.find_by_labels(labels)
        if len(matches) > 1:
            raise RuntimeAdapterError(
                "MATERIALIZATION_AMBIGUOUS",
                "multiple Paseo Agents match one Admission",
                failure_class="ambiguous",
            )
        return None if not matches else matches[0]

    def _find_exact(
        self,
        agent_id: str,
        labels: dict[str, str],
        *,
        code: str = "RUNTIME_IDENTITY_MISMATCH",
    ) -> PaseoAgentRecord:
        matches = self.client.find_by_labels(labels)
        exact = [record for record in matches if record.agent_id == agent_id]
        if len(matches) != 1 or len(exact) != 1:
            raise RuntimeAdapterError(
                code,
                "Paseo authoritative label readback did not identify one Agent",
                failure_class="ambiguous",
            )
        return exact[0]

    def _prompt_is_accepted(
        self,
        agent_id: str,
        prompt: RuntimePrompt,
        identity_labels: dict[str, str],
    ) -> bool:
        published = self.client.find_by_labels(
            {
                **identity_labels,
                "gwo.prompt_digest": prompt.digest,
            }
        )
        if published:
            exact = [record for record in published if record.agent_id == agent_id]
            if len(published) != 1 or len(exact) != 1:
                raise RuntimeAdapterError(
                    "PROMPT_IDENTITY_MISMATCH",
                    "Prompt digest label identifies a different or duplicate Agent",
                    failure_class="ambiguous",
                )
        count = self.client.prompt_acceptance_count(agent_id, prompt)
        if count > 1:
            raise RuntimeAdapterError(
                "PROMPT_ACCEPTANCE_DUPLICATE",
                "Paseo activity contains the exact Prompt more than once",
                failure_class="ambiguous",
            )
        if published and count != 1:
            raise RuntimeAdapterError(
                "PROMPT_ACCEPTANCE_READBACK_MISMATCH",
                (
                    "Prompt digest label exists without exactly one "
                    "authoritative activity boundary"
                ),
                failure_class="ambiguous",
            )
        if published:
            return True
        if count == 0:
            return False
        self.client.update_labels(
            agent_id,
            {"gwo.prompt_digest": prompt.digest},
        )
        self._find_exact(
            agent_id,
            {
                **identity_labels,
                "gwo.prompt_digest": prompt.digest,
            },
            code="PROMPT_LABEL_READBACK_FAILED",
        )
        return True

    @staticmethod
    def _delivery_label_value(
        phase: str,
        ordinal: int,
        action_key: str,
    ) -> str:
        return (
            f"{phase}:{ordinal}:"
            f"{digest_bytes(action_key.encode('utf-8'))[:32]}"
        )

    def _read_delivery_state(
        self,
        agent_id: str,
        identity_labels: dict[str, str],
        action_key: str,
    ) -> tuple[str, int] | None:
        found: list[tuple[str, int]] = []
        for ordinal in reversed(range(PASEO_PROMPT_DELIVERY_ATTEMPTS)):
            for phase in reversed(PASEO_PROMPT_DELIVERY_PHASES):
                value = self._delivery_label_value(
                    phase,
                    ordinal,
                    action_key,
                )
                matches = self.client.find_by_labels(
                    {
                        **identity_labels,
                        "gwo.prompt_delivery": value,
                    }
                )
                if not matches:
                    continue
                exact = [
                    record for record in matches if record.agent_id == agent_id
                ]
                if len(matches) != 1 or len(exact) != 1:
                    raise RuntimeAdapterError(
                        "PROMPT_DELIVERY_IDENTITY_MISMATCH",
                        (
                            "Prompt delivery state identifies a different "
                            "or duplicate Agent"
                        ),
                        failure_class="ambiguous",
                    )
                found.append((phase, ordinal))
        if len(found) > 1:
            raise RuntimeAdapterError(
                "PROMPT_DELIVERY_STATE_AMBIGUOUS",
                "Paseo read back more than one current Prompt delivery state",
                failure_class="ambiguous",
            )
        return None if not found else found[0]

    def _publish_delivery_state(
        self,
        agent_id: str,
        identity_labels: dict[str, str],
        action_key: str,
        *,
        phase: str,
        ordinal: int,
    ) -> tuple[str, int]:
        value = self._delivery_label_value(phase, ordinal, action_key)
        self.client.update_labels(
            agent_id,
            {"gwo.prompt_delivery": value},
        )
        self._find_exact(
            agent_id,
            {
                **identity_labels,
                "gwo.prompt_delivery": value,
            },
            code="PROMPT_DELIVERY_STATE_READBACK_FAILED",
        )
        return phase, ordinal

    def _converge_prompt_delivery(
        self,
        agent_id: str,
        prompt: RuntimePrompt,
        *,
        identity_labels: dict[str, str],
        agent_labels: dict[str, str],
        action_key: str,
    ) -> None:
        deadline = time.monotonic() + PASEO_BOOTSTRAP_WAIT_SECONDS
        self._find_exact(agent_id, agent_labels)
        if self._prompt_is_accepted(
            agent_id,
            prompt,
            identity_labels,
        ):
            return
        state = self._read_delivery_state(
            agent_id,
            identity_labels,
            action_key,
        )
        prompt_was_inline = (
            len(prompt.text.encode("utf-8"))
            <= PASEO_INLINE_PROMPT_MAX_BYTES
        )
        send_authorized = False
        while time.monotonic() < deadline:
            if self._prompt_is_accepted(
                agent_id,
                prompt,
                identity_labels,
            ):
                return
            agent = self._find_exact(agent_id, agent_labels)
            if state is None:
                if prompt_was_inline:
                    # Creation already acknowledged this exact Prompt as its
                    # initial user message. A missing boundary can only remain
                    # ambiguous; a separate send would duplicate that effect.
                    time.sleep(PASEO_PROMPT_SETTLE_SECONDS)
                    continue
                if agent.lifecycle != "idle":
                    time.sleep(PASEO_BOOTSTRAP_POLL_SECONDS)
                    continue
                state = self._publish_delivery_state(
                    agent_id,
                    identity_labels,
                    action_key,
                    phase="prepared",
                    ordinal=0,
                )
                send_authorized = True
            phase, ordinal = state
            if phase == "prepared" and not send_authorized:
                # The process that published this receipt may already have
                # invoked Paseo. Recovery cannot distinguish that case from a
                # crash immediately before invocation, so it must not resend.
                time.sleep(PASEO_PROMPT_SETTLE_SECONDS)
                continue
            if phase not in {"prepared", "rejected"}:
                # Acknowledged and legacy finite-settlement phases all mean
                # that Paseo may still surface the first exact boundary. Idle
                # and elapsed time cannot prove that an asynchronous send was
                # absent from the queue.
                time.sleep(PASEO_PROMPT_SETTLE_SECONDS)
                continue

            if phase == "rejected":
                if ordinal + 1 >= PASEO_PROMPT_DELIVERY_ATTEMPTS:
                    raise RuntimeAdapterError(
                        "PROMPT_DELIVERY_REJECTIONS_EXHAUSTED",
                        (
                            "three explicit pre-enqueue Prompt rejections "
                            "were exhausted"
                        ),
                        failure_class="transient",
                    )
                if agent.lifecycle != "idle":
                    time.sleep(PASEO_BOOTSTRAP_POLL_SECONDS)
                    continue
                ordinal += 1
                state = self._publish_delivery_state(
                    agent_id,
                    identity_labels,
                    action_key,
                    phase="prepared",
                    ordinal=ordinal,
                )
                send_authorized = True

            send_authorized = False
            try:
                self.client.send_prompt(
                    agent_id,
                    prompt,
                    action_key=action_key,
                )
            except RuntimeAdapterError as error:
                if error.code != "PASEO_PROMPT_REJECTED":
                    raise RuntimeAdapterError(
                        "PROMPT_DELIVERY_AMBIGUOUS",
                        (
                            "Paseo Prompt send may have been enqueued; "
                            "authoritative acceptance remains unresolved"
                        ),
                        failure_class="ambiguous",
                    ) from error
                state = self._publish_delivery_state(
                    agent_id,
                    identity_labels,
                    action_key,
                    phase="rejected",
                    ordinal=ordinal,
                )
                continue

            state = self._publish_delivery_state(
                agent_id,
                identity_labels,
                action_key,
                phase="acked",
                ordinal=ordinal,
            )
            if self._prompt_is_accepted(
                agent_id,
                prompt,
                identity_labels,
            ):
                return
            time.sleep(PASEO_PROMPT_SETTLE_SECONDS)
        raise RuntimeAdapterError(
            "PROMPT_DELIVERY_AMBIGUOUS",
            (
                "Paseo Prompt delivery has no exact acceptance boundary; "
                "the same Agent and action remain ambiguous"
            ),
            failure_class="ambiguous",
        )

    @staticmethod
    def _binding_labels(
        binding: RuntimeBinding,
        *,
        include_prompt: bool = True,
        include_attempt: bool = True,
    ) -> dict[str, str]:
        labels = {
            "gwo.repository": binding.repository,
            "gwo.plan": binding.plan_digest,
            "gwo.node": binding.node_key,
            "gwo.admission": binding.admission_id,
        }
        optional = {
            "gwo.repository_path": binding.repository_path,
            "gwo.runtime_profile": binding.runtime_profile,
            "gwo.profile_digest": binding.profile_digest,
            "gwo.parent_agent": binding.parent_agent_id,
            "gwo.base_sha": binding.base_sha,
            "gwo.prompt_digest": (
                binding.prompt_digest if include_prompt else None
            ),
            "gwo.attempt": binding.attempt_id if include_attempt else None,
        }
        labels.update(
            {
                key: value
                for key, value in optional.items()
                if isinstance(value, str) and value
            }
        )
        return labels

    @staticmethod
    def _binding(agent: PaseoAgentRecord) -> RuntimeBinding:
        labels = agent.labels
        required = ("gwo.repository", "gwo.plan", "gwo.node", "gwo.admission")
        if any(
            not isinstance(labels.get(name), str) or not labels[name]
            for name in required
        ):
            raise RuntimeAdapterError(
                "RUNTIME_IDENTITY_MISMATCH",
                "Paseo Agent lacks GWO identity labels",
                failure_class="ambiguous",
            )
        prompt_digest = labels.get("gwo.prompt_digest")
        return RuntimeBinding(
            adapter="paseo",
            runtime_id=agent.agent_id,
            repository=labels["gwo.repository"],
            plan_digest=labels["gwo.plan"],
            node_key=labels["gwo.node"],
            admission_id=labels["gwo.admission"],
            repository_path=labels.get("gwo.repository_path", ""),
            workspace=agent.workspace,
            prompt_accepted=isinstance(prompt_digest, str) and bool(prompt_digest),
            prompt_digest=prompt_digest,
            attempt_id=labels.get("gwo.attempt"),
            agent_id=agent.agent_id,
            session_id=agent.session_id,
            workspace_id=agent.workspace_id,
            parent_agent_id=agent.parent_agent_id,
            runtime_profile=labels.get("gwo.runtime_profile"),
            profile_digest=agent.profile_digest,
            provider=agent.provider,
            model=agent.model,
            thinking=agent.thinking,
            mode=agent.mode,
            features_digest=digest_value(agent.features),
            base_sha=labels.get("gwo.base_sha"),
        )

    @staticmethod
    def _assert_admission_identity(
        admission: RuntimeAdmission,
        binding: RuntimeBinding,
        prompt: RuntimePrompt,
    ) -> None:
        profile = admission.runtime_profile
        if (
            profile is None
            or binding.repository != admission.repository
            or binding.plan_digest != admission.plan_digest
            or binding.node_key != admission.node_key
            or binding.admission_id != admission.admission_id
            or binding.parent_agent_id != admission.parent_agent_id
            or binding.runtime_profile != profile.name
            or binding.profile_digest != profile.digest
            or binding.provider != profile.provider
            or binding.model != profile.model
            or binding.base_sha != admission.base_sha
            or binding.thinking != profile.thinking
            or binding.mode != profile.mode
            or binding.features_digest != digest_value(profile.features)
            or (
                binding.prompt_accepted
                and binding.prompt_digest != prompt.digest
            )
            or (
                not binding.prompt_accepted
                and binding.prompt_digest is not None
            )
        ):
            raise RuntimeAdapterError(
                "RUNTIME_IDENTITY_MISMATCH",
                "Paseo readback does not match Admission, profile, and Prompt",
                failure_class="ambiguous",
            )

    def materialize(
        self,
        admission: RuntimeAdmission,
        prompt: RuntimePrompt | None = None,
    ) -> RuntimeBinding:
        if admission.runtime_profile is None:
            raise RuntimeAdapterError(
                "RUNTIME_PROFILE_MISSING",
                "Paseo Materialization requires a resolved Runtime Profile",
            )
        if prompt is None:
            raise RuntimeAdapterError(
                "PROMPT_MISSING",
                "Paseo Materialization requires the frozen initial Prompt",
            )
        labels = {
            **self._identity_labels(admission),
            "gwo.repository_path": str(Path(admission.repository_path).resolve()),
            "gwo.runtime_profile": admission.runtime_profile.name,
            "gwo.profile_digest": admission.runtime_profile.digest,
        }
        if admission.parent_agent_id is not None:
            labels["gwo.parent_agent"] = admission.parent_agent_id
        existing = self._find_one(labels)
        if existing is None:
            creation_labels = dict(labels)
            if (
                len(prompt.text.encode("utf-8"))
                <= PASEO_INLINE_PROMPT_MAX_BYTES
            ):
                creation_labels["gwo.prompt_delivery"] = (
                    self._delivery_label_value(
                        "acked",
                        0,
                        f"{admission.admission_id}:prompt",
                    )
                )
            existing = self.client.create(
                PaseoCreateRequest(
                    action_key=f"{admission.admission_id}:materialize",
                    title=f"GWO {admission.node_key}",
                    labels=creation_labels,
                    prompt=prompt,
                    repository_path=str(Path(admission.repository_path).resolve()),
                    base_sha=admission.base_sha,
                    profile=admission.runtime_profile,
                    parent_agent_id=admission.parent_agent_id,
                )
            )
        accepted = self._prompt_is_accepted(
            existing.agent_id,
            prompt,
            {"gwo.admission": admission.admission_id},
        )
        existing = self._find_exact(
            existing.agent_id,
            {
                **labels,
                **(
                    {"gwo.prompt_digest": prompt.digest}
                    if accepted
                    else {}
                ),
            },
        )
        binding = self._binding(existing)
        self._assert_admission_identity(admission, binding, prompt)
        self._prompts[admission.admission_id] = prompt
        return binding

    def read_binding(
        self,
        admission: RuntimeAdmission | str,
        prompt: RuntimePrompt | None = None,
    ) -> RuntimeBinding | None:
        if isinstance(admission, str):
            labels = {"gwo.admission": admission}
            expected_admission = None
        else:
            profile = admission.runtime_profile
            if profile is None:
                raise RuntimeAdapterError(
                    "RUNTIME_PROFILE_MISSING",
                    "Paseo readback requires the resolved Runtime Profile",
                )
            labels = {
                **self._identity_labels(admission),
                "gwo.repository_path": str(Path(admission.repository_path).resolve()),
                "gwo.runtime_profile": profile.name,
                "gwo.profile_digest": profile.digest,
            }
            if admission.parent_agent_id is not None:
                labels["gwo.parent_agent"] = admission.parent_agent_id
            expected_admission = admission
        agent = self._find_one(labels)
        if agent is None:
            return None
        accepted = False
        if prompt is not None:
            accepted = self._prompt_is_accepted(
                agent.agent_id,
                prompt,
                {
                    "gwo.admission": (
                        admission
                        if isinstance(admission, str)
                        else admission.admission_id
                    )
                },
            )
        readback = self._find_exact(
            agent.agent_id,
            {
                **labels,
                **(
                    {"gwo.prompt_digest": prompt.digest}
                    if accepted and prompt is not None
                    else {}
                ),
            },
        )
        binding = self._binding(readback)
        if expected_admission is not None and prompt is not None:
            self._assert_admission_identity(
                expected_admission,
                binding,
                prompt,
            )
            self._prompts[expected_admission.admission_id] = prompt
        return binding

    def accept_prompt(self, binding: RuntimeBinding, prompt: RuntimePrompt) -> None:
        if binding.prompt_accepted:
            if binding.prompt_digest != prompt.digest:
                raise RuntimeAdapterError(
                    "PROMPT_IDENTITY_MISMATCH",
                    "Paseo accepted a different Prompt snapshot",
                    failure_class="ambiguous",
                )
        if binding.agent_id is None:
            raise RuntimeAdapterError(
                "RUNTIME_BINDING_UNKNOWN",
                "Paseo Binding has no Agent identity",
                failure_class="ambiguous",
            )
        identity_labels = {"gwo.admission": binding.admission_id}
        self._converge_prompt_delivery(
            binding.agent_id,
            prompt,
            identity_labels=identity_labels,
            agent_labels=self._binding_labels(
                binding,
                include_prompt=binding.prompt_accepted,
                include_attempt=False,
            ),
            action_key=f"{binding.admission_id}:prompt",
        )

    def attach_attempt(
        self,
        binding: RuntimeBinding,
        attempt_id: str,
    ) -> RuntimeBinding:
        if binding.agent_id is None or not binding.prompt_accepted:
            raise RuntimeAdapterError(
                "PROMPT_NOT_ACCEPTED",
                "Attempt cannot bind before Paseo Prompt readback",
            )
        labels = self._binding_labels(
            binding,
            include_attempt=False,
        )
        agent = self._find_exact(binding.agent_id, labels)
        existing = agent.labels.get("gwo.attempt")
        if existing not in {None, attempt_id}:
            raise RuntimeAdapterError(
                "ATTEMPT_IDENTITY_MISMATCH",
                "Paseo Agent already belongs to another Attempt",
                failure_class="ambiguous",
            )
        self.client.update_labels(
            binding.agent_id,
            {"gwo.attempt": attempt_id},
        )
        self._find_exact(
            binding.agent_id,
            {**labels, "gwo.attempt": attempt_id},
            code="ATTEMPT_READBACK_FAILED",
        )
        return replace(binding, attempt_id=attempt_id)

    def resume(self, binding: RuntimeBinding) -> None:
        if binding.agent_id is None:
            raise RuntimeAdapterError(
                "RUNTIME_BINDING_UNKNOWN",
                "Paseo Binding has no Agent identity",
                failure_class="ambiguous",
            )
        self.client.resume(binding.agent_id)

    def defer_repository_checks(self, binding: RuntimeBinding) -> None:
        if binding.agent_id is None:
            raise RuntimeAdapterError(
                "RUNTIME_BINDING_UNKNOWN",
                "Paseo Binding has no Agent identity",
                failure_class="ambiguous",
            )
        self._deferred_repository_checks.add(binding.agent_id)

    @staticmethod
    def _capture_declared_checks(
        observed: RuntimeBinding,
        candidate_sha: str,
        evidence: tuple[TypedEvidence, ...],
        checks: Any,
        *,
        defer_repository: bool,
    ) -> tuple[TypedEvidence, ...]:
        captured = list(evidence)
        captured_ids = {
            item.payload.get("check_id") for item in captured if item.kind == "check"
        }
        for check in checks or ():
            if (
                not isinstance(check, dict)
                or not isinstance(check.get("check_id"), str)
                or not isinstance(check.get("command"), list)
                or not all(isinstance(part, str) and part for part in check["command"])
            ):
                raise RuntimeAdapterError(
                    "CHECK_CONTRACT_INVALID",
                    "frozen Prompt contains an invalid check",
                )
            if (
                check.get("hosted_only") is True
                or check["check_id"] in captured_ids
                or (defer_repository and check.get("suite") == "repository")
            ):
                continue
            result = _run(
                list(check["command"]),
                cwd=Path(observed.workspace),
            )
            environment_requirements = tuple(
                str(item) for item in check.get("environment_requirements") or ()
            )
            environment = _environment_snapshot(
                environment_requirements,
                cwd=Path(observed.workspace),
            )
            base_sha = observed.base_sha
            base_tree = None
            if check.get("base_sensitive") is True:
                if (
                    not isinstance(base_sha, str)
                    or re.fullmatch(r"[0-9a-f]{40}", base_sha) is None
                ):
                    raise RuntimeAdapterError(
                        "CHECK_BASE_PROVENANCE_MISSING",
                        "base-sensitive check lacks the admitted base SHA",
                    )
                base_tree = _git(
                    Path(observed.workspace),
                    "rev-parse",
                    f"{base_sha}^{{tree}}",
                )
            post_check_head = _git(
                Path(observed.workspace),
                "rev-parse",
                "HEAD",
            )
            post_check_status = _git(
                Path(observed.workspace),
                "status",
                "--porcelain=v1",
                "--untracked-files=all",
            )
            post_check_tree = _git(
                Path(observed.workspace),
                "rev-parse",
                "HEAD^{tree}",
            )
            if post_check_head != candidate_sha or post_check_status:
                raise RuntimeAdapterError(
                    "CHECK_CANDIDATE_MUTATED",
                    (
                        "declared check changed Candidate HEAD, index, "
                        "worktree, or untracked inputs"
                    ),
                    failure_class="ambiguous",
                )
            captured.append(
                TypedEvidence._capture(
                    kind="check",
                    subject=candidate_sha,
                    observer_type="runtime_adapter",
                    observer_id=observed.runtime_id,
                    observed_at=_now(),
                    source_ref=(
                        f"paseo://agent/{observed.agent_id}/check/{check['check_id']}"
                    ),
                    payload={
                        "check_id": check["check_id"],
                        "definition_digest": check.get("definition_digest"),
                        "command_digest": digest_value(check["command"]),
                        "observed_tree_digest": post_check_tree,
                        "environment_requirements": list(environment_requirements),
                        "environment_identity": environment,
                        "environment_digest": digest_value(environment),
                        **(
                            {
                                "base_sha": base_sha,
                                "observed_base_tree_digest": base_tree,
                            }
                            if check.get("base_sensitive") is True
                            else {}
                        ),
                        "input_projection_digest": _input_projection_digest(
                            Path(observed.workspace),
                            candidate_sha,
                            tuple(check.get("input_selector") or ()),
                        ),
                        "exit_code": result.returncode,
                        "outcome": ("passed" if result.returncode == 0 else "failed"),
                        "stdout_digest": digest_bytes(result.stdout.encode("utf-8")),
                        "stderr_digest": digest_bytes(result.stderr.encode("utf-8")),
                        "log_digest": digest_bytes(
                            (f"{result.stdout}\n{result.stderr}").encode("utf-8")
                        ),
                    },
                )
            )
        return tuple(captured)

    def capture_deferred_checks(
        self,
        binding: RuntimeBinding,
        observation: RuntimeObservation,
    ) -> RuntimeObservation:
        if binding.agent_id is None or observation.result_claim is None:
            return observation
        prompt = self._prompts.get(binding.admission_id)
        try:
            node = _contract_node_from_prompt(prompt)
        except json.JSONDecodeError as error:
            raise RuntimeAdapterError(
                "PROMPT_SNAPSHOT_INVALID",
                "frozen Paseo Prompt is not valid JSON",
            ) from error
        contract = node.get("output_contract") if isinstance(node, dict) else None
        checks = contract.get("checks") if isinstance(contract, dict) else ()
        completed = replace(
            observation,
            evidence=self._capture_declared_checks(
                observation.binding,
                observation.result_claim.candidate_sha,
                observation.evidence,
                checks,
                defer_repository=False,
            ),
        )
        self._deferred_repository_checks.discard(binding.agent_id)
        output = self.client.read_output(binding.agent_id)
        if output is not None:
            self._worker_observations[binding.agent_id] = (
                digest_bytes(output.encode("utf-8")),
                completed,
            )
        return completed

    def observe(self, binding: RuntimeBinding) -> RuntimeObservation:
        if binding.agent_id is None:
            raise RuntimeAdapterError(
                "RUNTIME_BINDING_UNKNOWN",
                "Paseo Binding has no Agent identity",
                failure_class="ambiguous",
            )
        prompt = self._prompts.get(binding.admission_id)
        if prompt is None or prompt.digest != binding.prompt_digest:
            raise RuntimeAdapterError(
                "PROMPT_SNAPSHOT_MISSING",
                "Paseo observation requires the frozen Prompt snapshot",
                failure_class="ambiguous",
            )
        if not self._prompt_is_accepted(
            binding.agent_id,
            prompt,
            {"gwo.admission": binding.admission_id},
        ):
            raise RuntimeAdapterError(
                "PROMPT_ACCEPTANCE_AMBIGUOUS",
                "Paseo observation lacks one exact Prompt boundary",
                failure_class="ambiguous",
            )
        agent = self._find_exact(
            binding.agent_id,
            self._binding_labels(binding),
        )
        observed = self._binding(agent)
        if (
            observed.admission_id != binding.admission_id
            or observed.plan_digest != binding.plan_digest
            or observed.node_key != binding.node_key
        ):
            raise RuntimeAdapterError(
                "RUNTIME_IDENTITY_MISMATCH",
                "Paseo observation changed GWO identity",
                failure_class="ambiguous",
            )
        output_text = self.client.read_output(binding.agent_id)
        output_identity = (
            None if output_text is None else digest_bytes(output_text.encode("utf-8"))
        )
        cached = self._worker_observations.get(binding.agent_id)
        if (
            output_identity is not None
            and cached is not None
            and cached[0] == output_identity
        ):
            cached_observation = cached[1]
            if cached_observation.result_claim is not None:
                workspace_head = _git(
                    Path(observed.workspace),
                    "rev-parse",
                    "HEAD",
                )
                workspace_status = _git(
                    Path(observed.workspace),
                    "status",
                    "--porcelain=v1",
                    "--untracked-files=all",
                )
                if (
                    workspace_head != cached_observation.result_claim.candidate_sha
                    or workspace_status
                ):
                    raise RuntimeAdapterError(
                        "PASEO_RESULT_READBACK_FAILED",
                        "cached Candidate no longer matches clean Workspace HEAD",
                        failure_class="ambiguous",
                    )
            return replace(cached_observation, binding=observed)
        result_claim = agent.result_claim
        evidence = agent.evidence
        if (
            result_claim is None
            and observed.attempt_id is not None
            and agent.lifecycle in {"idle", "completed", "ready"}
        ):
            envelope = read_bounded_outcome(
                output_text,
                marker="GWO_RESULT",
                action_key=binding.node_key,
            )
            if envelope is not None:
                if envelope.get("terminal_reason") == "no_result":
                    reason = envelope.get("reason")
                    if (
                        not isinstance(reason, str)
                        or not reason.strip()
                        or len(reason.encode("utf-8")) > 8_192
                    ):
                        raise RuntimeAdapterError(
                            "PASEO_NO_RESULT_INVALID",
                            "typed no_result requires one bounded reason",
                        )
                    return RuntimeObservation(
                        binding=observed,
                        lifecycle="completed",
                        result_claim=None,
                        evidence=(
                            TypedEvidence._capture(
                                kind="runtime",
                                subject=str(observed.attempt_id),
                                observer_type="runtime_adapter",
                                observer_id=observed.runtime_id,
                                observed_at=_now(),
                                source_ref=(
                                    f"paseo://agent/{binding.agent_id}/no-result"
                                ),
                                payload={
                                    "terminal_reason": "no_result",
                                    "reason_digest": digest_bytes(
                                        reason.encode("utf-8")
                                    ),
                                },
                            ),
                        ),
                        terminal_reason="no_result",
                        terminal_detail=reason,
                    )
                candidate_sha = envelope.get("candidate_sha")
                if (
                    not isinstance(candidate_sha, str)
                    or re.fullmatch(r"[0-9a-f]{40}", candidate_sha) is None
                ):
                    raise RuntimeAdapterError(
                        "PASEO_RESULT_INVALID",
                        "Paseo Worker returned an invalid Candidate SHA",
                    )
                workspace_head = _git(
                    Path(observed.workspace),
                    "rev-parse",
                    "HEAD",
                )
                workspace_status = _git(
                    Path(observed.workspace),
                    "status",
                    "--porcelain=v1",
                    "--untracked-files=all",
                )
                workspace_tree = _git(
                    Path(observed.workspace),
                    "rev-parse",
                    "HEAD^{tree}",
                )
                if workspace_head != candidate_sha or workspace_status:
                    raise RuntimeAdapterError(
                        "PASEO_RESULT_READBACK_FAILED",
                        (
                            "Paseo Worker Candidate does not match a clean "
                            "Workspace HEAD"
                        ),
                        failure_class="ambiguous",
                    )
                result_claim = ResultClaim(
                    attempt_id=observed.attempt_id,
                    node_key=observed.node_key,
                    candidate_sha=candidate_sha,
                    assertions={
                        "paseo_action_key": observed.node_key,
                        "output_digest": digest_bytes(canonical_bytes(envelope)),
                    },
                )
                evidence = (
                    TypedEvidence._capture(
                        kind="candidate",
                        subject=candidate_sha,
                        observer_type="runtime_adapter",
                        observer_id=observed.runtime_id,
                        observed_at=_now(),
                        source_ref=(
                            f"paseo://agent/{binding.agent_id}/"
                            f"result/{observed.node_key}"
                        ),
                        payload={
                            "workspace": observed.workspace,
                            "head": workspace_head,
                            "tree_sha": workspace_tree,
                        },
                    ),
                )
                prompt = self._prompts.get(observed.admission_id)
                try:
                    node = _contract_node_from_prompt(prompt)
                except json.JSONDecodeError as error:
                    raise RuntimeAdapterError(
                        "PROMPT_SNAPSHOT_INVALID",
                        "frozen Paseo Prompt is not valid JSON",
                    ) from error
                output_contract = (
                    node.get("output_contract") if isinstance(node, dict) else None
                )
                checks = (
                    output_contract.get("checks")
                    if isinstance(output_contract, dict)
                    else ()
                )
                evidence = self._capture_declared_checks(
                    observed,
                    candidate_sha,
                    evidence,
                    checks,
                    defer_repository=(
                        binding.agent_id in self._deferred_repository_checks
                    ),
                )
        runtime_observation = RuntimeObservation(
            binding=observed,
            lifecycle=agent.lifecycle,
            result_claim=result_claim,
            evidence=evidence,
        )
        if output_identity is not None and result_claim is not None:
            self._worker_observations[binding.agent_id] = (
                output_identity,
                runtime_observation,
            )
        return runtime_observation

    def repair(self, binding: RuntimeBinding, prompt: RuntimePrompt) -> None:
        if binding.agent_id is None or binding.attempt_id is None:
            raise RuntimeAdapterError(
                "REPAIR_BINDING_INVALID",
                "Repair requires one Prompt-bound Attempt identity",
            )
        prior_output = self.client.read_output(binding.agent_id)
        self.client.send_prompt(
            binding.agent_id,
            prompt,
            action_key=f"{binding.attempt_id}:repair:{prompt.digest}",
        )
        if binding.prompt_digest is not None:
            self.client.update_labels(
                binding.agent_id,
                {"gwo.prompt_digest": binding.prompt_digest},
            )
        self._worker_observations.pop(binding.agent_id, None)
        if (
            prior_output is not None
            and self.client.read_output(binding.agent_id) == prior_output
        ):
            raise RuntimeAdapterError(
                "REPAIR_PROMPT_NOT_ACCEPTED",
                "Runtime did not move beyond the previous bounded output",
                failure_class="ambiguous",
            )

    @staticmethod
    def _assert_review_workspace(request: ReviewAxisRequest) -> None:
        workspace = Path(request.workspace).resolve()
        head = _git(workspace, "rev-parse", "HEAD")
        status = _git(
            workspace,
            "status",
            "--porcelain=v1",
            "--untracked-files=all",
        )
        if head != request.candidate_sha or status:
            raise RuntimeAdapterError(
                "REVIEW_CANDIDATE_NOT_CLEAN",
                "Review requires a clean exact-Candidate workspace",
                failure_class="ambiguous",
            )

    @staticmethod
    def _review_binding(
        agent: PaseoAgentRecord,
        request: ReviewAxisRequest,
        profile: RuntimeProfile,
        prompt: RuntimePrompt,
        parent_agent_id: str | None,
    ) -> ReviewAxisBinding:
        labels = agent.labels
        if (
            labels.get("gwo.action_key") != request.action_key
            or labels.get("gwo.review_attempt") != request.attempt_id
            or labels.get("gwo.review_candidate") != request.candidate_sha
            or labels.get("gwo.review_axis") != request.axis
            or labels.get("gwo.review_recovery") != str(request.recovery_ordinal)
            or labels.get("gwo.review_input") != request.fixed_input_digest
            or labels.get("gwo.prompt_digest") != prompt.digest
            or labels.get("gwo.runtime_profile") != profile.name
            or labels.get("gwo.profile_digest") != profile.digest
            or agent.parent_agent_id != parent_agent_id
            or agent.profile_digest != profile.digest
            or agent.provider != profile.provider
            or agent.model != profile.model
            or agent.thinking != profile.thinking
            or agent.mode != profile.mode
            or digest_value(agent.features) != digest_value(profile.features)
        ):
            raise RuntimeAdapterError(
                "REVIEW_AXIS_IDENTITY_MISMATCH",
                "Review child readback does not match request and profile",
                failure_class="ambiguous",
            )
        if not all(
            isinstance(value, str) and value
            for value in (
                agent.agent_id,
                agent.session_id,
                agent.workspace_id,
                agent.workspace,
            )
        ):
            raise RuntimeAdapterError(
                "REVIEW_AXIS_IDENTITY_MISSING",
                "Review child has no complete Runtime identity",
                failure_class="ambiguous",
            )
        return ReviewAxisBinding(
            action_key=request.action_key,
            axis=request.axis,
            candidate_sha=request.candidate_sha,
            fixed_input_digest=request.fixed_input_digest,
            runtime_id=agent.agent_id,
            agent_id=agent.agent_id,
            session_id=agent.session_id,
            workspace_id=agent.workspace_id,
            workspace=agent.workspace,
            parent_agent_id=agent.parent_agent_id,
            runtime_profile=profile.name,
            profile_digest=profile.digest,
            provider=profile.provider,
            model=profile.model,
            thinking=profile.thinking,
            mode=profile.mode,
            prompt_digest=prompt.digest,
        )

    def materialize_review_axis(
        self,
        request: ReviewAxisRequest,
        profile: RuntimeProfile,
        *,
        parent_agent_id: str | None,
    ) -> ReviewAxisBinding:
        """Create or adopt one read-backed Review child with safe bounded retry."""

        self._assert_review_workspace(request)
        prompt = request.to_prompt()
        labels = {
            "gwo.action_key": request.action_key,
            "gwo.repository": request.repository,
            "gwo.review_attempt": request.attempt_id,
            "gwo.review_candidate": request.candidate_sha,
            "gwo.review_axis": request.axis,
            "gwo.review_recovery": str(request.recovery_ordinal),
            "gwo.review_input": request.fixed_input_digest,
            "gwo.runtime_profile": profile.name,
            "gwo.profile_digest": profile.digest,
        }
        if parent_agent_id is not None:
            labels["gwo.parent_agent"] = parent_agent_id
        last_error: RuntimeAdapterError | None = None
        for execution in range(3):
            try:
                agent = self._find_one({"gwo.action_key": request.action_key})
                if agent is None:
                    creation_labels = dict(labels)
                    if (
                        len(prompt.text.encode("utf-8"))
                        <= PASEO_INLINE_PROMPT_MAX_BYTES
                    ):
                        creation_labels["gwo.prompt_delivery"] = (
                            self._delivery_label_value(
                                "acked",
                                0,
                                request.action_key,
                            )
                        )
                    self.client.create(
                        PaseoCreateRequest(
                            action_key=request.action_key,
                            title=(
                                f"GWO Review {request.axis} "
                                f"{request.candidate_sha[:12]}"
                            ),
                            labels=creation_labels,
                            prompt=prompt,
                            repository_path=str(Path(request.workspace).resolve()),
                            base_sha=request.candidate_sha,
                            profile=profile,
                            parent_agent_id=parent_agent_id,
                        )
                    )
                agent = self._find_one(labels)
                if agent is None:
                    raise RuntimeAdapterError(
                        "REVIEW_AXIS_READBACK_MISSING",
                        "Review child creation has no exact identity readback",
                        failure_class="ambiguous",
                    )
                self._converge_prompt_delivery(
                    agent.agent_id,
                    prompt,
                    identity_labels={"gwo.action_key": request.action_key},
                    agent_labels=labels,
                    action_key=request.action_key,
                )
                readback = self._find_exact(
                    agent.agent_id,
                    {
                        **labels,
                        "gwo.prompt_digest": prompt.digest,
                    },
                    code="REVIEW_AXIS_READBACK_MISSING",
                )
                return self._review_binding(
                    readback,
                    request,
                    profile,
                    prompt,
                    parent_agent_id,
                )
            except RuntimeAdapterError as error:
                last_error = error
                if error.failure_class == "permanent":
                    raise
                if error.code == "PROMPT_DELIVERY_AMBIGUOUS":
                    # Paseo acknowledged an asynchronous send, so another
                    # convergence window can only read back the same Agent and
                    # action. Empty windows are not failed executions and must
                    # never turn this ambiguity into finite retry exhaustion.
                    raise
                if execution == 2:
                    break
        assert last_error is not None
        raise RuntimeAdapterError(
            "REVIEW_AXIS_MATERIALIZATION_RETRIES_EXHAUSTED",
            "review child initial execution plus two transport retries failed",
            failure_class=last_error.failure_class,
        ) from last_error

    def observe_review_axis(
        self,
        request: ReviewAxisRequest,
        binding: ReviewAxisBinding,
    ) -> ReviewAxisObservation:
        """Capture one typed axis record directly from the child output."""

        self._assert_review_workspace(request)
        if (
            binding.action_key != request.action_key
            or binding.axis != request.axis
            or binding.candidate_sha != request.candidate_sha
            or binding.fixed_input_digest != request.fixed_input_digest
        ):
            raise RuntimeAdapterError(
                "REVIEW_AXIS_IDENTITY_MISMATCH",
                "Review axis Binding does not match the fixed request",
                failure_class="ambiguous",
            )
        labels = {
            "gwo.action_key": request.action_key,
            "gwo.repository": request.repository,
            "gwo.review_attempt": request.attempt_id,
            "gwo.review_candidate": request.candidate_sha,
            "gwo.review_axis": request.axis,
            "gwo.review_recovery": str(request.recovery_ordinal),
            "gwo.review_input": request.fixed_input_digest,
            "gwo.runtime_profile": binding.runtime_profile,
            "gwo.profile_digest": binding.profile_digest,
            "gwo.prompt_digest": binding.prompt_digest,
        }
        if binding.parent_agent_id is not None:
            labels["gwo.parent_agent"] = binding.parent_agent_id
        agent = self._find_exact(
            binding.agent_id,
            labels,
            code="REVIEW_AXIS_IDENTITY_MISMATCH",
        )
        prompt = request.to_prompt()
        if (
            binding.prompt_digest != prompt.digest
            or not self._prompt_is_accepted(
                binding.agent_id,
                prompt,
                {"gwo.action_key": request.action_key},
            )
        ):
            raise RuntimeAdapterError(
                "REVIEW_AXIS_PROMPT_IDENTITY_MISMATCH",
                "Review child no longer has one exact accepted Prompt",
                failure_class="ambiguous",
            )
        if (
            agent.agent_id != binding.agent_id
            or agent.session_id != binding.session_id
            or agent.profile_digest != binding.profile_digest
            or agent.provider != binding.provider
            or agent.model != binding.model
            or agent.thinking != binding.thinking
            or agent.mode != binding.mode
        ):
            raise RuntimeAdapterError(
                "REVIEW_AXIS_IDENTITY_MISMATCH",
                "Review child Runtime identity changed",
                failure_class="ambiguous",
            )
        output = self.client.read_output(binding.agent_id)
        envelope = read_bounded_outcome(
            output,
            marker="GWO_REVIEW_AXIS",
            action_key=request.action_key,
        )
        if envelope is None:
            if agent.lifecycle in {"running", "queued", "active"}:
                return ReviewAxisObservation(
                    lifecycle="running",
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
                    output_digest=None,
                )
            raise RuntimeAdapterError(
                "REVIEW_AXIS_OUTPUT_MISSING",
                "terminal Review child returned no typed axis observation",
            )
        expected_fields = {
            "schema_version",
            "action_key",
            "candidate_sha",
            "axis",
            "fixed_input_digest",
            "findings",
        }
        findings = envelope.get("findings")
        if (
            set(envelope) != expected_fields
            or envelope.get("candidate_sha") != request.candidate_sha
            or envelope.get("axis") != request.axis
            or envelope.get("fixed_input_digest") != request.fixed_input_digest
            or not isinstance(findings, list)
        ):
            raise RuntimeAdapterError(
                "REVIEW_AXIS_OUTPUT_INVALID",
                "Review child returned an invalid typed axis envelope",
            )
        finding_fields = {
            "severity",
            "code",
            "source",
            "location",
            "message",
        }
        normalized: list[dict[str, str]] = []
        for finding in findings:
            if (
                not isinstance(finding, dict)
                or set(finding) != finding_fields
                or finding.get("severity") not in {"hard", "advisory"}
                or any(
                    not isinstance(finding.get(field), str) or not finding[field]
                    for field in finding_fields - {"severity"}
                )
            ):
                raise RuntimeAdapterError(
                    "REVIEW_AXIS_OUTPUT_INVALID",
                    "Review child returned an invalid typed finding",
                )
            normalized.append(
                {field: str(finding[field]) for field in sorted(finding_fields)}
            )
        self._assert_review_workspace(request)
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
            output_digest=digest_value(envelope),
            findings=tuple(normalized),
        )

    def retire_review_axis(self, binding: ReviewAxisBinding) -> None:
        self.client.archive(binding.agent_id)
        if not self.client.inspect(binding.agent_id).archived:
            raise RuntimeAdapterError(
                "REVIEW_AXIS_RETIRE_READBACK_FAILED",
                "Review child did not read back retired",
                failure_class="ambiguous",
            )

    def interrupt(self, binding: RuntimeBinding) -> None:
        if binding.agent_id is None:
            raise RuntimeAdapterError(
                "RUNTIME_BINDING_UNKNOWN",
                "Paseo Binding has no Agent identity",
                failure_class="ambiguous",
            )
        self.client.stop(binding.agent_id)
        if self.client.inspect(binding.agent_id).lifecycle not in {"idle", "completed"}:
            raise RuntimeAdapterError(
                "RUNTIME_INTERRUPT_READBACK_FAILED",
                "Paseo Agent remains active after interrupt",
                failure_class="ambiguous",
            )

    def retire(self, binding: RuntimeBinding) -> None:
        if binding.agent_id is None:
            raise RuntimeAdapterError(
                "RUNTIME_BINDING_UNKNOWN",
                "Paseo Binding has no Agent identity",
                failure_class="ambiguous",
            )
        self.client.archive(binding.agent_id)
        if not self.client.inspect(binding.agent_id).archived:
            raise RuntimeAdapterError(
                "RUNTIME_RETIRE_READBACK_FAILED",
                "Paseo Agent did not read back retired",
                failure_class="ambiguous",
            )


class InMemoryRuntimeAdapter:
    """Deterministic contract fake; it is not a universal production Adapter."""

    adapter_name = "in-memory"

    def __init__(self, workspace_root: Path):
        self.workspace_root = Path(workspace_root)
        self.workspace_root.mkdir(parents=True, exist_ok=True)
        self._states: dict[str, _RuntimeState] = {}

    def materialize(
        self,
        admission: RuntimeAdmission,
        prompt: RuntimePrompt | None = None,
    ) -> RuntimeBinding:
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
            agent_id=runtime_id,
            session_id=f"session:{runtime_id}",
            workspace_id=f"workspace:{runtime_id}",
            parent_agent_id=admission.parent_agent_id,
            runtime_profile=(
                None
                if admission.runtime_profile is None
                else admission.runtime_profile.name
            ),
            profile_digest=(
                None
                if admission.runtime_profile is None
                else admission.runtime_profile.digest
            ),
            provider=(
                "in-memory"
                if admission.runtime_profile is None
                else admission.runtime_profile.provider
            ),
            model=(
                "deterministic"
                if admission.runtime_profile is None
                else admission.runtime_profile.model
            ),
            thinking=(
                None
                if admission.runtime_profile is None
                else admission.runtime_profile.thinking
            ),
            mode=(
                None
                if admission.runtime_profile is None
                else admission.runtime_profile.mode
            ),
            features_digest=(
                None
                if admission.runtime_profile is None
                else digest_value(admission.runtime_profile.features)
            ),
            base_sha=admission.base_sha,
        )
        self._states[admission.admission_id] = _RuntimeState(binding=binding)
        if prompt is not None:
            self.accept_prompt(binding, prompt)
        return self._states[admission.admission_id].binding

    def read_binding(
        self,
        admission: RuntimeAdmission | str,
        prompt: RuntimePrompt | None = None,
    ) -> RuntimeBinding | None:
        del prompt
        admission_id = (
            admission if isinstance(admission, str) else admission.admission_id
        )
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
            raise RuntimeAdapterError(
                "PROMPT_INVALID", "Prompt is not readable"
            ) from error
        node = payload.get("node") if isinstance(payload, dict) else None
        if not isinstance(node, dict) or node.get("node_key") != binding.node_key:
            raise RuntimeAdapterError(
                "PROMPT_IDENTITY_MISMATCH", "Prompt does not describe this Plan Node"
            )
        state.prompt = prompt
        state.node = (
            prompt.contract_node
            if isinstance(prompt.contract_node, dict)
            else node
        )
        state.binding = replace(
            binding,
            prompt_accepted=True,
            prompt_digest=prompt.digest,
        )

    def attach_attempt(
        self,
        binding: RuntimeBinding,
        attempt_id: str,
    ) -> RuntimeBinding:
        state = self._state_for(binding)
        if not binding.prompt_accepted:
            raise RuntimeAdapterError(
                "PROMPT_NOT_ACCEPTED", "Attempt cannot bind before Prompt readback"
            )
        if not isinstance(attempt_id, str) or not attempt_id:
            raise RuntimeAdapterError("ATTEMPT_ID_INVALID", "Attempt ID is required")
        state.binding = replace(binding, attempt_id=attempt_id)
        return state.binding

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

    def defer_repository_checks(self, binding: RuntimeBinding) -> None:
        self._state_for(binding)

    def capture_deferred_checks(
        self,
        binding: RuntimeBinding,
        observation: RuntimeObservation,
    ) -> RuntimeObservation:
        self._state_for(binding)
        return observation

    def repair(self, binding: RuntimeBinding, prompt: RuntimePrompt) -> None:
        state = self._state_for(binding)
        state.prompt = prompt
        state.result_claim = None
        state.evidence = ()

    def interrupt(self, binding: RuntimeBinding) -> None:
        self._state_for(binding)

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
            if check.get("hosted_only") is True:
                continue
            command = [str(part) for part in check["command"]]
            result = _run(command, cwd=workspace)
            log = f"{result.stdout}\n{result.stderr}".encode("utf-8")
            environment_requirements = tuple(
                str(item) for item in check.get("environment_requirements") or ()
            )
            environment = _environment_snapshot(
                environment_requirements,
                cwd=workspace,
            )
            base_tree = None
            if check.get("base_sensitive") is True:
                if (
                    not isinstance(binding.base_sha, str)
                    or re.fullmatch(r"[0-9a-f]{40}", binding.base_sha) is None
                ):
                    raise RuntimeAdapterError(
                        "CHECK_BASE_PROVENANCE_MISSING",
                        "base-sensitive check lacks the admitted base SHA",
                    )
                base_tree = _git(
                    workspace,
                    "rev-parse",
                    f"{binding.base_sha}^{{tree}}",
                )
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
                        "definition_digest": check.get("definition_digest"),
                        "command_digest": digest_value(command),
                        "observed_tree_digest": tree_sha,
                        "input_projection_digest": _input_projection_digest(
                            workspace,
                            candidate_sha,
                            tuple(check.get("input_selector") or ()),
                        ),
                        "outcome": "passed" if result.returncode == 0 else "failed",
                        "exit_code": result.returncode,
                        "environment_requirements": list(environment_requirements),
                        "environment_identity": environment,
                        "environment_digest": digest_value(environment),
                        **(
                            {
                                "base_sha": binding.base_sha,
                                "observed_base_tree_digest": base_tree,
                            }
                            if check.get("base_sensitive") is True
                            else {}
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
                    check.get("check_id") for check in checks if isinstance(check, dict)
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
