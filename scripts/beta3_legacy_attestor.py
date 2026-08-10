from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
import shlex
from typing import Callable, Mapping

from gwo_v8._canonical import canonical_bytes, digest_bytes, digest_value, load_canonical_json
from gwo_v8.cutover_guard import CutoverSubject, LegacyReadback

from beta3_bootstrap_model import (
    AttemptIdentity,
    BootstrapError,
    ComponentObservation,
    FieldBinding,
    SourceObservation,
    SourceRecord,
    WriterAuthorityObservation,
    require_read_only_surface,
)


@dataclass(frozen=True)
class LegacySourceSet:
    dispatches: object
    workers: object
    processes: object
    decoder: object | None = None


@dataclass(frozen=True)
class _ObservedSource:
    name: str
    record: SourceRecord
    payload: bytes
    value: object
    records: tuple[dict[str, object], ...]
    complete: bool


def _unavailable(detail: str) -> None:
    raise BootstrapError("LEGACY_SOURCE_UNAVAILABLE", detail)


def _drift(detail: str) -> None:
    raise BootstrapError("LIVE_INPUT_DRIFT", detail)


def _exact_text(value: object, name: str) -> str:
    if type(value) is not str or not value:
        _unavailable(f"{name} is not non-empty exact text")
    return value


def _canonical_source_value(observation: object) -> _ObservedSource:
    if type(observation) is not SourceObservation:
        _unavailable("source did not return a SourceObservation")
    if observation.complete is not True:
        _unavailable("source observation is incomplete")
    try:
        value = load_canonical_json(observation.canonical_payload)
    except Exception as error:
        _unavailable("source observation is not canonical JSON")
        raise AssertionError from error
    if digest_bytes(observation.canonical_payload) != observation.record.content_sha256:
        _unavailable("source record content digest does not match observation")
    return _ObservedSource(
        name="",
        record=observation.record,
        payload=observation.canonical_payload,
        value=value,
        records=(),
        complete=True,
    )


def _records_from_value(value: object, *, source: str) -> tuple[dict[str, object], ...]:
    if type(value) is list:
        raw = value
    elif type(value) is dict:
        raw = None
        for key in ("records", "dispatches", "workers", "processes", "agents", "issues"):
            candidate = value.get(key)
            if type(candidate) is list:
                raw = candidate
                break
        if raw is None:
            if any(
                key in value
                for key in (
                    "id",
                    "Id",
                    "agent_id",
                    "worker_id",
                    "ProcessId",
                    "process_id",
                    "dispatch_id",
                    "status",
                    "Status",
                )
            ):
                raw = [value]
            else:
                raw = []
    else:
        _unavailable(f"{source} observation is not an object or list")
    records: list[dict[str, object]] = []
    for item in raw:
        if type(item) is not dict:
            _unavailable(f"{source} observation contains a non-object record")
        records.append(dict(item))
    return tuple(records)


def _source_observation(
    source: object,
    repository: str,
    *,
    name: str,
) -> _ObservedSource:
    try:
        observation = source.read(repository)
    except BootstrapError:
        raise
    except Exception as error:
        _unavailable(f"{name} source read failed")
        raise AssertionError from error
    result = _canonical_source_value(observation)
    if result.record.repository != repository:
        _unavailable(f"{name} source repository differs from subject")
    records = _records_from_value(result.value, source=name)
    return replace(result, name=name, records=records)


def _record_identity(record: Mapping[str, object], *, name: str) -> str:
    for key in ("reference", "identity", "id", "Id", "agent_id", "worker_id"):
        value = record.get(key)
        if type(value) is str and value:
            return value
        if key == "identity" and type(value) is dict:
            candidate = value.get("reference") or value.get("id")
            if type(candidate) is str and candidate:
                return candidate
    _unavailable(f"{name} record has no identity")


def _status(record: Mapping[str, object], *, name: str) -> str:
    value = record.get("status", record.get("Status"))
    if type(value) is not str or not value:
        _unavailable(f"{name} record has no status")
    return value.casefold()


