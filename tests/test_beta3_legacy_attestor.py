from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
import sys

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
EXACT_SCRIPTS = REPO_ROOT / "skills" / "orchestrator" / "scripts"
SCRIPTS = REPO_ROOT / "scripts"
for path in (EXACT_SCRIPTS, SCRIPTS):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from gwo_v8._canonical import (  # noqa: E402
    canonical_bytes,
    digest_value,
    load_canonical_json,
)
from gwo_v8.cutover_guard import (  # noqa: E402
    CutoverSubject,
    LegacyReadback,
)
from beta3_bootstrap_model import (  # noqa: E402
    AttemptIdentity,
    BootstrapError,
    SourceObservation,
    SourceRecord,
    WriterAuthorityObservation,
)
from beta3_legacy_attestor import (  # noqa: E402
    CooperativeHostProcessReader,
    GitHubDispatchSnapshotReader,
    LegacyAttestor,
    LegacySourceSet,
    PaseoWorkerInventoryReader,
    production_legacy_sources,
)


@dataclass(frozen=True)
class FakeSource:
    records: tuple[dict[str, object], ...]
    complete: bool = True
    typed_readback: object | None = None
    role: str = "fixture.source"

    def read(self, repository: str) -> object:
        if self.typed_readback is not None:
            return self.typed_readback
        payload = canonical_bytes({"repository": repository, "records": self.records})
        record = SourceRecord(
            role=self.role,
            locator=f"fixture://{repository}",
            repository=repository,
            read_mode="FIXTURE",
            identity=(("fixture", "source"),),
            content_sha256=digest_value({"repository": repository, "records": self.records}),
            readback_digest=None,
            producer_sha256="8" * 64,
        )
        return SourceObservation(record, payload, self.complete)


@dataclass(frozen=True)
class FakeDecoder:
    proofs: tuple[tuple[str, dict[str, object]], ...]

    def read(self, reference: str) -> SourceObservation:
        values = dict(self.proofs)
        value = values[reference]
        payload = canonical_bytes(value)
        record = SourceRecord(
            role="fixture.decoder",
            locator=f"fixture://decoder/{reference}",
            repository="owner/repo",
            read_mode="FIXTURE",
            identity=(("reference", reference),),
            content_sha256=digest_value(value),
            readback_digest=None,
            producer_sha256="8" * 64,
        )
        return SourceObservation(record, payload, True)


def _sources(
    *,
    dispatches: tuple[dict[str, object], ...] = (),
    workers: tuple[dict[str, object], ...] = (),
    processes: tuple[dict[str, object], ...] = (),
    decoder: object | None = None,
) -> LegacySourceSet:
    return LegacySourceSet(
        dispatches=FakeSource(dispatches, role="fixture.dispatches"),
        workers=FakeSource(workers, role="fixture.workers"),
        processes=FakeSource(processes, role="fixture.processes"),
        decoder=decoder,
    )


def _subject() -> CutoverSubject:
    return CutoverSubject(
        repository="owner/repo",
        control_branch="gwo-control",
        target_branch="main",
        source_writer_generation="v6.1",
        target_writer_generation="v8",
        store_generation="store:v8:test",
        source_commit="a" * 40,
        source_tree_digest="b" * 64,
        production_entry_refs=("entry:main",),
    )


@pytest.fixture
def subject() -> CutoverSubject:
    return _subject()


@pytest.fixture
def attempt(subject: CutoverSubject) -> AttemptIdentity:
    return AttemptIdentity.create(
        run_id="beta3-prod-001",
        repository=subject.repository,
        evidence_root=r"D:\evidence",
        cutover_subject_digest=digest_value(subject.canonical()),
        runner_sha256="7" * 64,
        attestor_sha256="8" * 64,
        nonce_factory=lambda size: "9" * (size * 2),
    )


@pytest.fixture
def writer() -> WriterAuthorityObservation:
    return WriterAuthorityObservation(
        writer_generation="v6.1",
        record_id="writer-record:test",
        authority_state="authoritative",
        activation_id=None,
        legacy_stopped=False,
        source_record_digests=("a" * 64,),
    )


