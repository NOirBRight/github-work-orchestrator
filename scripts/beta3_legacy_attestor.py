from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
import re
import shlex
from typing import Callable, Mapping

import orch_core
from gwo_v8._canonical import (
    canonical_bytes,
    digest_bytes,
    digest_value,
    load_canonical_json,
    strict_json_loads,
)
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


def _dict_records(raw: object, *, source: str) -> tuple[dict[str, object], ...]:
    if type(raw) is not list:
        _unavailable(f"{source} observation records are not an exact list")
    records: list[dict[str, object]] = []
    for item in raw:
        if type(item) is not dict:
            _unavailable(f"{source} observation contains a non-object record")
        records.append(dict(item))
    return tuple(records)


def _fixture_records(
    value: object,
    *,
    source: str,
    repository: str,
) -> tuple[dict[str, object], ...]:
    if (
        type(value) is not dict
        or set(value) != {"repository", "records"}
        or value.get("repository") != repository
    ):
        _unavailable(f"{source} fixture observation has an unknown shape")
    return _dict_records(value["records"], source=source)


def _paseo_inventory_rows(value: object) -> tuple[dict[str, object], ...]:
    if type(value) is list:
        return _dict_records(value, source="Paseo")
    if type(value) is not dict:
        _unavailable("Paseo inventory is not an object or list")
    wrapper_keys = [key for key in ("agents", "workers", "records") if key in value]
    if wrapper_keys:
        if len(wrapper_keys) != 1 or set(value) != {wrapper_keys[0]}:
            _unavailable("Paseo inventory wrapper is ambiguous")
        return _dict_records(value[wrapper_keys[0]], source="Paseo")
    has_identity = any(key in value for key in ("id", "Id", "agent_id", "worker_id"))
    has_state = any(key in value for key in ("status", "Status", "state"))
    if not (has_identity and has_state):
        _unavailable("Paseo inventory object is not a Worker row")
    return (dict(value),)


def _process_inventory_rows(value: object) -> tuple[dict[str, object], ...]:
    if type(value) is list:
        return _dict_records(value, source="process")
    if type(value) is not dict:
        _unavailable("process inventory is not an object or list")
    wrapper_keys = [key for key in ("processes", "records") if key in value]
    if wrapper_keys:
        if len(wrapper_keys) != 1 or set(value) != {wrapper_keys[0]}:
            _unavailable("process inventory wrapper is ambiguous")
        return _dict_records(value[wrapper_keys[0]], source="process")
    required = (
        ("ProcessId", "process_id"),
        ("ParentProcessId", "parent_process_id"),
        ("CreationDate", "creation_date"),
        ("ExecutablePath", "executable_path"),
        ("CommandLine", "command_line"),
    )
    if not all(primary in value or alternate in value for primary, alternate in required):
        _unavailable("process inventory object is not a complete CIM row")
    return (dict(value),)


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
    live_role = f"legacy.{name}"
    fixture_role = f"fixture.{name}"
    if result.record.role == live_role:
        if result.record.read_mode != "COMPLETE_DOUBLE_READ":
            _unavailable(f"{name} live source is not a complete double read")
        if result.record.identity != (
            ("observation_digest", result.record.content_sha256),
        ):
            _unavailable(f"{name} live source observation identity is not content-bound")
    elif result.record.role == fixture_role:
        if result.record.read_mode != "FIXTURE":
            _unavailable(f"{name} fixture source read mode is invalid")
        if (
            len(result.record.identity) != 1
            or result.record.identity[0][0] != "fixture"
        ):
            _unavailable(f"{name} fixture source identity is invalid")
    else:
        _unavailable(f"{name} source role is invalid")
    if result.record.role == fixture_role:
        records = _fixture_records(
            result.value,
            source=name,
            repository=repository,
        )
    elif name == "dispatches":
        _github_repository(result.value, repository)
        records = ()
    elif name == "workers":
        value = result.value
        if (
            type(value) is not dict
            or set(value) != {"repository", "workers", "inventory"}
            or value.get("repository") != repository
        ):
            _unavailable("workers observation has an unknown normalized shape")
        records = _dict_records(value["workers"], source=name)
        inventory_rows = _paseo_inventory_rows(value["inventory"])
        inventory_by_identity: dict[str, dict[str, object]] = {}
        for row in inventory_rows:
            identity = _record_identity(row, name="Worker")
            if identity in inventory_by_identity:
                _unavailable("workers inventory contains duplicate identity")
            inventory_by_identity[identity] = row
        normalized_identities: set[str] = set()
        for record in records:
            if set(record) != {"id", "inventory", "inspect"}:
                _unavailable("workers normalized record has an unknown shape")
            identity = _record_identity(record, name="Worker")
            if identity in normalized_identities:
                _unavailable("workers normalized observation contains duplicate identity")
            normalized_identities.add(identity)
            if record["inventory"] != inventory_by_identity.get(identity):
                _unavailable("workers normalized record differs from inventory row")
        if normalized_identities != set(inventory_by_identity):
            _unavailable("workers normalized observation omits inventory identities")
    elif name == "processes":
        records = _dict_records(result.value, source=name)
        for record in records:
            if set(record) != {
                "ProcessId",
                "ParentProcessId",
                "CreationDate",
                "ExecutablePath",
                "CommandLine",
                "integration_lease",
            } or type(record["integration_lease"]) is not bool:
                _unavailable("processes normalized record has an unknown shape")
    else:
        _unavailable(f"unknown legacy source: {name}")
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
    if type(source.value) is dict and "data" in source.value:
        dispatch_records = list(
            _normalized_github_dispatches(source.value, source.record.repository)
        )
    else:
        dispatch_records = []

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
        archived, archived_at = _worker_archive_evidence(inspection)
        if archived or archived_at is not None or state.casefold() in {"archived", "closed"}:
            continue
        reference = _worker_reference(item)
        prior = active.get(reference)
        if prior is not None and prior != item:
            _unavailable("duplicate Worker identity has conflicting records")
        active[reference] = item
    return tuple(sorted(active))