_DISPATCH_TERMINAL = {
    "closed",
    "error",
    "stopped",
    "merged",
    "retired",
    "terminal",
    "completed",
    "complete",
    "succeeded",
    "failed",
    "cancelled",
    "canceled",
    "abandoned",
    "consumed",
}
_DISPATCH_ACTIVE = {
    "claiming",
    "running",
    "review",
    "ready-to-merge",
    "integrating",
    "parking",
    "resuming",
    "blocked",
    "active",
    "pending",
    "queued",
    "waiting",
    "retrying",
    "in_progress",
    "in-progress",
}


def _dispatch_reference(record: Mapping[str, object]) -> str:
    identity = _record_identity(record, name="Dispatch")
    if identity.startswith("dispatch:"):
        return identity
    return f"dispatch:{identity}"


def _dispatch_facts(source: _ObservedSource) -> tuple[tuple[str, ...], tuple[str, ...]]:
    dispatch_records: list[dict[str, object]] = []

    def collect(value: object) -> None:
        if type(value) is dict:
            dispatch = value.get("dispatch")
            if type(dispatch) is dict:
                dispatch_records.append(dict(dispatch))
            elif any(key in value for key in ("id", "reference", "status")):
                dispatch_records.append(dict(value))
            dispatches = value.get("dispatches")
            if type(dispatches) is list:
                for item in dispatches:
                    if type(item) is dict:
                        dispatch_records.append(dict(item))
            for key, child in value.items():
                if key not in {"dispatch", "dispatches"}:
                    collect(child)
        elif type(value) is list:
            for child in value:
                collect(child)

    if source.records:
        for item in source.records:
            collect(item)
    else:
        collect(source.value)
    active: dict[str, dict[str, object]] = {}
    v2_refs: set[str] = set()
    root = source.value
    if type(root) is dict:
        root_refs = root.get("v2_execution_refs")
        if root_refs is not None:
            if type(root_refs) is not list or any(
                type(ref) is not str or not ref for ref in root_refs
            ):
                _unavailable("Dispatch V2 execution references are malformed")
            v2_refs.update(root_refs)
    for item in dispatch_records:
        status = _status(item, name="Dispatch")
        if status not in _DISPATCH_TERMINAL and status not in _DISPATCH_ACTIVE:
            _unavailable(f"Dispatch status is unknown: {status}")
        reference = _dispatch_reference(item)
        for key in ("v2_execution_ref", "v2_ref", "execution_ref"):
            ref = item.get(key)
            if ref is not None:
                if type(ref) is not str or not ref:
                    _unavailable("Dispatch V2 execution reference is malformed")
                v2_refs.add(ref)
        if status in _DISPATCH_ACTIVE:
            prior = active.get(reference)
            if prior is not None and prior != item:
                _unavailable("duplicate Dispatch identity has conflicting records")
            active[reference] = item
    return tuple(sorted(active)), tuple(sorted(v2_refs))


def _worker_reference(record: Mapping[str, object]) -> str:
    value = record.get("reference")
    if type(value) is str and value:
        return value
    return _record_identity(record, name="Worker")


def _worker_facts(source: _ObservedSource, expected_repository: str) -> tuple[str, ...]:
    active: dict[str, dict[str, object]] = {}
    for item in source.records:
        identity = _record_identity(item, name="Worker")
        inspection = item.get("inspect", item.get("inspection"))
        if type(inspection) is not dict:
            _unavailable("Worker record has no exact inspect readback")
        inspected_id = inspection.get("id", inspection.get("Id"))
        if inspected_id != identity:
            _unavailable("Worker inspect identity differs from inventory identity")
        inspected_repository = inspection.get("repository")
        if type(inspected_repository) is not str or inspected_repository != expected_repository:
            _unavailable("Worker inspect repository differs from subject")
        role = inspection.get("role")
        if type(role) is not str or role != "worker":
            _unavailable("Worker inspect role differs from query")
        state = inspection.get("status", inspection.get("Status"))
        if type(state) is not str or not state:
            _unavailable("Worker inspect state is absent")
        archived = inspection.get("archived", inspection.get("Archived", False))
        if type(archived) is not bool:
            _unavailable("Worker inspect archived flag is malformed")
        archived_at = inspection.get("archivedAt", inspection.get("archived_at"))
        if archived_at is not None and (type(archived_at) is not str or not archived_at):
            _unavailable("Worker inspect archived timestamp is malformed")
        if archived or archived_at is not None or state.casefold() in {"archived", "closed"}:
            continue
        reference = _worker_reference(item)
        prior = active.get(reference)
        if prior is not None and prior != item:
            _unavailable("duplicate Worker identity has conflicting records")
        active[reference] = item
    return tuple(sorted(active))


