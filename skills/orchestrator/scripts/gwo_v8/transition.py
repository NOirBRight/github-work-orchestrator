"""Canary acceptance and single-writer V6.1-to-V8 transition protocol."""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sqlite3
from typing import Any, Callable, Protocol

from ._canonical import canonical_bytes, digest_bytes, digest_value, load_canonical_json
from .activation import GitHubContentClient, LocalPlanPublication
from .compiler import CompiledPlan
from .evidence import TypedEvidence
from .kernel import Kernel


REQUIRED_CANARY_COVERAGE = frozenset(
    {
        "contract_activation",
        "prompt_acceptance",
        "local_first_publication",
        "dual_axis_review",
        "hosted_code_failure",
        "hosted_infrastructure_failure",
        "recovery",
        "ci_parking",
        "capacity_refill",
        "parallel_8_1",
        "conflict_exclusion",
        "serial_integration",
        "rollback",
    }
)


# This private ledger is deliberately read by the production Writer transition
# owner as part of its exact control-ref CAS.  It is not PlanControl state: it
# merely fences the narrow interval between a Gateway's provider dispatch
# admission and the adapter's durable readback.
_PLANNING_EFFECT_DISPATCH_PATH = ".gwo-v8/planning-effect-dispatch-v1.json"
_PLANNING_EFFECT_DISPATCH_SCHEMA = "gwo.planning-effect-dispatch.v1"
_PLANNING_EFFECT_DISPATCH_FIELDS = {
    "repository",
    "campaign_key",
    "campaign_handle",
    "subject_digest",
    "stable_action_id",
    "effect_boundary",
    "writer_generation",
    "writer_cut_over_record_id",
    "writer_observation_ref",
    "ticket",
    "attempt",
    "state",
}
_PLANNING_EFFECT_DISPATCH_MAX_CANONICAL_BYTES = 12_288
_PLANNING_EFFECT_DISPATCH_MAX_ENTRIES = 16
_PLANNING_EFFECT_DISPATCH_MAX_ACTIVE_ENTRIES = 8
_PLANNING_EFFECT_DISPATCH_MAX_TEXT_BYTES = 256
_PLANNING_EFFECT_DISPATCH_MAX_ATTEMPT = 16
_PLANNING_EFFECT_DISPATCH_TICKET_FIELDS = tuple(
    sorted(_PLANNING_EFFECT_DISPATCH_FIELDS - {"ticket", "state"})
)