def _worker_archive_evidence(
    inspection: Mapping[str, object],
) -> tuple[bool, str | None]:
    archived_keys = [key for key in ("archived", "Archived") if key in inspection]
    timestamp_keys = [key for key in ("archivedAt", "archived_at") if key in inspection]
    if len(archived_keys) > 1 or len(timestamp_keys) > 1:
        _unavailable("Worker inspect archive evidence is ambiguous")
    if not archived_keys and not timestamp_keys:
        _unavailable("Worker inspect archive evidence is absent")
    archived = False
    if archived_keys:
        value = inspection[archived_keys[0]]
        if type(value) is not bool:
            _unavailable("Worker inspect archived flag is malformed")
        archived = value
    archived_at = None
    if timestamp_keys:
        value = inspection[timestamp_keys[0]]
        if value is not None and (type(value) is not str or not value):
            _unavailable("Worker inspect archived timestamp is malformed")
        archived_at = value
    return archived, archived_at


def _validate_decoder_record(
    record: SourceRecord,
    *,
    reference: str,
    repository: str,
) -> None:
    if record.repository != repository:
        _unavailable("original decoder repository differs from subject")
    if record.role == "legacy.decoder":
        if record.read_mode != "COMPLETE_DOUBLE_READ":
            _unavailable("original decoder is not a complete double read")
        if record.identity != (
            ("observation_digest", record.content_sha256),
            ("reference", reference),
        ):
            _unavailable("original decoder identity is not reference/content-bound")
    elif record.role == "fixture.decoder":
        if record.read_mode != "FIXTURE" or record.identity != (
            ("reference", reference),
        ):
            _unavailable("original decoder fixture identity is invalid")
    else:
        _unavailable("original decoder role is invalid")


def _process_identity(record: Mapping[str, object]) -> str:
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
        elif index == 2:
            valid = type(value) is str and bool(value)
        else:
            valid = value is None or (type(value) is str and bool(value))
        if not valid:
            _unavailable(f"process inventory field is missing: {primary}")
        values.append(value)
    return tuple(values)


_POSSIBLE_V61_PROCESS_NAME = re.compile(
    r"^python(?:\d+(?:\.\d+)?)?w?\.exe$",
    re.IGNORECASE,
)