def _process_identity(record: Mapping[str, object]) -> str:
    explicit = record.get("identity", record.get("lease_owner"))
    if type(explicit) is str and explicit:
        return explicit
    process_id = record.get("ProcessId", record.get("process_id"))
    creation = record.get("CreationDate", record.get("creation_date"))
    if type(process_id) not in {int, str} or type(creation) is not str or not creation:
        _unavailable("process identity is incomplete")
    return f"process:{process_id}:{creation}"


def _process_fields(record: Mapping[str, object]) -> tuple[object, ...]:
    names = (
        ("ProcessId", "process_id"),
        ("ParentProcessId", "parent_process_id"),
        ("CreationDate", "creation_date"),
        ("ExecutablePath", "executable_path"),
        ("CommandLine", "command_line"),
    )
    values: list[object] = []
    for index, (primary, alternate) in enumerate(names):
        value = record.get(primary, record.get(alternate))
        if index < 2:
            valid = type(value) is int
        else:
            valid = type(value) is str and bool(value)
        if not valid:
            _unavailable(f"process inventory field is missing: {primary}")
        values.append(value)
    return tuple(values)


class LegacyAttestor:
    def __init__(self, sources: LegacySourceSet) -> None:
        if type(sources) is not LegacySourceSet:
            raise BootstrapError("UNSAFE_SOURCE_CAPABILITY", "sources have the wrong exact type")
        for name in ("dispatches", "workers", "processes"):
            require_read_only_surface(getattr(sources, name), required_method="read")
        if sources.decoder is not None:
            require_read_only_surface(sources.decoder, required_method="read")
        self._sources = sources

    def observe(
        self,
        *,
        subject: CutoverSubject,
        attempt: AttemptIdentity,
        writer: WriterAuthorityObservation,
    ) -> ComponentObservation:
        if type(subject) is not CutoverSubject:
            _unavailable("subject has the wrong exact type")
        if type(attempt) is not AttemptIdentity:
            _unavailable("attempt has the wrong exact type")
        if type(writer) is not WriterAuthorityObservation:
            _unavailable("writer observation has the wrong exact type")
        if attempt.repository != subject.repository:
            _unavailable("attempt repository differs from subject")
        if attempt.cutover_subject_digest != digest_value(subject.canonical()):
            _unavailable("attempt does not bind subject")
        sources = tuple(
            _source_observation(getattr(self._sources, name), subject.repository, name=name)
            for name in ("dispatches", "workers", "processes")
        )
        source_records = tuple(sorted((item.record for item in sources), key=lambda item: item.digest))
        if len({record.digest for record in source_records}) != len(source_records):
            _unavailable("legacy source record identities are not unique")
        active_dispatches, v2_refs = _dispatch_facts(sources[0])
        for item in source_records:
            if item.producer_sha256 != attempt.attestor_sha256:
                _unavailable("legacy source producer does not match attestor")
        active_workers = _worker_facts(sources[1], subject.repository)
        for item in sources[2].records:
            _process_fields(item)
        lease_owner = None
        if sources[2].records:
            matching = []
            for item in sources[2].records:
                lease = item.get(
                    "integration_lease",
                    item.get("matches_integration", item.get("is_integration_lease")),
                )
                if lease is not None and type(lease) is not bool:
                    _unavailable("process integration lease flag is malformed")
                if lease is True:
                    matching.append(item)
            owners = tuple(sorted({_process_identity(item) for item in matching}))
            if len(owners) > 1:
                _unavailable("multiple integration lease owners are contradictory")
            lease_owner = owners[0] if owners else None
        if writer.legacy_stopped:
            authority_state = "stopped"
        elif active_dispatches or active_workers or lease_owner is not None:
            authority_state = "active"
        elif writer.writer_generation == "v6.1":
            authority_state = "authoritative_quiescent"
        else:
            _unavailable("legacy observations do not prove authoritative quiescence")
        decoder_proof: tuple[object, ...] = ()
        if v2_refs:
            if self._sources.decoder is None:
                _unavailable("non-empty V2 references have no original decoder")
            proofs: list[object] = []
            for reference in v2_refs:
                try:
                    proof = self._sources.decoder.read(reference)
                except Exception as error:
                    _unavailable("original decoder read failed")
                    raise AssertionError from error
                if type(proof) is not SourceObservation or proof.complete is not True:
                    _unavailable("original decoder proof is incomplete")
                parsed = _canonical_source_value(proof)
                if parsed.record.producer_sha256 != attempt.attestor_sha256:
                    _unavailable("original decoder producer does not match attestor")
                if type(parsed.value) is not dict or parsed.value.get("ref") != reference:
                    _unavailable("original decoder proof does not match V2 reference")
                if parsed.value.get("readable") is not True or parsed.value.get("effectful") is True:
                    _unavailable("original decoder proof is not read-only")
                proofs.append(parsed.value)
                source_records += (parsed.record,)
            decoder_proof = tuple(proofs)
            states = {str(proof.get("state")) for proof in proofs}
            if states == {"terminal"}:
                v2_state = "terminal"
            elif states == {"running"}:
                v2_state = "running"
            elif states == {"quiescent_read_only"}:
                v2_state = "quiescent_read_only"
            else:
                _unavailable("original decoder proofs have contradictory states")
            decoder_readable = True
        else:
            v2_state = "none"
            decoder_readable = True
        source_record_values = tuple(source_records)
        source_record_digests = tuple(record.digest for record in source_record_values)
        if len(set(source_record_digests)) != len(source_record_digests):
            _unavailable("legacy source record identities are not unique")
        envelope = {
            "kind": "gwo.beta3.legacy-authority.v1",
            "repository": subject.repository,
            "writer_source_record_digests": list(writer.source_record_digests),
            "writer": writer.canonical(),
            "source_records": [
                record.canonical()
                for record in sorted(source_record_values, key=lambda item: item.digest)
            ],
            "dispatch": sources[0].value,
            "workers": sources[1].value,
            "processes": sources[2].value,
            "active_dispatches": list(active_dispatches),
            "active_workers": list(active_workers),
            "integration_lease_owner": lease_owner,
            "v2_execution_refs": list(v2_refs),
            "v2_execution_state": v2_state,
            "decoder_proof": list(decoder_proof),
        }
        legacy = LegacyReadback(
            repository=subject.repository,
            writer_generation=writer.writer_generation,
            authority_state=authority_state,
            active_dispatches=active_dispatches,
            active_workers=active_workers,
            integration_lease_owner=lease_owner,
            v2_execution_refs=v2_refs,
            v2_execution_state=v2_state,
            original_decoder_readable=decoder_readable,
            durable_state_digest=digest_value(envelope),
        )
        body = legacy.canonical()
        body.pop("readback_digest")
        legacy = replace(legacy, readback_digest=digest_value(body))
        all_records = tuple(sorted(source_record_values, key=lambda item: item.digest))
        source_digests = tuple(record.digest for record in all_records)
        writer_digests = writer.source_record_digests or source_digests
        dispatch_digest = sources[0].record.digest
        worker_digest = sources[1].record.digest
        process_digest = sources[2].record.digest
        decoder_digests = tuple(
            record.digest
            for record in source_record_values
            if record.role.startswith("legacy.decoder") or record.role == "fixture.decoder"
        )
        decoder_bindings = decoder_digests or (dispatch_digest,)
        authority_bindings = tuple(
            sorted(set(writer_digests + (dispatch_digest, worker_digest, process_digest)))
        )
        field_sources: dict[str, tuple[tuple[str, ...], str]] = {
            "repository": (authority_bindings, "source_repository_cross_check"),
            "writer_generation": (tuple(writer_digests), "writer_authority"),
            "authority_state": (authority_bindings, "authority_precedence"),
            "active_dispatches": ((dispatch_digest,), "dispatch_snapshot"),
            "active_workers": ((worker_digest,), "worker_inspect_readback"),
            "integration_lease_owner": ((process_digest,), "process_inventory"),
            "v2_execution_refs": ((dispatch_digest,), "dispatch_v2_reference_projection"),
            "v2_execution_state": (
                decoder_bindings,
                "original_decoder_readback" if v2_refs else "vacuous_empty_reference_set",
            ),
            "original_decoder_readable": (
                decoder_bindings,
                "original_decoder_readback" if v2_refs else "vacuous_empty_reference_set",
            ),
            "durable_state_digest": (
                tuple(sorted(set(source_digests + tuple(writer_digests)))),
                "legacy_authority_envelope",
            ),
            "readback_digest": (
                tuple(sorted(set(source_digests + tuple(writer_digests)))),
                "canonical_digest",
            ),
        }
        bindings = tuple(
            FieldBinding(
                target=f"legacy.{field}",
                source_record_digests=field_sources[field][0],
                derivation=field_sources[field][1],
            )
            for field in legacy.canonical()
        )
        return ComponentObservation(
            readbacks=(("legacy", legacy),),
            source_records=all_records,
            field_bindings=bindings,
            writer_authority=writer,
        )