@pytest.fixture
def valid_empty_sources() -> LegacySourceSet:
    return LegacySourceSet(
        dispatches=FakeSource(records=(), role="fixture.dispatches"),
        workers=FakeSource(records=(), role="fixture.workers"),
        processes=FakeSource(records=(), role="fixture.processes"),
        decoder=None,
    )


@pytest.fixture
def valid_sources(valid_empty_sources: LegacySourceSet) -> LegacySourceSet:
    return valid_empty_sources


def replace_source(
    sources: LegacySourceSet, name: str, *, complete: bool
) -> LegacySourceSet:
    return replace(sources, **{name: replace(getattr(sources, name), complete=complete)})


def test_legacy_rejects_forged_typed_readback_without_source_records(
    subject: CutoverSubject,
    attempt: AttemptIdentity,
    writer: WriterAuthorityObservation,
) -> None:
    attestor = LegacyAttestor(
        LegacySourceSet(
            dispatches=FakeSource(records=(), role="fixture.dispatches"),
            workers=FakeSource(records=(), role="fixture.workers"),
            processes=FakeSource(
                records=(),
                role="fixture.processes",
                typed_readback=LegacyReadback(
                    repository=subject.repository,
                    writer_generation="v6.1",
                    authority_state="authoritative_quiescent",
                    active_dispatches=(),
                    active_workers=(),
                    integration_lease_owner=None,
                    v2_execution_refs=(),
                    v2_execution_state="none",
                    original_decoder_readable=True,
                    durable_state_digest="1" * 64,
                ),
            ),
            decoder=None,
        )
    )
    with pytest.raises(BootstrapError) as error:
        attestor.observe(subject=subject, attempt=attempt, writer=writer)
    assert error.value.code == "LEGACY_SOURCE_UNAVAILABLE"


@pytest.mark.parametrize("source", ("dispatches", "workers", "processes"))
def test_legacy_requires_complete_enumeration_and_identity(
    source: str,
    valid_sources: LegacySourceSet,
    subject: CutoverSubject,
    attempt: AttemptIdentity,
    writer: WriterAuthorityObservation,
) -> None:
    broken = replace_source(valid_sources, source, complete=False)
    with pytest.raises(BootstrapError) as error:
        LegacyAttestor(broken).observe(
            subject=subject,
            attempt=attempt,
            writer=writer,
        )
    assert error.value.code == "LEGACY_SOURCE_UNAVAILABLE"


def test_empty_complete_legacy_observation_is_authoritative_and_decoder_is_vacuous(
    subject: CutoverSubject,
    attempt: AttemptIdentity,
    writer: WriterAuthorityObservation,
    valid_empty_sources: LegacySourceSet,
) -> None:
    observed = LegacyAttestor(valid_empty_sources).observe(
        subject=subject,
        attempt=attempt,
        writer=writer,
    )
    readback = dict(observed.readbacks)["legacy"]
    assert type(readback) is LegacyReadback
    assert readback.authority_state == "authoritative_quiescent"
    assert readback.v2_execution_refs == ()
    assert readback.v2_execution_state == "none"
    assert readback.original_decoder_readable is True


def test_active_legacy_fact_is_not_silently_erased(
    subject: CutoverSubject,
    attempt: AttemptIdentity,
    writer: WriterAuthorityObservation,
) -> None:
    reference = "v2:17"
    sources = _sources(
        dispatches=(
            {"id": "17", "status": "running", "v2_execution_ref": reference},
        ),
        decoder=FakeDecoder(
            proofs=(
                (
                    reference,
                    {
                        "effectful": False,
                        "readable": True,
                        "ref": reference,
                        "state": "quiescent_read_only",
                    },
                ),
            )
        ),
    )
    observed = LegacyAttestor(sources).observe(
        subject=subject,
        attempt=attempt,
        writer=writer,
    )
    readback = dict(observed.readbacks)["legacy"]
    assert readback.authority_state == "active"
    assert readback.active_dispatches == ("dispatch:17",)
    assert readback.v2_execution_refs == (reference,)


def test_nonempty_v2_refs_without_original_decoder_proof_are_unavailable(
    subject: CutoverSubject,
    attempt: AttemptIdentity,
    writer: WriterAuthorityObservation,
) -> None:
    with pytest.raises(BootstrapError) as error:
        LegacyAttestor(
            _sources(
                dispatches=(
                    {"id": "17", "status": "running", "v2_execution_ref": "v2:17"},
                )
            )
        ).observe(subject=subject, attempt=attempt, writer=writer)
    assert error.value.code == "LEGACY_SOURCE_UNAVAILABLE"