def _validate_process_visibility(record: Mapping[str, object]) -> None:
    executable = record.get("ExecutablePath", record.get("executable_path"))
    command_line = record.get("CommandLine", record.get("command_line"))
    if executable is not None and (type(executable) is not str or not executable):
        _unavailable("process executable path is malformed")
    if command_line is not None and (type(command_line) is not str or not command_line):
        _unavailable("process command line is malformed")
    if executable is not None and command_line is not None:
        return
    name = record.get("Name")
    if type(name) is not str or not name:
        _unavailable("incomplete process visibility has no executable name")
    if _POSSIBLE_V61_PROCESS_NAME.fullmatch(name):
        _unavailable("possible V6.1 process has incomplete visibility")


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
        if not writer.source_record_digests:
            _unavailable("writer authority has no source record identities")
        sources = tuple(
            _source_observation(getattr(self._sources, name), subject.repository, name=name)
            for name in ("dispatches", "workers", "processes")
        )
        if type(sources[0].value) is dict and "data" in sources[0].value:
            github_repository = _github_repository(
                sources[0].value,
                subject.repository,
            )
            if github_repository["ref"]["target"]["oid"] != subject.source_commit:
                _unavailable("GitHub snapshot base OID differs from cutover subject")
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
        decoder_records: list[SourceRecord] = []
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
                _validate_decoder_record(
                    parsed.record,
                    reference=reference,
                    repository=subject.repository,
                )
                if parsed.record.producer_sha256 != attempt.attestor_sha256:
                    _unavailable("original decoder producer does not match attestor")
                if type(parsed.value) is not dict or parsed.value.get("ref") != reference:
                    _unavailable("original decoder proof does not match V2 reference")
                if (
                    parsed.value.get("readable") is not True
                    or parsed.value.get("effectful") is not False
                ):
                    _unavailable("original decoder proof is not read-only")
                proofs.append(parsed.value)
                source_records += (parsed.record,)
                decoder_records.append(parsed.record)
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
        writer_digests = writer.source_record_digests
        dispatch_digest = sources[0].record.digest
        worker_digest = sources[1].record.digest
        process_digest = sources[2].record.digest
        decoder_digests = tuple(sorted(record.digest for record in decoder_records))
        decoder_bindings = decoder_digests if v2_refs else (dispatch_digest,)
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
        known_binding_digests = set(source_digests) | set(writer_digests)
        if any(
            not set(digests).issubset(known_binding_digests)
            for digests, _ in field_sources.values()
        ):
            _unavailable("legacy field binding names an unknown source record")
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


def assert_same_legacy_observation(
    before: ComponentObservation,
    after: ComponentObservation,
) -> None:
    if type(before) is not ComponentObservation or type(after) is not ComponentObservation:
        _drift("Legacy comparison received a non-component observation")
    if canonical_bytes(before.canonical()) != canonical_bytes(after.canonical()):
        _drift("Legacy complete observations differ")


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
            value = strict_json_loads(raw)
        except BootstrapError:
            raise
        except Exception as error:
            _unavailable(f"{role} command returned invalid strict JSON")
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
        _validate_github_snapshot(observation, repository)
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
        first_value = load_canonical_json(first.canonical_payload)
        rows = _paseo_inventory_rows(first_value)
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
            if inspect_value.get("repository") != repository:
                _unavailable("Paseo Worker inspect repository differs from query")
            if inspect_value.get("role") != "worker":
                _unavailable("Paseo Worker inspect role differs from query")
            state = inspect_value.get("status", inspect_value.get("Status"))
            if type(state) is not str or not state:
                _unavailable("Paseo Worker inspect state is absent")
            _worker_archive_evidence(inspect_value)
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
        rows = _process_inventory_rows(value)
        normalized: list[dict[str, object]] = []
        for row in rows:
            fixed_fields = {
                "ProcessId",
                "ParentProcessId",
                "CreationDate",
                "ExecutablePath",
                "CommandLine",
            }
            if set(row) not in (fixed_fields, fixed_fields | {"Name"}):
                _unavailable("process inventory row differs from the fixed CIM projection")
            _process_fields(row)
            _validate_process_visibility(row)
            item = {field: row[field] for field in fixed_fields}
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
        if command_line is None:
            executable = row.get("ExecutablePath", row.get("executable_path"))
            if executable is not None and self._repository_path is not None:
                try:
                    Path(executable).relative_to(self._repository_path)
                except ValueError:
                    pass
                else:
                    _unavailable(
                        "process under repository root has no command line"
                    )
            return False
        if type(command_line) is not str or not command_line:
            _unavailable("process command line is malformed")
        try:
            tokens = tuple(token.strip('"') for token in shlex.split(command_line, posix=False))
        except ValueError as error:
            _unavailable("process command line is not parseable")
            raise AssertionError from error
        token_set = set(tokens)
        has_repository = repository in token_set or f"--repo={repository}" in token_set
        has_action = bool(token_set & {"integrate", "deliver"})
        has_legacy = "v6.1" in token_set
        if not (has_repository and has_action and has_legacy):
            return False
        if self._repository_path is None:
            return False
        executable = row.get("ExecutablePath", row.get("executable_path"))
        if type(executable) is not str or not executable:
            _unavailable("possible V6.1 process has no executable path")
        executable_path = Path(executable)
        try:
            return str(executable_path.parent.parent.parent) == self._repository_path
        except Exception:
            return False