class WriterTransitionBlocked(RuntimeError):
    """A typed retry outcome from the durable Writer transition owner."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class CanaryRunReadback:
    repository: str
    node_keys: tuple[str, ...]
    hosted_ci_seconds: int
    coverage: frozenset[str]
    scenario_evidence: dict[str, TypedEvidence]
    candidate_evidence: dict[str, TypedEvidence]
    review_evidence: dict[str, TypedEvidence]
    managed_reviewer_identities: tuple[str, ...]


@dataclass(frozen=True)
class CanaryAcceptance:
    accepted: bool
    repository: str
    evidence_package_digest: str | None
    manifest_ref: str | None
    blockers: tuple[str, ...]
    evidence_refs: tuple[str, ...]


class CanaryEvidenceControl(Protocol):
    def read(self, source_ref: str) -> TypedEvidence | None: ...

    def publish_manifest(
        self,
        repository: str,
        package_digest: str,
        content: bytes,
    ) -> str: ...

    def read_manifest(self, manifest_ref: str) -> bytes | None: ...


class InMemoryCanaryEvidenceControl:
    def __init__(self, evidence: tuple[TypedEvidence, ...]):
        self._evidence = {item.source_ref: item for item in evidence}
        self._manifests: dict[str, bytes] = {}

    def read(self, source_ref: str) -> TypedEvidence | None:
        return self._evidence.get(source_ref)

    def publish_manifest(
        self,
        repository: str,
        package_digest: str,
        content: bytes,
    ) -> str:
        manifest_ref = (
            f"github://{repository}/gwo-v8/canary/{package_digest}.json"
        )
        existing = self._manifests.get(manifest_ref)
        if existing is not None and existing != content:
            raise ValueError("Canary manifest identity is immutable")
        self._manifests[manifest_ref] = content
        return manifest_ref

    def read_manifest(self, manifest_ref: str) -> bytes | None:
        return self._manifests.get(manifest_ref)


class GitHubCanaryEvidenceControl:
    """Read exact typed Evidence from configured immutable GitHub paths."""

    def __init__(
        self,
        client: GitHubContentClient,
        locations: dict[str, tuple[str, str, str]],
        *,
        manifest_repository: str,
        manifest_branch: str,
    ):
        self.client = client
        self.locations = dict(locations)
        self.manifest_repository = manifest_repository
        self.manifest_branch = manifest_branch

    def read(self, source_ref: str) -> TypedEvidence | None:
        location = self.locations.get(source_ref)
        if location is None:
            return None
        content = self.client.read(*location)
        if content is None:
            return None
        try:
            value = json.loads(content.content)
            return TypedEvidence(**value)
        except (json.JSONDecodeError, UnicodeDecodeError, TypeError):
            return None

    def publish_manifest(
        self,
        repository: str,
        package_digest: str,
        content: bytes,
    ) -> str:
        if repository != self.manifest_repository:
            raise ValueError("Canary manifest repository changed")
        path = f".gwo-v8/canary/{package_digest}.json"
        manifest_ref = f"github://canary-manifest/{package_digest}"
        current = self.client.read(repository, self.manifest_branch, path)
        if current is None:
            written = self.client.compare_and_swap(
                repository,
                self.manifest_branch,
                path,
                content,
                expected_blob_sha=None,
                message=f"GWO V8 canary manifest {package_digest}",
            )
            if written.content != content:
                raise ValueError("Canary manifest write did not read back")
        elif current.content != content:
            raise ValueError("Canary manifest identity is immutable")
        return manifest_ref

    def read_manifest(self, manifest_ref: str) -> bytes | None:
        prefix = "github://canary-manifest/"
        if not manifest_ref.startswith(prefix):
            return None
        package_digest = manifest_ref.removeprefix(prefix)
        if re.fullmatch(r"[0-9a-f]{64}", package_digest) is None:
            return None
        path = f".gwo-v8/canary/{package_digest}.json"
        content = self.client.read(
            self.manifest_repository,
            self.manifest_branch,
            path,
        )
        return None if content is None else content.content


class CanaryRunVerifier:
    """Accept only a complete, bounded external canary readback package."""

    def __init__(self, evidence_control: CanaryEvidenceControl):
        self.evidence_control = evidence_control

    def verify(self, readback: CanaryRunReadback) -> CanaryAcceptance:
        blockers: set[str] = set()
        node_keys = set(readback.node_keys)
        if len(readback.node_keys) not in {3, 4, 5} or len(node_keys) != len(
            readback.node_keys
        ):
            blockers.add("CANARY_NODE_SET_INVALID")
        if readback.hosted_ci_seconds < 1 or readback.hosted_ci_seconds > 300:
            blockers.add("CANARY_HOSTED_CI_OUT_OF_BOUNDS")
        if not REQUIRED_CANARY_COVERAGE.issubset(readback.coverage):
            blockers.add("CANARY_COVERAGE_INCOMPLETE")
        if set(readback.scenario_evidence) != set(REQUIRED_CANARY_COVERAGE):
            blockers.add("CANARY_SCENARIO_EVIDENCE_INCOMPLETE")
        for scenario, evidence in readback.scenario_evidence.items():
            if (
                not evidence.has_valid_digest()
                or evidence.kind != "canary"
                or evidence.subject != readback.repository
                or evidence.observer_type not in {"github", "kernel"}
                or evidence.payload.get("scenario") != scenario
                or evidence.payload.get("outcome") != "passed"
                or not self._durable_readback(evidence)
            ):
                blockers.add("CANARY_SCENARIO_EVIDENCE_INVALID")
        if set(readback.candidate_evidence) != node_keys:
            blockers.add("CANARY_CANDIDATE_EVIDENCE_INCOMPLETE")
        for node_key, evidence in readback.candidate_evidence.items():
            if (
                not evidence.has_valid_digest()
                or evidence.kind != "candidate"
                or re.fullmatch(r"[0-9a-f]{40}", evidence.subject) is None
                or evidence.payload.get("node_key") != node_key
                or not self._durable_readback(evidence)
            ):
                blockers.add("CANARY_CANDIDATE_EVIDENCE_INVALID")
        if set(readback.review_evidence) != node_keys:
            blockers.add("CANARY_REVIEW_EVIDENCE_INCOMPLETE")
        for node_key, evidence in readback.review_evidence.items():
            candidate = readback.candidate_evidence.get(node_key)
            axes = evidence.payload.get("axes")
            if (
                candidate is None
                or not evidence.has_valid_digest()
                or evidence.kind != "review"
                or evidence.subject != candidate.subject
                or evidence.payload.get("record_type") != "envelope"
                or not isinstance(evidence.payload.get("attempt_id"), str)
                or re.fullmatch(
                    r"[0-9a-f]{64}",
                    str(evidence.payload.get("acceptance_digest") or ""),
                )
                is None
                or re.fullmatch(
                    r"[0-9a-f]{64}",
                    str(evidence.payload.get("check_manifest_digest") or ""),
                )
                is None
                or not isinstance(axes, list)
                or [item.get("axis") for item in axes if isinstance(item, dict)]
                != ["standards", "spec"]
                or any(
                    not item.get("action_key")
                    or re.fullmatch(
                        r"[0-9a-f]{64}",
                        str(item.get("fixed_input_digest") or ""),
                    )
                    is None
                    or re.fullmatch(
                        r"[0-9a-f]{64}",
                        str(item.get("output_digest") or ""),
                    )
                    is None
                    or not isinstance(item.get("runtime"), dict)
                    or not item["runtime"].get("runtime_id")
                    or not item["runtime"].get("provider")
                    or not item["runtime"].get("model")
                    for item in axes
                    if isinstance(item, dict)
                )
                or any(
                    finding.get("severity") == "hard"
                    for item in axes
                    if isinstance(item, dict)
                    for finding in item.get("findings") or ()
                    if isinstance(finding, dict)
                )
                or not self._durable_readback(evidence)
            ):
                blockers.add("CANARY_REVIEW_EVIDENCE_INVALID")
        if readback.managed_reviewer_identities:
            blockers.add("CANARY_MANAGED_REVIEWER_PRESENT")
        all_evidence = (
            *readback.scenario_evidence.values(),
            *readback.candidate_evidence.values(),
            *readback.review_evidence.values(),
        )
        evidence_refs = tuple(
            sorted({evidence.source_ref for evidence in all_evidence})
        )
        package = {
            "repository": readback.repository,
            "node_keys": list(readback.node_keys),
            "hosted_ci_seconds": readback.hosted_ci_seconds,
            "coverage": sorted(readback.coverage),
            "scenario_evidence": {
                key: asdict(value)
                for key, value in readback.scenario_evidence.items()
            },
            "candidate_evidence": {
                key: asdict(value)
                for key, value in readback.candidate_evidence.items()
            },
            "review_evidence": {
                key: asdict(value)
                for key, value in readback.review_evidence.items()
            },
            "evidence_refs": list(evidence_refs),
        }
        package_bytes = canonical_bytes(package)
        package_digest = digest_bytes(package_bytes)
        manifest_ref = None
        if not blockers:
            try:
                manifest_ref = self.evidence_control.publish_manifest(
                    readback.repository,
                    package_digest,
                    package_bytes,
                )
                if (
                    self.evidence_control.read_manifest(manifest_ref)
                    != package_bytes
                ):
                    blockers.add("CANARY_MANIFEST_READBACK_FAILED")
            except ValueError:
                blockers.add("CANARY_MANIFEST_PUBLISH_FAILED")
        ordered = tuple(sorted(blockers))
        return CanaryAcceptance(
            accepted=not ordered,
            repository=readback.repository,
            evidence_package_digest=(
                None if ordered else package_digest
            ),
            manifest_ref=None if ordered else manifest_ref,
            blockers=ordered,
            evidence_refs=evidence_refs,
        )

    def _durable_readback(self, evidence: TypedEvidence) -> bool:
        return (
            evidence.source_ref.startswith(("github://", "git://"))
            and self.evidence_control.read(evidence.source_ref) == evidence
        )


@dataclass(frozen=True)
class LegacyWriterReadback:
    repository: str
    stopped: bool
    active_dispatches: tuple[str, ...]
    integration_lease: bool
    active_workers: tuple[str, ...]


class LegacyWriterControl(Protocol):
    def stop(self, repository: str, *, action_key: str) -> None: ...

    def restore(self, repository: str, *, action_key: str) -> None: ...

    def readback(self, repository: str) -> LegacyWriterReadback: ...


class InMemoryLegacyWriterControl:
    def __init__(
        self,
        *,
        active_dispatches: tuple[str, ...] = (),
        integration_lease: bool = False,
        active_workers: tuple[str, ...] = (),
    ):
        self.active_dispatches = active_dispatches
        self.integration_lease = integration_lease
        self.active_workers = active_workers
        self._stopped: set[str] = set()
        self._stop_actions: set[str] = set()

    def stop(self, repository: str, *, action_key: str) -> None:
        self._stop_actions.add(action_key)
        self._stopped.add(repository)

    def restore(self, repository: str, *, action_key: str) -> None:
        self._stop_actions.add(action_key)
        self._stopped.discard(repository)

    def readback(self, repository: str) -> LegacyWriterReadback:
        return LegacyWriterReadback(
            repository=repository,
            stopped=repository in self._stopped,
            active_dispatches=self.active_dispatches,
            integration_lease=self.integration_lease,
            active_workers=self.active_workers,
        )


class GitHubLegacyWriterControl:
    """Durable V6.1 fence combined with authoritative execution readback."""

    _PATH = ".gwo-v8/legacy-writer-fence.json"

    def __init__(
        self,
        client: GitHubContentClient,
        *,
        branch: str,
        execution_readback: Callable[[str], LegacyWriterReadback],
    ):
        if not branch:
            raise ValueError("legacy writer fence branch is required")
        self.client = client
        self.branch = branch
        self.execution_readback = execution_readback

    def _read_fence(
        self,
        repository: str,
    ) -> tuple[dict | None, str | None]:
        content = self.client.read(repository, self.branch, self._PATH)
        if content is None:
            return None, None
        try:
            value = json.loads(content.content)
        except (json.JSONDecodeError, UnicodeDecodeError) as error:
            raise ValueError("durable legacy writer fence is invalid") from error
        if (
            not isinstance(value, dict)
            or value.get("schema_version") != 1
            or value.get("repository") != repository
            or not isinstance(value.get("stopped"), bool)
            or not isinstance(value.get("events"), list)
        ):
            raise ValueError("durable legacy writer fence is malformed")
        action_operations: dict[str, str] = {}
        for event in value["events"]:
            if (
                not isinstance(event, dict)
                or event.get("operation") not in {"stop", "restore"}
                or not isinstance(event.get("action_key"), str)
                or not event["action_key"]
            ):
                raise ValueError("durable legacy writer fence event is malformed")
            previous = action_operations.setdefault(
                event["action_key"],
                event["operation"],
            )
            if previous != event["operation"]:
                raise ValueError("legacy writer action key was reused")
        expected_stopped = bool(
            value["events"] and value["events"][-1]["operation"] == "stop"
        )
        if value["stopped"] != expected_stopped:
            raise ValueError("durable legacy writer fence state is contradictory")
        return value, content.blob_sha

    def _set(
        self,
        repository: str,
        *,
        action_key: str,
        operation: str,
    ) -> None:
        if not action_key:
            raise ValueError("legacy writer action key is required")
        value, blob_sha = self._read_fence(repository)
        if value is None:
            value = {
                "schema_version": 1,
                "repository": repository,
                "stopped": False,
                "events": [],
            }
        matching = [
            event
            for event in value["events"]
            if event["action_key"] == action_key
        ]
        if matching:
            if matching[0]["operation"] != operation:
                raise ValueError("legacy writer action key was reused")
            return
        target_stopped = operation == "stop"
        if value["stopped"] == target_stopped:
            return
        value["events"].append(
            {
                "action_key": action_key,
                "operation": operation,
            }
        )
        value["stopped"] = target_stopped
        rendered = json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        written = self.client.compare_and_swap(
            repository,
            self.branch,
            self._PATH,
            rendered,
            expected_blob_sha=blob_sha,
            message=f"GWO V6.1 writer {operation} {action_key}",
        )
        readback, _readback_sha = self._read_fence(repository)
        if (
            written.content != rendered
            or readback is None
            or readback["stopped"] != target_stopped
        ):
            raise ValueError("legacy writer fence did not read back")

    def stop(self, repository: str, *, action_key: str) -> None:
        self._set(repository, action_key=action_key, operation="stop")

    def restore(self, repository: str, *, action_key: str) -> None:
        self._set(repository, action_key=action_key, operation="restore")

    def readback(self, repository: str) -> LegacyWriterReadback:
        fence, _blob_sha = self._read_fence(repository)
        execution = self.execution_readback(repository)
        if execution.repository != repository:
            raise ValueError("legacy execution readback repository changed")
        return replace(
            execution,
            stopped=bool(fence and fence["stopped"]),
        )


@dataclass(frozen=True)
class V8OwnershipReadback:
    active_admissions: tuple[str, ...]
    active_attempts: tuple[str, ...]
    integration_lease: bool
    runtime_resources: tuple[str, ...]


class V8OwnershipControl(Protocol):
    def drain(self, repository: str, *, source_ref: str) -> None: ...

    def readback(self, repository: str) -> V8OwnershipReadback: ...


class InMemoryV8OwnershipControl:
    def __init__(
        self,
        readback: V8OwnershipReadback,
        *,
        auto_drain: bool = True,
    ):
        self.current = readback
        self.auto_drain = auto_drain
        self.reads = 0

    def drain(self, repository: str, *, source_ref: str) -> None:
        del repository, source_ref
        if self.auto_drain:
            self.current = V8OwnershipReadback(
                active_admissions=(),
                active_attempts=(),
                integration_lease=False,
                runtime_resources=(),
            )

    def readback(self, repository: str) -> V8OwnershipReadback:
        self.reads += 1
        return replace(self.current)


class StoreV8OwnershipControl:
    """Read native Store ownership plus Runtime resource readback."""

    def __init__(
        self,
        store_path: Path,
        runtime_resources: Callable[[str], tuple[str, ...]],
        drain_runtime_resources: Callable[[str], None],
    ):
        self.store_path = Path(store_path)
        self.runtime_resources = runtime_resources
        self.drain_runtime_resources = drain_runtime_resources

    def drain(self, repository: str, *, source_ref: str) -> None:
        self.drain_runtime_resources(repository)
        Kernel.drain_store_ownership(
            self.store_path,
            repository=repository,
            source_ref=source_ref,
        )

    def readback(self, repository: str) -> V8OwnershipReadback:
        with sqlite3.connect(self.store_path) as connection:
            admissions = tuple(
                str(row[0])
                for row in connection.execute(
                    """
                    SELECT admission_id FROM v8_admissions
                    WHERE repository = ?
                      AND state NOT IN ('consumed', 'abandoned')
                    ORDER BY admission_id
                    """,
                    (repository,),
                ).fetchall()
            )
            attempts = tuple(
                str(row[0])
                for row in connection.execute(
                    """
                    SELECT attempt_id FROM v8_attempts
                    WHERE repository = ?
                      AND state NOT IN ('verified', 'terminal')
                    ORDER BY attempt_id
                    """,
                    (repository,),
                ).fetchall()
            )
            lease = connection.execute(
                """
                SELECT 1 FROM v8_integration_leases WHERE repository = ?
                """,
                (repository,),
            ).fetchone()
        return V8OwnershipReadback(
            active_admissions=admissions,
            active_attempts=attempts,
            integration_lease=lease is not None,
            runtime_resources=self.runtime_resources(repository),
        )


@dataclass(frozen=True)
class WriterTransitionRecord:
    record_id: str
    repository: str
    kind: str
    status: str
    previous_writer_generation: str
    writer_generation: str
    activation_id: str | None
    plan_digest: str | None
    canary_evidence_digest: str | None
    canary_evidence_refs: tuple[str, ...]
    canary_manifest_ref: str | None
    worker_capacity: int
    coordinator_capacity: int
    reason: str | None
    created_at: str


@dataclass(frozen=True)
class CurrentWriter:
    repository: str
    writer_generation: str
    record_id: str


class WriterTransitionControl(Protocol):
    def read_current(self, repository: str) -> CurrentWriter: ...

    def publish(self, record: WriterTransitionRecord) -> None: ...

    def read(
        self,
        repository: str,
        record_id: str,
    ) -> WriterTransitionRecord | None: ...

    def history(self, repository: str) -> tuple[WriterTransitionRecord, ...]: ...

    def allows(
        self,
        repository: str,
        writer_generation: str,
        activation_id: str,
    ) -> bool: ...

    def allows_new_work(
        self,
        repository: str,
        writer_generation: str,
        activation_id: str,
    ) -> bool: ...

    def capacity_limits(
        self,
        repository: str,
        writer_generation: str,
        activation_id: str,
    ) -> tuple[int, int]: ...


class InMemoryWriterTransitionControl:
    """Append-only durable transition contract fake."""

    def __init__(self, *, initial_writer: str):
        self.initial_writer = initial_writer
        self._records: dict[tuple[str, str], WriterTransitionRecord] = {}
        self._history: dict[str, list[str]] = {}
        self._current: dict[str, CurrentWriter] = {}

    def read_current(self, repository: str) -> CurrentWriter:
        return self._current.get(
            repository,
            CurrentWriter(
                repository=repository,
                writer_generation=self.initial_writer,
                record_id="initial-writer",
            ),
        )

    def publish(self, record: WriterTransitionRecord) -> None:
        key = (record.repository, record.record_id)
        existing = self._records.get(key)
        if existing is not None and replace(
            existing,
            created_at=record.created_at,
        ) != record:
            raise ValueError("Writer Transition Records are immutable")
        if existing is not None:
            return
        current = self.read_current(record.repository)
        if current.writer_generation != record.previous_writer_generation:
            raise ValueError("writer generation changed before transition commit")
        self._records[key] = record
        self._history.setdefault(record.repository, []).append(record.record_id)
        if record.status in {"pending", "cut_over", "draining", "rolled_back"}:
            self._current[record.repository] = CurrentWriter(
                repository=record.repository,
                writer_generation=record.writer_generation,
                record_id=record.record_id,
            )

    def read(
        self,
        repository: str,
        record_id: str,
    ) -> WriterTransitionRecord | None:
        return self._records.get((repository, record_id))

    def history(self, repository: str) -> tuple[WriterTransitionRecord, ...]:
        return tuple(
            self._records[(repository, record_id)]
            for record_id in self._history.get(repository, ())
        )

    def allows(
        self,
        repository: str,
        writer_generation: str,
        activation_id: str,
    ) -> bool:
        current = self.read_current(repository)
        record = self.read(repository, current.record_id)
        return (
            current.writer_generation == writer_generation
            and record is not None
            and record.status in {"cut_over", "draining"}
            and record.activation_id == activation_id
        )

    def allows_new_work(
        self,
        repository: str,
        writer_generation: str,
        activation_id: str,
    ) -> bool:
        current = self.read_current(repository)
        record = self.read(repository, current.record_id)
        return (
            current.writer_generation == writer_generation
            and record is not None
            and record.status == "cut_over"
            and record.activation_id == activation_id
        )

    def capacity_limits(
        self,
        repository: str,
        writer_generation: str,
        activation_id: str,
    ) -> tuple[int, int]:
        if not self.allows_new_work(
            repository,
            writer_generation,
            activation_id,
        ):
            return 0, 0
        current = self.read_current(repository)
        record = self.read(repository, current.record_id)
        assert record is not None
        return record.worker_capacity, record.coordinator_capacity


def _planning_effect_dispatch_ticket(entry: Mapping[str, Any]) -> str:
    """Derive the opaque ticket from every immutable dispatch identity field."""

    identity = {
        name: entry[name] for name in _PLANNING_EFFECT_DISPATCH_TICKET_FIELDS
    }
    return "planning-dispatch:" + digest_value(identity)[:32]


def _planning_effect_dispatch_entry_order(entry: Mapping[str, Any]) -> tuple[Any, ...]:
    """Give compaction one stable identity order, independent of map order."""

    return tuple(entry[name] for name in sorted(_PLANNING_EFFECT_DISPATCH_FIELDS))


def _planning_effect_dispatch_ledger_bytes(
    repository: str,
    entries: list[dict[str, Any]] | tuple[dict[str, Any], ...],
) -> bytes:
    """Render one bounded canonical dispatch ledger before a ref CAS."""

    rendered = canonical_bytes(
        {
            "schema_version": _PLANNING_EFFECT_DISPATCH_SCHEMA,
            "repository": repository,
            "entries": list(entries),
        }
    )
    if len(rendered) > _PLANNING_EFFECT_DISPATCH_MAX_CANONICAL_BYTES:
        raise ValueError("control-ref dispatch ledger exceeds its canonical byte budget")
    return rendered


def _validate_planning_effect_dispatch_entries(
    repository: str,
    raw_entries: object,
) -> tuple[dict[str, Any], ...]:
    """Apply the shared closed dispatch-ledger policy before use or CAS."""

    if type(raw_entries) is not list:
        raise ValueError("control-ref dispatch entries are not one exact list")
    if len(raw_entries) > _PLANNING_EFFECT_DISPATCH_MAX_ENTRIES:
        raise ValueError("control-ref dispatch ledger exceeds its entry budget")
    entries: list[dict[str, Any]] = []
    keys: set[tuple[str, str, str, str]] = set()
    tickets: set[str] = set()
    active_entries = 0
    for raw in raw_entries:
        if type(raw) is not dict or set(raw) != _PLANNING_EFFECT_DISPATCH_FIELDS:
            raise ValueError("control-ref dispatch entry schema is invalid")
        if (
            raw["repository"] != repository
            or type(raw["subject_digest"]) is not str
            or len(raw["subject_digest"]) != 64
            or any(
                character not in "0123456789abcdef"
                for character in raw["subject_digest"]
            )
            or type(raw["effect_boundary"]) is not str
            or raw["effect_boundary"] not in {"prepare", "start"}
            or type(raw["state"]) is not str
            or raw["state"] not in {"active", "recovery"}
            or type(raw["attempt"]) is not int
            or isinstance(raw["attempt"], bool)
            or not 1 <= raw["attempt"] <= _PLANNING_EFFECT_DISPATCH_MAX_ATTEMPT
            or any(
                type(raw[name]) is not str
                or not raw[name]
                or len(raw[name].encode("utf-8"))
                > _PLANNING_EFFECT_DISPATCH_MAX_TEXT_BYTES
                for name in _PLANNING_EFFECT_DISPATCH_FIELDS - {"attempt"}
            )
            or raw["ticket"] != _planning_effect_dispatch_ticket(raw)
        ):
            raise ValueError("control-ref dispatch entry fields are invalid")
        key = (
            raw["writer_generation"],
            raw["writer_cut_over_record_id"],
            raw["stable_action_id"],
            raw["effect_boundary"],
        )
        if key in keys or raw["ticket"] in tickets:
            raise ValueError("control-ref dispatch entry identity is duplicated")
        keys.add(key)
        tickets.add(raw["ticket"])
        if raw["state"] == "active":
            active_entries += 1
        entries.append(dict(raw))
    if active_entries > _PLANNING_EFFECT_DISPATCH_MAX_ACTIVE_ENTRIES:
        raise ValueError("control-ref dispatch ledger exceeds its active-entry budget")
    _planning_effect_dispatch_ledger_bytes(repository, entries)
    return tuple(entries)


def _planning_effect_dispatch_entries_at_ref(
    client: object,
    repository: str,
    ref_digest: str,
) -> tuple[dict[str, Any], ...]:
    """Decode the one closed dispatch ledger visible at a control-ref OID."""

    reader = getattr(client, "read_at_ref", None)
    if not callable(reader):
        raise ValueError("control-ref dispatch read is unavailable")
    content = reader(repository, ref_digest, _PLANNING_EFFECT_DISPATCH_PATH)
    if content is None:
        return ()
    payload = getattr(content, "content", None)
    if type(payload) is not bytes:
        raise ValueError("control-ref dispatch ledger bytes are invalid")
    value = load_canonical_json(payload)
    if (
        type(value) is not dict
        or set(value) != {"schema_version", "repository", "entries"}
        or value["schema_version"] != _PLANNING_EFFECT_DISPATCH_SCHEMA
        or value["repository"] != repository
    ):
        raise ValueError("control-ref dispatch ledger schema is invalid")
    return _validate_planning_effect_dispatch_entries(repository, value["entries"])


def _writer_drain_dispatch_blocker(
    client: object,
    repository: str,
    ref_digest: str,
    *,
    writer_generation: str,
    cut_over_record_id: str,
) -> str | None:
    """Return the exact blocker before the Writer can append `draining`."""

    try:
        entries = _planning_effect_dispatch_entries_at_ref(
            client,
            repository,
            ref_digest,
        )
    except Exception:
        return "WRITER_DRAIN_DISPATCH_INVALID"
    if any(
        entry["state"] == "active"
        and entry["writer_generation"] == writer_generation
        and entry["writer_cut_over_record_id"] == cut_over_record_id
        for entry in entries
    ):
        return "WRITER_DRAIN_DISPATCH_ACTIVE"
    return None


class GitHubWriterTransitionControl:
    """Append-only writer transition record on a dedicated GitHub branch."""

    _PATH = ".gwo-v8/writer-transition.json"

    def __init__(
        self,
        client: GitHubContentClient,
        *,
        branch: str,
        initial_writer: str,
    ):
        self.client = client
        self.branch = branch
        self.initial_writer = initial_writer

    @property
    def _uses_ref_cas(self) -> bool:
        return all(
            callable(getattr(self.client, name, None))
            for name in ("read_ref", "read_at_ref", "compare_and_swap_ref")
        )

    def _read_control_at_ref(
        self,
        repository: str,
        ref_digest: str,
    ) -> dict[str, Any]:
        content = self.client.read_at_ref(repository, ref_digest, self._PATH)
        if content is None:
            return {
                "schema_version": 1,
                "current": {
                    "repository": repository,
                    "writer_generation": self.initial_writer,
                    "record_id": "initial-writer",
                },
                "records": [],
            }
        try:
            value = load_canonical_json(content.content)
        except Exception as error:
            raise ValueError("durable writer transition control is invalid") from error
        if (
            type(value) is not dict
            or value.get("schema_version") != 1
            or not isinstance(value.get("current"), dict)
            or not isinstance(value.get("records"), list)
        ):
            raise ValueError("durable writer transition control is malformed")
        return value

    def _read_control(
        self,
        repository: str,
    ) -> tuple[dict, str | None]:
        content = self.client.read(repository, self.branch, self._PATH)
        if content is None:
            return (
                {
                    "schema_version": 1,
                    "current": {
                        "repository": repository,
                        "writer_generation": self.initial_writer,
                        "record_id": "initial-writer",
                    },
                    "records": [],
                },
                None,
            )
        try:
            value = json.loads(content.content)
        except (json.JSONDecodeError, UnicodeDecodeError) as error:
            raise ValueError("durable writer transition control is invalid") from error
        if (
            not isinstance(value, dict)
            or value.get("schema_version") != 1
            or not isinstance(value.get("current"), dict)
            or not isinstance(value.get("records"), list)
        ):
            raise ValueError("durable writer transition control is malformed")
        return value, content.blob_sha

    @staticmethod
    def _record_from_dict(value: dict) -> WriterTransitionRecord:
        try:
            normalized = {
                **value,
                "canary_evidence_refs": tuple(
                    value.get("canary_evidence_refs") or ()
                ),
                "canary_manifest_ref": value.get("canary_manifest_ref"),
                "plan_digest": value.get("plan_digest"),
            }
            return WriterTransitionRecord(**normalized)
        except TypeError as error:
            raise ValueError("durable Writer Transition Record is invalid") from error

    def read_current(self, repository: str) -> CurrentWriter:
        value, _blob = self._read_control(repository)
        current = value["current"]
        if current.get("repository") != repository:
            raise ValueError("durable writer transition repository is invalid")
        return CurrentWriter(
            repository=str(current["repository"]),
            writer_generation=str(current["writer_generation"]),
            record_id=str(current["record_id"]),
        )

    def publish(self, record: WriterTransitionRecord) -> None:
        if self._uses_ref_cas:
            self._publish_at_ref(record)
            return
        value, blob_sha = self._read_control(record.repository)
        current = value["current"]
        existing = next(
            (
                self._record_from_dict(item)
                for item in value["records"]
                if item.get("record_id") == record.record_id
            ),
            None,
        )
        if existing is not None:
            if replace(existing, created_at=record.created_at) != record:
                raise ValueError("Writer Transition Records are immutable")
            return
        if current["writer_generation"] != record.previous_writer_generation:
            raise ValueError("writer generation changed before transition commit")
        value["records"].append(asdict(record))
        if record.status in {"pending", "cut_over", "draining", "rolled_back"}:
            value["current"] = {
                "repository": record.repository,
                "writer_generation": record.writer_generation,
                "record_id": record.record_id,
            }
        rendered = json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        written = self.client.compare_and_swap(
            record.repository,
            self.branch,
            self._PATH,
            rendered,
            expected_blob_sha=blob_sha,
            message=f"GWO V8 {record.kind} {record.record_id}",
        )
        if written.content != rendered or self.read(
            record.repository,
            record.record_id,
        ) != record:
            raise ValueError("Writer Transition Record did not read back")

    def _publish_at_ref(self, record: WriterTransitionRecord) -> None:
        ref_digest = self.client.read_ref(record.repository, self.branch)
        if type(ref_digest) is not str or not ref_digest:
            raise ValueError("durable writer transition ref is invalid")
        value = self._read_control_at_ref(record.repository, ref_digest)
        current = value["current"]
        existing = next(
            (
                self._record_from_dict(item)
                for item in value["records"]
                if item.get("record_id") == record.record_id
            ),
            None,
        )
        if existing is not None:
            if replace(existing, created_at=record.created_at) != record:
                raise ValueError("Writer Transition Records are immutable")
            return
        if current.get("writer_generation") != record.previous_writer_generation:
            raise ValueError("writer generation changed before transition commit")
        if record.status == "draining":
            blocker = _writer_drain_dispatch_blocker(
                self.client,
                record.repository,
                ref_digest,
                writer_generation=record.writer_generation,
                cut_over_record_id=str(current.get("record_id", "")),
            )
            if blocker is not None:
                raise WriterTransitionBlocked(blocker)
        value["records"].append(asdict(record))
        if record.status in {"pending", "cut_over", "draining", "rolled_back"}:
            value["current"] = {
                "repository": record.repository,
                "writer_generation": record.writer_generation,
                "record_id": record.record_id,
            }
        rendered = canonical_bytes(value)
        try:
            committed = self.client.compare_and_swap_ref(
                record.repository,
                self.branch,
                expected_ref_digest=ref_digest,
                changes={self._PATH: rendered},
                message=f"GWO V8 {record.kind} {record.record_id}",
            )
        except Exception as error:
            if record.status != "draining":
                raise
            # A dispatch CAS can win after this transition read the ref but
            # before its Writer CAS.  Re-read that exact newer state so this
            # loss has the same typed, durable outcome as an already-visible
            # active dispatch rather than an unclassified CAS failure.
            try:
                recovered_ref = self.client.read_ref(
                    record.repository,
                    self.branch,
                )
                recovered_value = self._read_control_at_ref(
                    record.repository,
                    recovered_ref,
                )
                recovered_existing = next(
                    (
                        self._record_from_dict(item)
                        for item in recovered_value["records"]
                        if item.get("record_id") == record.record_id
                    ),
                    None,
                )
                if recovered_existing is not None:
                    if (
                        replace(recovered_existing, created_at=record.created_at)
                        != record
                    ):
                        raise ValueError("Writer Transition Records are immutable")
                    return
                recovered_current = recovered_value["current"]
                blocker = _writer_drain_dispatch_blocker(
                    self.client,
                    record.repository,
                    recovered_ref,
                    writer_generation=record.writer_generation,
                    cut_over_record_id=str(
                        recovered_current.get("record_id", "")
                    ),
                )
            except Exception as recovery_error:
                raise WriterTransitionBlocked("WRITER_DRAIN_RETRY") from recovery_error
            if blocker is not None:
                raise WriterTransitionBlocked(blocker) from error
            raise WriterTransitionBlocked("WRITER_DRAIN_RETRY") from error
        if type(committed) is not str or not committed:
            raise ValueError("Writer Transition Record ref CAS did not commit")
        readback = self._read_control_at_ref(record.repository, committed)
        if not any(
            self._record_from_dict(item) == record
            for item in readback["records"]
        ):
            raise ValueError("Writer Transition Record did not read back")

    def read(
        self,
        repository: str,
        record_id: str,
    ) -> WriterTransitionRecord | None:
        value, _blob = self._read_control(repository)
        return next(
            (
                self._record_from_dict(item)
                for item in value["records"]
                if item.get("record_id") == record_id
            ),
            None,
        )

    def history(self, repository: str) -> tuple[WriterTransitionRecord, ...]:
        value, _blob = self._read_control(repository)
        return tuple(
            self._record_from_dict(item) for item in value["records"]
        )

    def allows(
        self,
        repository: str,
        writer_generation: str,
        activation_id: str,
    ) -> bool:
        current = self.read_current(repository)
        record = self.read(repository, current.record_id)
        return (
            current.writer_generation == writer_generation
            and record is not None
            and record.status in {"cut_over", "draining"}
            and record.activation_id == activation_id
        )

    def allows_new_work(
        self,
        repository: str,
        writer_generation: str,
        activation_id: str,
    ) -> bool:
        current = self.read_current(repository)
        record = self.read(repository, current.record_id)
        return (
            current.writer_generation == writer_generation
            and record is not None
            and record.status == "cut_over"
            and record.activation_id == activation_id
        )

    def capacity_limits(
        self,
        repository: str,
        writer_generation: str,
        activation_id: str,
    ) -> tuple[int, int]:
        if not self.allows_new_work(
            repository,
            writer_generation,
            activation_id,
        ):
            return 0, 0
        current = self.read_current(repository)
        record = self.read(repository, current.record_id)
        assert record is not None
        return record.worker_capacity, record.coordinator_capacity


@dataclass(frozen=True)
class WriterTransitionOutcome:
    status: str
    repository: str
    writer_generation: str
    record_id: str
    activation_id: str | None
    worker_capacity: int
    coordinator_capacity: int
    blockers: tuple[str, ...] = ()
    imported_legacy_identity_count: int = 0


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _record(
    *,
    repository: str,
    kind: str,
    status: str,
    previous_writer_generation: str,
    writer_generation: str,
    activation_id: str | None,
    plan_digest: str | None,
    canary_evidence_digest: str | None,
    canary_evidence_refs: tuple[str, ...],
    canary_manifest_ref: str | None,
    worker_capacity: int,
    coordinator_capacity: int,
    reason: str | None,
) -> WriterTransitionRecord:
    identity = {
        "repository": repository,
        "kind": kind,
        "status": status,
        "previous_writer_generation": previous_writer_generation,
        "writer_generation": writer_generation,
        "activation_id": activation_id,
        "plan_digest": plan_digest,
        "canary_evidence_digest": canary_evidence_digest,
        "canary_evidence_refs": canary_evidence_refs,
        "canary_manifest_ref": canary_manifest_ref,
        "worker_capacity": worker_capacity,
        "coordinator_capacity": coordinator_capacity,
        "reason": reason,
    }
    return WriterTransitionRecord(
        record_id=f"writer-transition:{digest_value(identity)[:24]}",
        created_at=_now(),
        **identity,
    )


class WriterCutoverController:
    """Fence V6.1, activate V8, and append compensating rollback records."""

    def __init__(
        self,
        *,
        legacy: LegacyWriterControl,
        transitions: WriterTransitionControl,
        publication: LocalPlanPublication,
    ):
        if publication.writer_authority is not transitions:
            raise ValueError(
                "cutover publication must use the transition control as writer fence"
            )
        self.legacy = legacy
        self.transitions = transitions
        self.publication = publication

    def cutover(
        self,
        compiled_plan: CompiledPlan,
        *,
        canary: CanaryAcceptance,
        writer_generation: str,
        worker_capacity: int,
        coordinator_capacity: int,
    ) -> WriterTransitionOutcome:
        repository = compiled_plan.repository
        current = self.transitions.read_current(repository)
        existing = self.transitions.read(repository, current.record_id)
        if current.writer_generation == writer_generation:
            if (
                existing is not None
                and existing.kind == "cutover"
                and existing.status == "cut_over"
                and existing.writer_generation == writer_generation
                and existing.plan_digest == compiled_plan.digest
                and existing.canary_evidence_digest
                == canary.evidence_package_digest
                and existing.worker_capacity == worker_capacity
                and existing.coordinator_capacity == coordinator_capacity
            ):
                active = self.publication.read_active(repository)
                if (
                    active is None
                    or active.activation_id != existing.activation_id
                ):
                    raise ValueError(
                        "cutover authority and Activation do not agree"
                    )
                return WriterTransitionOutcome(
                    status="cut_over",
                    repository=repository,
                    writer_generation=writer_generation,
                    record_id=existing.record_id,
                    activation_id=existing.activation_id,
                    worker_capacity=worker_capacity,
                    coordinator_capacity=coordinator_capacity,
                )

        def blocked_outcome(blockers: set[str]) -> WriterTransitionOutcome:
            ordered = tuple(sorted(blockers))
            record = _record(
                repository=repository,
                kind="cutover",
                status="blocked",
                previous_writer_generation=current.writer_generation,
                writer_generation=current.writer_generation,
                activation_id=None,
                plan_digest=compiled_plan.digest,
                canary_evidence_digest=canary.evidence_package_digest,
                canary_evidence_refs=canary.evidence_refs,
                canary_manifest_ref=canary.manifest_ref,
                worker_capacity=0,
                coordinator_capacity=0,
                reason=";".join(ordered),
            )
            self.transitions.publish(record)
            return WriterTransitionOutcome(
                status="blocked",
                repository=repository,
                writer_generation=current.writer_generation,
                record_id=record.record_id,
                activation_id=None,
                worker_capacity=0,
                coordinator_capacity=0,
                blockers=ordered,
            )

        blockers: set[str] = set()
        if (
            not canary.accepted
            or canary.evidence_package_digest is None
            or canary.manifest_ref is None
        ):
            blockers.add("CANARY_NOT_ACCEPTED")
        if worker_capacity != 8 or coordinator_capacity != 1:
            blockers.add("CUTOVER_CAPACITY_INVALID")
        resuming_pending = (
            current.writer_generation == writer_generation
            and existing is not None
            and existing.kind == "cutover_pending"
            and existing.status == "pending"
            and existing.plan_digest == compiled_plan.digest
            and existing.canary_evidence_digest
            == canary.evidence_package_digest
            and existing.canary_manifest_ref == canary.manifest_ref
        )
        if current.writer_generation != "v6.1" and not resuming_pending:
            blockers.add("CUTOVER_SOURCE_WRITER_INVALID")
        if blockers:
            return blocked_outcome(blockers)

        stop_action = (
            f"stop-v61:{digest_value({'repository': repository})[:24]}"
        )
        self.legacy.stop(repository, action_key=stop_action)
        legacy = self.legacy.readback(repository)
        if (
            not legacy.stopped
            or legacy.active_dispatches
            or legacy.integration_lease
            or legacy.active_workers
        ):
            blockers.add("V61_EXECUTION_AUTHORITY_ACTIVE")
        if blockers:
            return blocked_outcome(blockers)
        if not resuming_pending:
            pending = _record(
                repository=repository,
                kind="cutover_pending",
                status="pending",
                previous_writer_generation=current.writer_generation,
                writer_generation=writer_generation,
                activation_id=None,
                plan_digest=compiled_plan.digest,
                canary_evidence_digest=canary.evidence_package_digest,
                canary_evidence_refs=canary.evidence_refs,
                canary_manifest_ref=canary.manifest_ref,
                worker_capacity=0,
                coordinator_capacity=0,
                reason=None,
            )
            self.transitions.publish(pending)
        activation = self.publication.publish_and_activate(
            compiled_plan,
            expected_active_digest=None,
            writer_generation=writer_generation,
        )
        record = _record(
            repository=repository,
            kind="cutover",
            status="cut_over",
            previous_writer_generation=writer_generation,
            writer_generation=writer_generation,
            activation_id=activation.activation_id,
            plan_digest=compiled_plan.digest,
            canary_evidence_digest=canary.evidence_package_digest,
            canary_evidence_refs=canary.evidence_refs,
            canary_manifest_ref=canary.manifest_ref,
            worker_capacity=worker_capacity,
            coordinator_capacity=coordinator_capacity,
            reason=None,
        )
        self.transitions.publish(record)
        if self.transitions.read(repository, record.record_id) != record:
            raise ValueError("durable cutover record did not read back")
        active = self.publication.read_active(repository)
        if active is None or active.activation_id != activation.activation_id:
            raise ValueError("cutover writer fence did not authorize the Activation")
        return WriterTransitionOutcome(
            status="cut_over",
            repository=repository,
            writer_generation=writer_generation,
            record_id=record.record_id,
            activation_id=activation.activation_id,
            worker_capacity=worker_capacity,
            coordinator_capacity=coordinator_capacity,
        )

    def rollback(
        self,
        *,
        repository: str,
        ownership: V8OwnershipControl,
        restore_writer_generation: str,
        reason: str,
    ) -> WriterTransitionOutcome:
        current = self.transitions.read_current(repository)
        current_record = self.transitions.read(repository, current.record_id)
        if (
            current.writer_generation == restore_writer_generation
            and current_record is not None
            and current_record.kind == "rollback"
            and current_record.status == "rolled_back"
            and current_record.reason == reason
        ):
            self.legacy.restore(
                repository,
                action_key=f"restore-v61:{current_record.record_id}",
            )
            if self.legacy.readback(repository).stopped:
                raise ValueError("V6.1 writer did not restore after durable rollback")
            return WriterTransitionOutcome(
                status="rolled_back",
                repository=repository,
                writer_generation=restore_writer_generation,
                record_id=current_record.record_id,
                activation_id=current_record.activation_id,
                worker_capacity=0,
                coordinator_capacity=0,
            )
        blockers: set[str] = set()
        if not reason:
            blockers.add("ROLLBACK_REASON_REQUIRED")
        if current.writer_generation == restore_writer_generation:
            blockers.add("ROLLBACK_SOURCE_WRITER_INVALID")
        durable_activation = self.publication.durable.read_current_activation(
            repository
        )
        activation_id = (
            None
            if current_record is None
            else current_record.activation_id
        ) or (
            None
            if durable_activation is None
            else durable_activation.activation_id
        )
        if current_record is None:
            blockers.add("ROLLBACK_TRANSITION_MISSING")
        if not blockers:
            was_pending = current_record.status == "pending"
            if current_record.status != "draining":
                drain_record = _record(
                    repository=repository,
                    kind="drain",
                    status="draining",
                    previous_writer_generation=current.writer_generation,
                    writer_generation=current.writer_generation,
                    activation_id=activation_id,
                    plan_digest=current_record.plan_digest,
                    canary_evidence_digest=(
                        current_record.canary_evidence_digest
                    ),
                    canary_evidence_refs=current_record.canary_evidence_refs,
                    canary_manifest_ref=current_record.canary_manifest_ref,
                    worker_capacity=0,
                    coordinator_capacity=0,
                    reason=reason,
                )
                try:
                    self.transitions.publish(drain_record)
                except WriterTransitionBlocked as error:
                    # The GitHub Writer transition owner saw a durable active
                    # provider-dispatch fence at its own control-ref CAS.
                    # Leave every local drain state untouched; a Gateway
                    # retry/readback must resolve that exact fence first.
                    return WriterTransitionOutcome(
                        status="blocked",
                        repository=repository,
                        writer_generation=current.writer_generation,
                        record_id=current_record.record_id,
                        activation_id=activation_id,
                        worker_capacity=0,
                        coordinator_capacity=0,
                        blockers=(error.code,),
                    )
                current_record = drain_record
            if (
                was_pending
                and activation_id is None
                and current_record.plan_digest is not None
            ):
                self.publication.abandon_pending_activation(
                    repository,
                    writer_generation=current.writer_generation,
                    plan_digest=current_record.plan_digest,
                )
            if (
                was_pending
                and activation_id is not None
                and current_record.plan_digest is not None
            ):
                receipt = self.publication.durable.read_activation(
                    repository,
                    activation_id,
                )
                plan_record = self.publication.durable.read_plan(
                    repository,
                    current_record.plan_digest,
                )
                if receipt is None or plan_record is None:
                    raise ValueError(
                        "pending rollback lost its durable Plan or Activation"
                    )
                self.publication.finalize_pending_from_readback(
                    plan_record,
                    receipt,
                )
            if activation_id is not None:
                self.publication.begin_writer_drain(
                    repository,
                    writer_generation=current.writer_generation,
                    activation_id=activation_id,
                )
            ownership.drain(
                repository,
                source_ref=f"writer-transition://{current_record.record_id}",
            )
            drained = ownership.readback(repository)
            if (
                drained.active_admissions
                or drained.active_attempts
                or drained.integration_lease
                or drained.runtime_resources
            ):
                blockers.add("V8_DRAIN_PENDING")
        status = "blocked" if blockers else "rolled_back"
        target = (
            current.writer_generation if blockers else restore_writer_generation
        )
        record = _record(
            repository=repository,
            kind="rollback",
            status=status,
            previous_writer_generation=current.writer_generation,
            writer_generation=target,
            activation_id=(
                None if current_record is None else activation_id
            ),
            plan_digest=(
                None if current_record is None else current_record.plan_digest
            ),
            canary_evidence_digest=None,
            canary_evidence_refs=(
                ()
                if current_record is None
                else current_record.canary_evidence_refs
            ),
            canary_manifest_ref=(
                None
                if current_record is None
                else current_record.canary_manifest_ref
            ),
            worker_capacity=0,
            coordinator_capacity=0,
            reason=reason if not blockers else ";".join(sorted(blockers)),
        )
        self.transitions.publish(record)
        if not blockers:
            self.legacy.restore(
                repository,
                action_key=f"restore-v61:{record.record_id}",
            )
            if self.legacy.readback(repository).stopped:
                failure = _record(
                    repository=repository,
                    kind="rollback_restore",
                    status="blocked",
                    previous_writer_generation=target,
                    writer_generation=target,
                    activation_id=record.activation_id,
                    plan_digest=record.plan_digest,
                    canary_evidence_digest=None,
                    canary_evidence_refs=record.canary_evidence_refs,
                    canary_manifest_ref=record.canary_manifest_ref,
                    worker_capacity=0,
                    coordinator_capacity=0,
                    reason="V61_RESTORE_READBACK_FAILED",
                )
                self.transitions.publish(failure)
                return WriterTransitionOutcome(
                    status="blocked",
                    repository=repository,
                    writer_generation=target,
                    record_id=failure.record_id,
                    activation_id=record.activation_id,
                    worker_capacity=0,
                    coordinator_capacity=0,
                    blockers=("V61_RESTORE_READBACK_FAILED",),
                )
        return WriterTransitionOutcome(
            status=status,
            repository=repository,
            writer_generation=target,
            record_id=record.record_id,
            activation_id=record.activation_id,
            worker_capacity=0,
            coordinator_capacity=0,
            blockers=tuple(sorted(blockers)),
            imported_legacy_identity_count=0,
        )