def test_active_worker_requires_exact_inspect_identity(
    subject: CutoverSubject,
    attempt: AttemptIdentity,
    writer: WriterAuthorityObservation,
) -> None:
    sources = _sources(
        workers=(
            {
                "id": "worker-1",
                "inspect": {
                    "archived": False,
                    "id": "worker-1",
                    "repository": subject.repository,
                    "role": "worker",
                    "status": "running",
                },
            },
        )
    )
    observed = LegacyAttestor(sources).observe(
        subject=subject,
        attempt=attempt,
        writer=writer,
    )
    assert dict(observed.readbacks)["legacy"].active_workers == ("worker-1",)


def test_archived_worker_is_excluded_after_exact_inspect_readback(
    subject: CutoverSubject,
    attempt: AttemptIdentity,
    writer: WriterAuthorityObservation,
):
    observed = LegacyAttestor(
        _sources(
            workers=(
                {
                    "id": "worker-1",
                    "inspect": {
                        "archivedAt": "2026-08-10T00:00:00Z",
                        "id": "worker-1",
                        "repository": subject.repository,
                        "role": "worker",
                        "status": "running",
                    },
                },
            )
        )
    ).observe(subject=subject, attempt=attempt, writer=writer)
    assert dict(observed.readbacks)["legacy"].active_workers == ()


def test_multiple_integration_lease_owners_are_unavailable(
    subject: CutoverSubject,
    attempt: AttemptIdentity,
    writer: WriterAuthorityObservation,
) -> None:
    processes = tuple(
        {
            "CommandLine": "legacy integrate owner/repo",
            "CreationDate": f"2026-08-10T00:00:0{index}Z",
            "ExecutablePath": r"C:\Python313\python.exe",
            "ParentProcessId": 1,
            "ProcessId": index,
            "integration_lease": True,
        }
        for index in (1, 2)
    )
    with pytest.raises(BootstrapError) as error:
        LegacyAttestor(_sources(processes=processes)).observe(
            subject=subject,
            attempt=attempt,
            writer=writer,
        )
    assert error.value.code == "LEGACY_SOURCE_UNAVAILABLE"


def test_legacy_digest_contains_source_record_identity_not_only_projection(
    subject: CutoverSubject,
    attempt: AttemptIdentity,
    writer: WriterAuthorityObservation,
) -> None:
    first = LegacyAttestor(_sources()).observe(
        subject=subject,
        attempt=attempt,
        writer=writer,
    )
    second_sources = LegacySourceSet(
        dispatches=FakeSource(records=(), role="fixture.dispatches.changed"),
        workers=FakeSource(records=(), role="fixture.workers"),
        processes=FakeSource(records=(), role="fixture.processes"),
        decoder=None,
    )
    second = LegacyAttestor(second_sources).observe(
        subject=subject,
        attempt=attempt,
        writer=writer,
    )
    first_readback = dict(first.readbacks)["legacy"]
    second_readback = dict(second.readbacks)["legacy"]
    assert first_readback.active_dispatches == second_readback.active_dispatches
    assert first_readback.durable_state_digest != second_readback.durable_state_digest
    assert first.source_records != second.source_records


def test_legacy_rejects_unsafe_source_capability():
    class UnsafeSource(FakeSource):
        def start(self) -> None:
            raise AssertionError("must not be called")

    with pytest.raises(BootstrapError) as error:
        LegacyAttestor(
            LegacySourceSet(
                dispatches=UnsafeSource(records=()),
                workers=FakeSource(records=(), role="fixture.workers"),
                processes=FakeSource(records=(), role="fixture.processes"),
            )
        )
    assert error.value.code == "UNSAFE_SOURCE_CAPABILITY"


def _github_payload(*, has_next: bool = False, base_oid: str | None = "a" * 40):
    repository: dict[str, object] = {
        "readyIssues": {"pageInfo": {"hasNextPage": has_next}, "nodes": []},
        "activeIssues": {"pageInfo": {"hasNextPage": has_next}, "nodes": []},
        "blockedIssues": {"pageInfo": {"hasNextPage": has_next}, "nodes": []},
        "pullRequests": {"pageInfo": {"hasNextPage": has_next}, "nodes": []},
    }
    if base_oid is not None:
        repository["ref"] = {"target": {"oid": base_oid}}
    return {"data": {"repository": repository}}