class _CommandReader:
    def __init__(self, command_runner: Callable[[tuple[str, ...]], bytes], producer_sha256: str) -> None:
        if not callable(command_runner) or type(producer_sha256) is not str:
            raise BootstrapError("LEGACY_SOURCE_UNAVAILABLE", "production reader configuration is invalid")
        self._command_runner = command_runner
        self._producer_sha256 = producer_sha256

    def _json(self, command: tuple[str, ...], *, role: str, locator: str, repository: str, payload_kind: str) -> SourceObservation:
        try:
            raw = self._command_runner(command)
            if type(raw) is not bytes:
                _unavailable(f"{role} command did not return exact bytes")
            value = load_canonical_json(raw)
        except BootstrapError:
            raise
        except Exception as error:
            _unavailable(f"{role} command returned invalid canonical JSON")
            raise AssertionError from error
        payload = canonical_bytes(value)
        digest = digest_bytes(payload)
        record = SourceRecord(
            role=role,
            locator=locator,
            repository=repository,
            read_mode="COMPLETE_DOUBLE_READ",
            identity=(("observation_digest", digest),),
            content_sha256=digest,
            readback_digest=None,
            producer_sha256=self._producer_sha256,
        )
        return SourceObservation(record=record, canonical_payload=payload, complete=True)