_HEX40 = re.compile(r"^[0-9a-f]{40}$")


def _github_connection(
    parent: Mapping[str, object],
    key: str,
    *,
    context: str,
    bounded_last: int | None = None,
) -> list[dict[str, object]]:
    connection = parent.get(key)
    if type(connection) is not dict:
        _unavailable(f"GitHub snapshot connection is missing: {context}.{key}")
    nodes = connection.get("nodes")
    if type(nodes) is not list:
        _unavailable(f"GitHub snapshot nodes are missing: {context}.{key}")
    page_info = connection.get("pageInfo")
    if type(page_info) is not dict or page_info.get("hasNextPage") is not False:
        _unavailable(f"GitHub snapshot pagination is incomplete: {context}.{key}")
    total_count = connection.get("totalCount")
    if type(total_count) is not int or total_count < 0:
        _unavailable(f"GitHub snapshot total count is invalid: {context}.{key}")
    expected_nodes = total_count if bounded_last is None else min(total_count, bounded_last)
    if len(nodes) != expected_nodes:
        _unavailable(f"GitHub snapshot total count is invalid: {context}.{key}")
    result: list[dict[str, object]] = []
    for node in nodes:
        if type(node) is not dict:
            _unavailable(f"GitHub snapshot contains a non-object node: {context}.{key}")
        result.append(dict(node))
    return result


def _validate_issue_node(node: Mapping[str, object], *, expected_label: str) -> None:
    if type(node.get("id")) is not str or not node["id"]:
        _unavailable("GitHub Issue node identity is missing")
    if type(node.get("number")) is not int or node["number"] <= 0:
        _unavailable("GitHub Issue number is invalid")
    if node.get("state") != "OPEN":
        _unavailable("GitHub Issue state is not exact OPEN")
    if type(node.get("title")) is not str or not node["title"]:
        _unavailable("GitHub Issue title is invalid")
    if type(node.get("body")) is not str:
        _unavailable("GitHub Issue body is invalid")
    if type(node.get("updatedAt")) is not str or not node["updatedAt"]:
        _unavailable("GitHub Issue update identity is invalid")
    labels = _github_connection(node, "labels", context=f"Issue #{node['number']}")
    label_names: list[str] = []
    for label in labels:
        name = label.get("name")
        if type(name) is not str or not name:
            _unavailable("GitHub Issue label identity is invalid")
        label_names.append(name)
    if expected_label not in label_names:
        _unavailable("GitHub Issue does not match its queried orchestration label")
    assignees = _github_connection(node, "assignees", context=f"Issue #{node['number']}")
    if any(type(item.get("login")) is not str or not item["login"] for item in assignees):
        _unavailable("GitHub Issue assignee identity is invalid")
    comments = _github_connection(node, "comments", context=f"Issue #{node['number']}")
    for comment in comments:
        if type(comment.get("databaseId")) is not int or comment["databaseId"] <= 0:
            _unavailable("GitHub Issue comment identity is invalid")
        if type(comment.get("body")) is not str:
            _unavailable("GitHub Issue comment body is invalid")
        for field in ("createdAt", "updatedAt"):
            if type(comment.get(field)) is not str or not comment[field]:
                _unavailable("GitHub Issue comment time identity is invalid")
        author = comment.get("author")
        if author is not None and (
            type(author) is not dict
            or type(author.get("login")) is not str
            or not author["login"]
        ):
            _unavailable("GitHub Issue comment author identity is invalid")