def test_github_dispatch_reader_keeps_complete_response_digest_and_read_only_command():
    calls: list[tuple[str, ...]] = []
    payload = _github_payload()

    def run(command: tuple[str, ...]) -> bytes:
        calls.append(command)
        return canonical_bytes(payload)

    reader = GitHubDispatchSnapshotReader(run, "8" * 64)
    observed = reader.read("owner/repo")
    assert len(calls) == 1
    assert calls[0][:3] == ("gh", "api", "graphql")
    assert calls[0][-1] == "branch=refs/heads/main"
    assert observed.complete is True
    assert observed.record.read_mode == "COMPLETE_DOUBLE_READ"
    assert observed.record.identity == (("observation_digest", observed.record.content_sha256),)


def test_github_snapshot_dispatches_are_normalized_into_active_references(
    subject: CutoverSubject,
    attempt: AttemptIdentity,
    writer: WriterAuthorityObservation,
):
    payload = _github_payload()
    payload["data"]["repository"]["activeIssues"]["nodes"] = [
        {"dispatch": {"id": "17", "status": "running"}}
    ]

    class GitHubSource:
        def read(self, repository: str) -> SourceObservation:
            value = canonical_bytes(payload)
            record = SourceRecord(
                role="fixture.dispatches",
                locator="fixture://github",
                repository=repository,
                read_mode="FIXTURE",
                identity=(("observation_digest", digest_value(payload)),),
                content_sha256=digest_value(payload),
                readback_digest=None,
                producer_sha256="8" * 64,
            )
            return SourceObservation(record, value, True)

    sources = LegacySourceSet(
        dispatches=GitHubSource(),
        workers=FakeSource(records=(), role="fixture.workers"),
        processes=FakeSource(records=(), role="fixture.processes"),
    )
    observed = LegacyAttestor(sources).observe(
        subject=subject,
        attempt=attempt,
        writer=writer,
    )
    assert dict(observed.readbacks)["legacy"].active_dispatches == ("dispatch:17",)


@pytest.mark.parametrize(
    "payload",
    (
        _github_payload(has_next=True),
        _github_payload(base_oid=None),
    ),
)
def test_github_dispatch_reader_rejects_incomplete_or_unbound_snapshot(payload):
    def run(command: tuple[str, ...]) -> bytes:
        return canonical_bytes(payload)

    with pytest.raises(BootstrapError) as error:
        GitHubDispatchSnapshotReader(run, "8" * 64).read("owner/repo")
    assert error.value.code == "LEGACY_SOURCE_UNAVAILABLE"


def test_paseo_worker_reader_inspects_every_unique_identity():
    calls: list[tuple[str, ...]] = []
    inventory = [
        {"Id": "worker-1", "Status": "running"},
        {"Id": "worker-2", "Status": "closed"},
    ]
    inspections = {
        "worker-1": {
            "id": "worker-1",
            "repository": "owner/repo",
            "role": "worker",
            "status": "running",
            "archived": False,
        },
        "worker-2": {
            "id": "worker-2",
            "repository": "owner/repo",
            "role": "worker",
            "status": "closed",
            "archived": False,
        },
    }

    def run(command: tuple[str, ...]) -> bytes:
        calls.append(command)
        if command[1] == "ls":
            return canonical_bytes(inventory)
        return canonical_bytes(inspections[command[2]])

    observed = PaseoWorkerInventoryReader(run, "8" * 64).read("owner/repo")
    assert calls[0] == (
        "paseo",
        "ls",
        "--global",
        "--all",
        "--label",
        "orch.repository=owner/repo",
        "--label",
        "orch.role=worker",
        "--json",
    )
    assert calls[1:] == [
        ("paseo", "inspect", "worker-1", "--json"),
        ("paseo", "inspect", "worker-2", "--json"),
    ]
    assert observed.record.identity == (("observation_digest", observed.record.content_sha256),)