class GitHubDispatchSnapshotReader(_CommandReader):
    def read(self, repository: str) -> SourceObservation:
        owner, separator, name = repository.partition("/")
        if not separator or not owner or not name:
            _unavailable("repository is not owner/name")
        command = (
            "gh",
            "api",
            "graphql",
            "-f",
            f"query={_SNAPSHOT_QUERY}",
            "-F",
            f"owner={owner}",
            "-F",
            f"name={name}",
            "-F",
            "branch=refs/heads/main",
        )
        observation = self._json(
            command,
            role="legacy.dispatches",
            locator=f"github://{repository}/graphql/snapshot",
            repository=repository,
            payload_kind="github",
        )
        _validate_github_snapshot(observation)
        return observation


class PaseoWorkerInventoryReader(_CommandReader):
    def read(self, repository: str) -> SourceObservation:
        command = (
            "paseo",
            "ls",
            "--global",
            "--all",
            "--label",
            f"orch.repository={repository}",
            "--label",
            "orch.role=worker",
            "--json",
        )
        first = self._json(
            command,
            role="legacy.workers",
            locator=f"paseo://global/worker-inventory/{repository}",
            repository=repository,
            payload_kind="workers",
        )
        rows = _records_from_value(first_value := load_canonical_json(first.canonical_payload), source="Paseo")
        identities: list[str] = []
        normalized: list[dict[str, object]] = []
        for row in rows:
            identity = _record_identity(row, name="Worker")
            if identity in identities:
                _unavailable("Paseo Worker inventory contains duplicate identity")
            identities.append(identity)
            inspected = self._json(
                ("paseo", "inspect", identity, "--json"),
                role="legacy.worker.inspect",
                locator=f"paseo://inspect/{identity}",
                repository=repository,
                payload_kind="worker-inspect",
            )
            inspect_value = load_canonical_json(inspected.canonical_payload)
            if type(inspect_value) is not dict:
                _unavailable("Paseo Worker inspect response is not an object")
            inspected_id = inspect_value.get("id", inspect_value.get("Id"))
            if inspected_id != identity:
                _unavailable("Paseo Worker inspect identity differs from inventory")
            if inspect_value.get("repository") not in {repository, None}:
                _unavailable("Paseo Worker inspect repository differs from query")
            if inspect_value.get("role") not in {"worker", None}:
                _unavailable("Paseo Worker inspect role differs from query")
            normalized.append({"inventory": row, "inspect": inspect_value, "id": identity})
        payload = canonical_bytes({"repository": repository, "workers": normalized, "inventory": first_value})
        digest = digest_bytes(payload)
        record = SourceRecord(
            role="legacy.workers",
            locator=f"paseo://global/worker-inventory/{repository}",
            repository=repository,
            read_mode="COMPLETE_DOUBLE_READ",
            identity=(("observation_digest", digest),),
            content_sha256=digest,
            readback_digest=None,
            producer_sha256=self._producer_sha256,
        )
        return SourceObservation(record, payload, True)