def _validate_pr_node(node: Mapping[str, object]) -> None:
    if type(node.get("id")) is not str or not node["id"]:
        _unavailable("GitHub PR node identity is missing")
    if type(node.get("number")) is not int or node["number"] <= 0:
        _unavailable("GitHub PR number is invalid")
    if node.get("state") != "OPEN":
        _unavailable("GitHub PR state is not exact OPEN")
    for field in ("title", "headRefName", "baseRefName", "updatedAt", "url"):
        if type(node.get(field)) is not str or not node[field]:
            _unavailable(f"GitHub PR identity field is invalid: {field}")
    if type(node.get("body")) is not str:
        _unavailable("GitHub PR body is invalid")
    if type(node.get("headRefOid")) is not str or _HEX40.fullmatch(node["headRefOid"]) is None:
        _unavailable("GitHub PR head OID is invalid")
    if type(node.get("isDraft")) is not bool:
        _unavailable("GitHub PR draft state is invalid")
    for field in ("mergedAt", "reviewDecision"):
        if node.get(field) is not None and type(node[field]) is not str:
            _unavailable(f"GitHub PR optional field is invalid: {field}")
    if type(node.get("mergeStateStatus")) is not str or not node["mergeStateStatus"]:
        _unavailable("GitHub PR merge state is invalid")
    reviews = _github_connection(node, "reviews", context=f"PR #{node['number']}")
    files = _github_connection(node, "files", context=f"PR #{node['number']}")
    commits = _github_connection(
        node,
        "commits",
        context=f"PR #{node['number']}",
        bounded_last=1,
    )
    if any(type(item.get("path")) is not str or not item["path"] for item in files):
        _unavailable("GitHub PR changed path identity is invalid")
    for review in reviews:
        if type(review.get("state")) is not str or not review["state"]:
            _unavailable("GitHub PR review state is invalid")
    for commit_node in commits:
        commit = commit_node.get("commit")
        if type(commit) is not dict:
            _unavailable("GitHub PR commit node is invalid")
        rollup = commit.get("statusCheckRollup")
        if rollup is not None:
            if type(rollup) is not dict:
                _unavailable("GitHub PR status rollup is invalid")
            _github_connection(
                rollup,
                "contexts",
                context=f"PR #{node['number']} status rollup",
            )


def _github_repository(value: object, expected_repository: str) -> dict[str, object]:
    if type(value) is not dict:
        _unavailable("GitHub snapshot is not an object")
    if "errors" in value and value["errors"] != []:
        _unavailable("GitHub snapshot contains GraphQL errors")
    data = value.get("data")
    if type(data) is not dict:
        _unavailable("GitHub snapshot data is missing")
    repository = data.get("repository")
    if type(repository) is not dict:
        _unavailable("GitHub repository snapshot is missing")
    if type(repository.get("id")) is not str or not repository["id"]:
        _unavailable("GitHub repository node identity is missing")
    if repository.get("nameWithOwner") != expected_repository:
        _unavailable("GitHub repository identity differs from subject")
    ref = repository.get("ref")
    oid = (ref.get("target") if type(ref) is dict else None)
    oid = oid.get("oid") if type(oid) is dict else None
    if type(oid) is not str or _HEX40.fullmatch(oid) is None:
        _unavailable("GitHub snapshot base OID is missing")
    issue_ids: set[str] = set()
    issue_numbers: set[int] = set()
    for key, label in (
        ("readyIssues", "orch:ready"),
        ("activeIssues", "orch:active"),
        ("blockedIssues", "orch:blocked"),
    ):
        for node in _github_connection(repository, key, context="repository"):
            _validate_issue_node(node, expected_label=label)
            if node["id"] in issue_ids or node["number"] in issue_numbers:
                _unavailable("GitHub Issue identity occurs in multiple connections")
            issue_ids.add(node["id"])
            issue_numbers.add(node["number"])
    pr_ids: set[str] = set()
    pr_numbers: set[int] = set()
    for node in _github_connection(repository, "pullRequests", context="repository"):
        _validate_pr_node(node)
        if node["id"] in pr_ids or node["number"] in pr_numbers:
            _unavailable("GitHub PR identity is duplicated")
        pr_ids.add(node["id"])
        pr_numbers.add(node["number"])
    return dict(repository)


def _plain_github_issue(node: Mapping[str, object]) -> dict[str, object]:
    return {
        **node,
        "labels": node["labels"]["nodes"],
        "assignees": node["assignees"]["nodes"],
        "comments": [
            {**comment, "id": comment["databaseId"]}
            for comment in node["comments"]["nodes"]
        ],
    }