def test_paseo_worker_reader_rejects_inspect_identity_substitution():
    def run(command: tuple[str, ...]) -> bytes:
        if command[1] == "ls":
            return canonical_bytes([{"Id": "worker-1", "Status": "running"}])
        return canonical_bytes(
            {
                "id": "worker-other",
                "repository": "owner/repo",
                "role": "worker",
                "status": "running",
                "archived": False,
            }
        )

    with pytest.raises(BootstrapError) as error:
        PaseoWorkerInventoryReader(run, "8" * 64).read("owner/repo")
    assert error.value.code == "LEGACY_SOURCE_UNAVAILABLE"


def test_paseo_worker_reader_accepts_one_json_object_without_dropping_it():
    calls: list[tuple[str, ...]] = []

    def run(command: tuple[str, ...]) -> bytes:
        calls.append(command)
        if command[1] == "ls":
            return canonical_bytes({"Id": "worker-1", "Status": "running"})
        return canonical_bytes(
            {
                "id": "worker-1",
                "repository": "owner/repo",
                "role": "worker",
                "status": "running",
                "archived": False,
            }
        )

    observed = PaseoWorkerInventoryReader(run, "8" * 64).read("owner/repo")
    value = load_canonical_json(observed.canonical_payload)
    assert len(value["workers"]) == 1
    assert calls[1] == ("paseo", "inspect", "worker-1", "--json")


def test_process_reader_requires_complete_cim_fields_and_marks_exact_lease_match():
    rows = [
        {
            "ProcessId": 17,
            "ParentProcessId": 1,
            "CreationDate": "20260810000000.000000+000",
            "ExecutablePath": r"D:\repo\.venv\Scripts\python.exe",
            "CommandLine": "python -m orch integrate owner/repo v6.1",
        },
    ]

    def run(command: tuple[str, ...]) -> bytes:
        return canonical_bytes(rows)

    reader = CooperativeHostProcessReader(
        run,
        "8" * 64,
        repository_path=r"D:\repo",
    )
    observed = reader.read("owner/repo")
    value = load_canonical_json(observed.canonical_payload)
    assert value[0]["integration_lease"] is True


def test_process_reader_accepts_one_cim_object_without_dropping_it():
    row = {
        "ProcessId": 17,
        "ParentProcessId": 1,
        "CreationDate": "20260810000000.000000+000",
        "ExecutablePath": r"D:\repo\.venv\Scripts\python.exe",
        "CommandLine": "python -m orch integrate owner/repo v6.1",
    }
    observed = CooperativeHostProcessReader(
        lambda command: canonical_bytes(row),
        "8" * 64,
        repository_path=r"D:\repo",
    ).read("owner/repo")
    assert len(load_canonical_json(observed.canonical_payload)) == 1


def test_process_reader_rejects_unparseable_complete_inventory():
    with pytest.raises(BootstrapError) as error:
        CooperativeHostProcessReader(
            lambda command: canonical_bytes({"unexpected": []}),
            "8" * 64,
        ).read("owner/repo")
    assert error.value.code == "LEGACY_SOURCE_UNAVAILABLE"


@pytest.mark.parametrize("field", ("ProcessId", "ParentProcessId", "CreationDate", "ExecutablePath", "CommandLine"))
def test_process_reader_rejects_malformed_cim_field(field: str):
    row: dict[str, object] = {
        "ProcessId": 17,
        "ParentProcessId": 1,
        "CreationDate": "20260810000000.000000+000",
        "ExecutablePath": r"D:\repo\.venv\Scripts\python.exe",
        "CommandLine": "python -m orch integrate owner/repo v6.1",
    }
    row[field] = None if field in {"ExecutablePath", "CommandLine", "CreationDate"} else "17"
    with pytest.raises(BootstrapError) as error:
        CooperativeHostProcessReader(
            lambda command: canonical_bytes([row]),
            "8" * 64,
        ).read("owner/repo")
    assert error.value.code == "LEGACY_SOURCE_UNAVAILABLE"


def test_production_legacy_sources_have_three_narrow_readers():
    sources = production_legacy_sources(command_runner=lambda command: b"[]", producer_sha256="8" * 64)
    assert type(sources.dispatches) is GitHubDispatchSnapshotReader
    assert type(sources.workers) is PaseoWorkerInventoryReader
    assert type(sources.processes) is CooperativeHostProcessReader
    assert sources.decoder is None