class CooperativeHostProcessReader(_CommandReader):
    def __init__(
        self,
        command_runner: Callable[[tuple[str, ...]], bytes],
        producer_sha256: str,
        repository_path: str | Path | None = None,
    ) -> None:
        super().__init__(command_runner, producer_sha256)
        self._repository_path = None if repository_path is None else str(Path(repository_path).resolve())

    def read(self, repository: str) -> SourceObservation:
        command = (
            "powershell",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            _PROCESS_QUERY,
        )
        observation = self._json(
            command,
            role="legacy.processes",
            locator="host://cim/win32-process",
            repository=repository,
            payload_kind="processes",
        )
        value = load_canonical_json(observation.canonical_payload)
        if type(value) is dict and not any(
            key in value
            for key in (
                "records",
                "processes",
                "ProcessId",
                "process_id",
            )
        ):
            _unavailable("process inventory is not a complete CIM response")
        rows = _records_from_value(value, source="process")
        normalized: list[dict[str, object]] = []
        for row in rows:
            _process_fields(row)
            item = dict(row)
            item["integration_lease"] = self._matches(repository, row)
            normalized.append(item)
        payload = canonical_bytes(normalized)
        digest = digest_bytes(payload)
        record = SourceRecord(
            role="legacy.processes",
            locator="host://cim/win32-process",
            repository=repository,
            read_mode="COMPLETE_DOUBLE_READ",
            identity=(("observation_digest", digest),),
            content_sha256=digest,
            readback_digest=None,
            producer_sha256=self._producer_sha256,
        )
        return SourceObservation(record, payload, True)

    def _matches(self, repository: str, row: Mapping[str, object]) -> bool:
        command_line = row.get("CommandLine", row.get("command_line"))
        if type(command_line) is not str:
            _unavailable("process command line is malformed")
        try:
            tokens = tuple(token.strip('"') for token in shlex.split(command_line, posix=False))
        except ValueError as error:
            _unavailable("process command line is not parseable")
            raise AssertionError from error
        normalized_tokens = {token.casefold() for token in tokens}
        repository_token = repository.casefold()
        has_repository = repository_token in normalized_tokens or any(
            token.casefold().endswith(f"={repository_token}")
            for token in tokens
        )
        action_tokens = {"integrate", "integration", "delivery", "deliver", "merge"}
        legacy_tokens = {"v6.1", "v6_1", "v61", "legacy"}
        has_action = bool(normalized_tokens & action_tokens)
        has_legacy = bool(normalized_tokens & legacy_tokens)
        if not (has_repository and has_action and has_legacy):
            return False
        if self._repository_path is None:
            return True
        explicit_path = row.get("RepositoryPath", row.get("repository_path"))
        if explicit_path is not None:
            return type(explicit_path) is str and explicit_path == self._repository_path
        executable = row.get("ExecutablePath", row.get("executable_path"))
        if type(executable) is not str:
            return False
        executable_path = Path(executable)
        try:
            return str(executable_path.parent.parent.parent) == self._repository_path
        except Exception:
            return False