def _plain_github_pr(node: Mapping[str, object]) -> dict[str, object]:
    commits = node["commits"]["nodes"]
    rollup = None
    if commits:
        rollup = commits[-1]["commit"].get("statusCheckRollup")
    contexts = [] if rollup is None else rollup["contexts"]["nodes"]
    checks: list[dict[str, object]] = []
    for context in contexts:
        if context.get("state"):
            state = str(context["state"]).upper()
            checks.append(
                {
                    "status": "COMPLETED" if state in {"SUCCESS", "FAILURE", "ERROR"} else "PENDING",
                    "conclusion": state,
                }
            )
        else:
            checks.append(dict(context))
    return {
        **node,
        "statusCheckRollup": checks,
        "reviews": node["reviews"]["nodes"],
        "changedPaths": [item["path"] for item in node["files"]["nodes"]],
        "filesTruncated": False,
    }


def _normalized_github_dispatches(
    value: object,
    expected_repository: str,
) -> tuple[dict[str, object], ...]:
    repository = _github_repository(value, expected_repository)
    issues = [
        _plain_github_issue(node)
        for key in ("readyIssues", "activeIssues", "blockedIssues")
        for node in repository[key]["nodes"]
    ]
    prs = [_plain_github_pr(node) for node in repository["pullRequests"]["nodes"]]
    try:
        normalized = orch_core.normalize_github_snapshot(expected_repository, issues, prs)
    except orch_core.PolicyError as error:
        _unavailable(f"GitHub Dispatch normalization failed: {error.code}")
        raise AssertionError from error
    dispatches: list[dict[str, object]] = []
    for issue in normalized["issues"]:
        dispatch = issue.get("dispatch")
        labels = issue.get("labels")
        requires_dispatch = type(labels) is list and bool(
            {"orch:active", "orch:blocked"} & set(labels)
        )
        if dispatch:
            if type(dispatch) is not dict:
                _unavailable("normalized GitHub Dispatch is malformed")
            dispatches.append(dict(dispatch))
        elif requires_dispatch:
            _unavailable("active GitHub Issue has no durable Dispatch record")
    return tuple(dispatches)


def _validate_github_snapshot(
    observation: SourceObservation,
    expected_repository: str,
) -> None:
    value = load_canonical_json(observation.canonical_payload)
    _github_repository(value, expected_repository)


_SNAPSHOT_QUERY = r"""
query($owner:String!,$name:String!,$branch:String!){
  repository(owner:$owner,name:$name){
    id nameWithOwner
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
  id number state title body updatedAt
  labels(first:100){totalCount pageInfo{hasNextPage} nodes{name}}
  milestone{title dueOn}
  assignees(first:20){totalCount pageInfo{hasNextPage} nodes{login}}
  comments(first:100){totalCount pageInfo{hasNextPage} nodes{databaseId body createdAt updatedAt author{login}}}
}
""".replace(
    "__ORCH_PR_FIELDS__",
    """id number state title body headRefName headRefOid baseRefName isDraft updatedAt mergedAt
mergeStateStatus reviewDecision url
reviews(first:100){totalCount pageInfo{hasNextPage} nodes{state body submittedAt author{login} commit{oid}}}
files(first:100){totalCount pageInfo{hasNextPage} nodes{path}}
commits(last:1){totalCount pageInfo{hasNextPage} nodes{commit{statusCheckRollup{contexts(first:100){totalCount pageInfo{hasNextPage} nodes{
  ... on CheckRun{status conclusion}
  ... on StatusContext{state}
}}}}}}
""",
)


_PROCESS_QUERY = (
    "Get-CimInstance Win32_Process | "
    "Select-Object ProcessId, ParentProcessId, CreationDate, Name, ExecutablePath, CommandLine | "
    "ConvertTo-Json -Compress"
)

_PRODUCTION_REPOSITORY_PATH = r"D:\Workstation\github-work-orchestrator"


def production_legacy_sources(
    *,
    command_runner: Callable[[tuple[str, ...]], bytes],
    producer_sha256: str,
) -> LegacySourceSet:
    return LegacySourceSet(
        dispatches=GitHubDispatchSnapshotReader(command_runner, producer_sha256),
        workers=PaseoWorkerInventoryReader(command_runner, producer_sha256),
        processes=CooperativeHostProcessReader(
            command_runner,
            producer_sha256,
            repository_path=_PRODUCTION_REPOSITORY_PATH,
        ),
        decoder=None,
    )