def test_github_dispatch_reader_rejects_nested_paginated_connection():
    payload = _github_payload()
    payload["data"]["repository"]["readyIssues"]["nodes"] = [
        {
            "labels": {"pageInfo": {"hasNextPage": True}, "nodes": []},
        }
    ]

    with pytest.raises(BootstrapError) as error:
        GitHubDispatchSnapshotReader(
            lambda command: canonical_bytes(payload),
            "8" * 64,
        ).read("owner/repo")
    assert error.value.code == "LEGACY_SOURCE_UNAVAILABLE"


def test_legacy_rejects_worker_repository_or_role_substitution(
    subject: CutoverSubject,
    attempt: AttemptIdentity,
    writer: WriterAuthorityObservation,
):
    with pytest.raises(BootstrapError) as error:
        LegacyAttestor(
            _sources(
                workers=(
                    {
                        "id": "worker-1",
                        "inspect": {
                            "archived": False,
                            "id": "worker-1",
                            "repository": "other/repo",
                            "role": "worker",
                            "status": "running",
                        },
                    },
                )
            )
        ).observe(subject=subject, attempt=attempt, writer=writer)
    assert error.value.code == "LEGACY_SOURCE_UNAVAILABLE"


def test_legacy_rejects_source_record_producer_substitution(
    subject: CutoverSubject,
    attempt: AttemptIdentity,
    writer: WriterAuthorityObservation,
):
    broken = _sources()
    broken_source = replace(
        broken.dispatches,
        role="fixture.dispatches",
    )
    original_read = broken_source.read

    class MismatchedSource:
        def read(self, repository: str) -> SourceObservation:
            observed = original_read(repository)
            return SourceObservation(
                replace(observed.record, producer_sha256="f" * 64),
                observed.canonical_payload,
                observed.complete,
            )

    with pytest.raises(BootstrapError) as error:
        LegacyAttestor(replace(broken, dispatches=MismatchedSource())).observe(
            subject=subject,
            attempt=attempt,
            writer=writer,
        )
    assert error.value.code == "LEGACY_SOURCE_UNAVAILABLE"


def test_legacy_requires_decoder_for_nonempty_v2_refs_even_when_dispatch_is_terminal(
    subject: CutoverSubject,
    attempt: AttemptIdentity,
    writer: WriterAuthorityObservation,
):
    with pytest.raises(BootstrapError) as error:
        LegacyAttestor(
            _sources(dispatches=({"id": "17", "status": "merged", "v2_execution_ref": "v2:17"},))
        ).observe(subject=subject, attempt=attempt, writer=writer)
    assert error.value.code == "LEGACY_SOURCE_UNAVAILABLE"


def test_legacy_readback_digest_is_exact_main_body_digest(
    subject: CutoverSubject,
    attempt: AttemptIdentity,
    writer: WriterAuthorityObservation,
):
    observed = LegacyAttestor(_sources()).observe(
        subject=subject,
        attempt=attempt,
        writer=writer,
    )
    readback = dict(observed.readbacks)["legacy"]
    body = readback.canonical()
    digest = body.pop("readback_digest")
    assert digest == digest_value(body)
    assert readback.durable_state_digest != digest_value(readback.canonical())
    assert {binding.target for binding in observed.field_bindings} == {
        f"legacy.{field}" for field in readback.canonical()
    }


def test_legacy_field_bindings_name_exact_underlying_observations(
    subject: CutoverSubject,
    attempt: AttemptIdentity,
    writer: WriterAuthorityObservation,
):
    observed = LegacyAttestor(_sources()).observe(
        subject=subject,
        attempt=attempt,
        writer=writer,
    )
    by_target = {binding.target: binding for binding in observed.field_bindings}
    by_role = {record.role: record.digest for record in observed.source_records}
    assert by_target["legacy.active_dispatches"].source_record_digests == (
        by_role["fixture.dispatches"],
    )
    assert by_target["legacy.active_workers"].source_record_digests == (
        by_role["fixture.workers"],
    )
    assert by_target["legacy.integration_lease_owner"].source_record_digests == (
        by_role["fixture.processes"],
    )
    assert by_target["legacy.v2_execution_state"].derivation == "vacuous_empty_reference_set"
    assert by_target["legacy.readback_digest"].derivation == "canonical_digest"