def _validate_github_snapshot(observation: SourceObservation) -> None:
    value = load_canonical_json(observation.canonical_payload)
    if type(value) is not dict:
        _unavailable("GitHub snapshot is not an object")
    repository = value.get("data", value).get("repository") if type(value.get("data", value)) is dict else None
    if type(repository) is not dict:
        _unavailable("GitHub repository snapshot is missing")
    ref = repository.get("ref")
    if type(ref) is not dict or type(ref.get("target")) is not dict or type(ref["target"].get("oid")) is not str:
        _unavailable("GitHub snapshot base OID is missing")
    for key in ("readyIssues", "activeIssues", "blockedIssues", "pullRequests"):
        connection = repository.get(key)
        if type(connection) is not dict:
            _unavailable(f"GitHub snapshot connection is missing: {key}")
    def validate_pagination(item: object) -> None:
        if type(item) is dict:
            if "pageInfo" in item:
                page = item.get("pageInfo")
                if type(page) is not dict or page.get("hasNextPage") is not False:
                    _unavailable("GitHub snapshot pagination is incomplete")
            for child in item.values():
                validate_pagination(child)
        elif type(item) is list:
            for child in item:
                validate_pagination(child)

    validate_pagination(value)


_SNAPSHOT_QUERY = r"""
query($owner:String!,$name:String!,$branch:String!){
  repository(owner:$owner,name:$name){
    ref(qualifiedName:$branch){target{... on Commit{oid}}}
    readyIssues:issues(first:100,states:OPEN,labels:["orch:ready"],orderBy:{field:UPDATED_AT,direction:DESC}){
      totalCount pageInfo{hasNextPage}
      nodes{...OrchIssue}
    }
    activeIssues:issues(first:100,states:OPEN,labels:["orch:active"],orderBy:{field:UPDATED_AT,direction:DESC}){
      totalCount pageInfo{hasNextPage}
      nodes{...OrchIssue}
    }
    blockedIssues:issues(first:100,states:OPEN,labels:["orch:blocked"],orderBy:{field:UPDATED_AT,direction:DESC}){
      totalCount pageInfo{hasNextPage}
      nodes{...OrchIssue}
    }
    pullRequests(first:100,states:OPEN,orderBy:{field:UPDATED_AT,direction:DESC}){
      totalCount pageInfo{hasNextPage}
      nodes{__ORCH_PR_FIELDS__}
    }
  }
}
fragment OrchIssue on Issue{
  number title body updatedAt
  labels(first:100){pageInfo{hasNextPage} nodes{name}}
  milestone{title dueOn}
  assignees(first:20){nodes{login}}
  comments(first:100){pageInfo{hasNextPage} nodes{databaseId body createdAt updatedAt author{login}}}
}
""".replace(
    "__ORCH_PR_FIELDS__",
    """number state title body headRefName headRefOid baseRefName isDraft updatedAt mergedAt
mergeStateStatus reviewDecision url
reviews(first:100){pageInfo{hasNextPage} nodes{state body submittedAt author{login} commit{oid}}}
files(first:100){pageInfo{hasNextPage} nodes{path}}
commits(last:1){nodes{commit{statusCheckRollup{contexts(first:100){pageInfo{hasNextPage} nodes{
  ... on CheckRun{status conclusion}
  ... on StatusContext{state}
}}}}}}
""",
)


_PROCESS_QUERY = (
    "Get-CimInstance Win32_Process | "
    "Select-Object ProcessId, ParentProcessId, CreationDate, ExecutablePath, CommandLine | "
    "ConvertTo-Json -Compress"
)


def production_legacy_sources(
    *,
    command_runner: Callable[[tuple[str, ...]], bytes],
    producer_sha256: str,
) -> LegacySourceSet:
    return LegacySourceSet(
        dispatches=GitHubDispatchSnapshotReader(command_runner, producer_sha256),
        workers=PaseoWorkerInventoryReader(command_runner, producer_sha256),
        processes=CooperativeHostProcessReader(command_runner, producer_sha256),
        decoder=None,
    )
