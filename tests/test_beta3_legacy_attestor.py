from __future__ import annotations

from dataclasses import dataclass, replace
import json
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
    CanonicalJsonError,
    canonical_bytes,
    digest_value,
    load_canonical_json,
    strict_json_loads,
)
from gwo_v8.cutover_guard import (  # noqa: E402
    CutoverSubject,
    LegacyReadback,
)
import orch_core  # noqa: E402
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
    assert_same_legacy_observation,
    production_legacy_sources,
)


@dataclass(frozen=True)
class FakeSource:
    records: tuple[dict[str, object], ...]
    complete: bool = True
    typed_readback: object | None = None
    role: str = "fixture.source"
    read_mode: str = "FIXTURE"
    identity: tuple[tuple[str, str], ...] = (("fixture", "source"),)

    def read(self, repository: str) -> object:
        if self.typed_readback is not None:
            return self.typed_readback
        payload = canonical_bytes({"repository": repository, "records": self.records})
        record = SourceRecord(
            role=self.role,
            locator=f"fixture://{repository}",
            repository=repository,
            read_mode=self.read_mode,
            identity=self.identity,
            content_sha256=digest_value({"repository": repository, "records": self.records}),
            readback_digest=None,
            producer_sha256="8" * 64,
        )
        return SourceObservation(record, payload, self.complete)


@dataclass(frozen=True)
class FakeDecoder:
    proofs: tuple[tuple[str, dict[str, object]], ...]
    role: str = "fixture.decoder"
    repository: str = "owner/repo"
    read_mode: str = "FIXTURE"
    live_identity: bool = False

    def read(self, reference: str) -> SourceObservation:
        values = dict(self.proofs)
        value = values[reference]
        payload = canonical_bytes(value)
        identity = (("reference", reference),)
        if self.live_identity:
            identity = (
                ("observation_digest", digest_value(value)),
                ("reference", reference),
            )
        record = SourceRecord(
            role=self.role,
            locator=f"fixture://decoder/{reference}",
            repository=self.repository,
            read_mode=self.read_mode,
            identity=identity,
            content_sha256=digest_value(value),
            readback_digest=None,
            producer_sha256="8" * 64,
        )
        return SourceObservation(record, payload, True)


@dataclass(frozen=True)
class PayloadSource:
    value: object
    role: str

    def read(self, repository: str) -> SourceObservation:
        payload = canonical_bytes(self.value)
        record = SourceRecord(
            role=self.role,
            locator=f"fixture://{repository}/payload",
            repository=repository,
            read_mode="FIXTURE",
            identity=(("fixture", "payload"),),
            content_sha256=digest_value(self.value),
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


def test_running_worker_without_explicit_archive_evidence_is_unavailable(
    subject: CutoverSubject,
    attempt: AttemptIdentity,
    writer: WriterAuthorityObservation,
) -> None:
    sources = _sources(
        workers=(
            {
                "id": "worker-1",
                "inspect": {
                    "id": "worker-1",
                    "repository": subject.repository,
                    "role": "worker",
                    "status": "running",
                },
            },
        )
    )
    with pytest.raises(BootstrapError) as error:
        LegacyAttestor(sources).observe(
            subject=subject,
            attempt=attempt,
            writer=writer,
        )
    assert error.value.code == "LEGACY_SOURCE_UNAVAILABLE"


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


def test_integration_lease_owner_uses_exact_process_creation_identity(
    subject: CutoverSubject,
    attempt: AttemptIdentity,
    writer: WriterAuthorityObservation,
) -> None:
    process = {
        "ProcessId": 17,
        "ParentProcessId": 1,
        "CreationDate": "20260810000000.000000+000",
        "ExecutablePath": r"D:\repo\.venv\Scripts\python.exe",
        "CommandLine": "python -m orch integrate owner/repo v6.1",
        "identity": "forged-owner",
        "integration_lease": True,
    }
    observed = LegacyAttestor(_sources(processes=(process,))).observe(
        subject=subject,
        attempt=attempt,
        writer=writer,
    )
    assert dict(observed.readbacks)["legacy"].integration_lease_owner == (
        "process:17:20260810000000.000000+000"
    )


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
        dispatches=FakeSource(
            records=(),
            role="fixture.dispatches",
            identity=(("fixture", "changed"),),
        ),
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


def test_legacy_rejects_missing_writer_source_record_identity(
    subject: CutoverSubject,
    attempt: AttemptIdentity,
    writer: WriterAuthorityObservation,
) -> None:
    with pytest.raises(BootstrapError) as error:
        LegacyAttestor(_sources()).observe(
            subject=subject,
            attempt=attempt,
            writer=replace(writer, source_record_digests=()),
        )
    assert error.value.code == "LEGACY_SOURCE_UNAVAILABLE"


@pytest.mark.parametrize("source_name", ("dispatches", "workers", "processes"))
def test_legacy_rejects_unknown_source_role(
    source_name: str,
    subject: CutoverSubject,
    attempt: AttemptIdentity,
    writer: WriterAuthorityObservation,
) -> None:
    sources = _sources()
    broken = replace(getattr(sources, source_name), role="fixture.unknown")
    with pytest.raises(BootstrapError) as error:
        LegacyAttestor(replace(sources, **{source_name: broken})).observe(
            subject=subject,
            attempt=attempt,
            writer=writer,
        )
    assert error.value.code == "LEGACY_SOURCE_UNAVAILABLE"


@pytest.mark.parametrize("mutation", ("read_mode", "identity"))
def test_legacy_requires_live_complete_double_read_observation_identity(
    mutation: str,
    subject: CutoverSubject,
    attempt: AttemptIdentity,
    writer: WriterAuthorityObservation,
) -> None:
    reader = GitHubDispatchSnapshotReader(
        lambda command: canonical_bytes(_github_payload()),
        "8" * 64,
    )

    class MutatedLiveSource:
        def read(self, repository: str) -> SourceObservation:
            observed = reader.read(repository)
            record = observed.record
            if mutation == "read_mode":
                record = replace(record, read_mode="FIXTURE")
            else:
                record = replace(
                    record,
                    identity=(("observation_digest", "f" * 64),),
                )
            return SourceObservation(record, observed.canonical_payload, True)

    with pytest.raises(BootstrapError) as error:
        LegacyAttestor(
            replace(_sources(), dispatches=MutatedLiveSource())
        ).observe(subject=subject, attempt=attempt, writer=writer)
    assert error.value.code == "LEGACY_SOURCE_UNAVAILABLE"


@pytest.mark.parametrize(
    "decoder",
    (
        FakeDecoder(
            proofs=(("v2:17", {"effectful": False, "readable": True, "ref": "v2:17", "state": "terminal"}),),
            repository="other/repo",
        ),
        FakeDecoder(
            proofs=(("v2:17", {"effectful": False, "readable": True, "ref": "v2:17", "state": "terminal"}),),
            role="fixture.unknown-decoder",
        ),
        FakeDecoder(
            proofs=(("v2:17", {"effectful": False, "readable": True, "ref": "v2:17", "state": "terminal"}),),
            role="legacy.decoder",
            read_mode="FIXTURE",
            live_identity=True,
        ),
        FakeDecoder(
            proofs=(("v2:17", {"effectful": False, "readable": True, "ref": "v2:17", "state": "terminal"}),),
            role="legacy.decoder",
            read_mode="COMPLETE_DOUBLE_READ",
            live_identity=False,
        ),
        FakeDecoder(
            proofs=(("v2:17", {"readable": True, "ref": "v2:17", "state": "terminal"}),),
        ),
    ),
    ids=("repository", "role", "mode", "identity", "effectful-proof"),
)
def test_legacy_rejects_substituted_or_effectful_decoder_proof(
    decoder: FakeDecoder,
    subject: CutoverSubject,
    attempt: AttemptIdentity,
    writer: WriterAuthorityObservation,
) -> None:
    sources = _sources(
        dispatches=({"id": "17", "status": "running", "v2_execution_ref": "v2:17"},),
        decoder=decoder,
    )
    with pytest.raises(BootstrapError) as error:
        LegacyAttestor(sources).observe(
            subject=subject,
            attempt=attempt,
            writer=writer,
        )
    assert error.value.code == "LEGACY_SOURCE_UNAVAILABLE"


def test_decoder_binding_names_exact_included_decoder_record(
    subject: CutoverSubject,
    attempt: AttemptIdentity,
    writer: WriterAuthorityObservation,
) -> None:
    decoder = FakeDecoder(
        proofs=(
            (
                "v2:17",
                {
                    "effectful": False,
                    "readable": True,
                    "ref": "v2:17",
                    "state": "terminal",
                },
            ),
        )
    )
    observed = LegacyAttestor(
        _sources(
            dispatches=({"id": "17", "status": "running", "v2_execution_ref": "v2:17"},),
            decoder=decoder,
        )
    ).observe(subject=subject, attempt=attempt, writer=writer)
    decoder_record = next(
        record for record in observed.source_records if record.role == "fixture.decoder"
    )
    bindings = {binding.target: binding for binding in observed.field_bindings}
    assert bindings["legacy.v2_execution_state"].source_record_digests == (
        decoder_record.digest,
    )
    assert bindings["legacy.original_decoder_readable"].source_record_digests == (
        decoder_record.digest,
    )


def test_legacy_accepts_exact_live_decoder_identity_and_read_only_proof(
    subject: CutoverSubject,
    attempt: AttemptIdentity,
    writer: WriterAuthorityObservation,
) -> None:
    decoder = FakeDecoder(
        proofs=(
            (
                "v2:17",
                {
                    "effectful": False,
                    "readable": True,
                    "ref": "v2:17",
                    "state": "quiescent_read_only",
                },
            ),
        ),
        role="legacy.decoder",
        read_mode="COMPLETE_DOUBLE_READ",
        live_identity=True,
    )
    observed = LegacyAttestor(
        _sources(
            dispatches=(
                {"id": "17", "status": "running", "v2_execution_ref": "v2:17"},
            ),
            decoder=decoder,
        )
    ).observe(subject=subject, attempt=attempt, writer=writer)
    readback = dict(observed.readbacks)["legacy"]
    assert readback.v2_execution_state == "quiescent_read_only"
    assert any(record.role == "legacy.decoder" for record in observed.source_records)


@pytest.mark.parametrize(
    "drift_kind",
    (
        "observation_identity",
        "process_creation",
        "writer_source_identity",
        "writer_record_identity",
    ),
)
def test_legacy_comparison_rejects_complete_identity_drift(
    drift_kind: str,
    subject: CutoverSubject,
    attempt: AttemptIdentity,
    writer: WriterAuthorityObservation,
) -> None:
    process = {
        "ProcessId": 17,
        "ParentProcessId": 1,
        "CreationDate": "20260810000000.000000+000",
        "ExecutablePath": r"D:\repo\.venv\Scripts\python.exe",
        "CommandLine": "python -m orch reconcile owner/repo v6.1",
        "integration_lease": False,
    }
    before_sources = _sources(processes=(process,))
    after_sources = before_sources
    after_writer = writer
    if drift_kind == "observation_identity":
        after_sources = replace(
            before_sources,
            dispatches=replace(
                before_sources.dispatches,
                identity=(("fixture", "substituted"),),
            ),
        )
    elif drift_kind == "process_creation":
        after_sources = _sources(
            processes=({**process, "CreationDate": "20260810000001.000000+000"},)
        )
    elif drift_kind == "writer_source_identity":
        after_writer = replace(writer, source_record_digests=("b" * 64,))
    else:
        after_writer = replace(writer, record_id="writer-record:substituted")

    before = LegacyAttestor(before_sources).observe(
        subject=subject,
        attempt=attempt,
        writer=writer,
    )
    after = LegacyAttestor(after_sources).observe(
        subject=subject,
        attempt=attempt,
        writer=after_writer,
    )
    before_readback = dict(before.readbacks)["legacy"]
    after_readback = dict(after.readbacks)["legacy"]
    assert before_readback.active_dispatches == after_readback.active_dispatches
    assert before_readback.integration_lease_owner == after_readback.integration_lease_owner
    with pytest.raises(BootstrapError) as error:
        assert_same_legacy_observation(before, after)
    assert error.value.code == "LIVE_INPUT_DRIFT"


def test_legacy_comparison_accepts_exact_same_complete_observation(
    subject: CutoverSubject,
    attempt: AttemptIdentity,
    writer: WriterAuthorityObservation,
) -> None:
    observed = LegacyAttestor(_sources()).observe(
        subject=subject,
        attempt=attempt,
        writer=writer,
    )
    assert_same_legacy_observation(observed, observed)


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


def _github_connection(
    nodes: list[dict[str, object]], *, has_next: bool = False
) -> dict[str, object]:
    return {
        "totalCount": len(nodes),
        "pageInfo": {"hasNextPage": has_next},
        "nodes": nodes,
    }


def _github_issue_node(
    *,
    number: int,
    label: str,
    dispatch: dict[str, object] | None,
) -> dict[str, object]:
    comments: list[dict[str, object]] = []
    if dispatch is not None:
        comments.append(
            {
                "databaseId": number * 10,
                "body": orch_core.render_issue_record({"dispatch": dispatch}),
                "createdAt": "2026-08-10T00:00:00Z",
                "updatedAt": "2026-08-10T00:00:00Z",
                "author": {"login": "owner"},
            }
        )
    return {
        "id": f"I_{number}",
        "number": number,
        "state": "OPEN",
        "title": f"Issue {number}",
        "body": "",
        "updatedAt": "2026-08-10T00:00:00Z",
        "labels": _github_connection([{"name": label}]),
        "milestone": None,
        "assignees": _github_connection([]),
        "comments": _github_connection(comments),
    }


def _github_pr_node(*, number: int, branch: str) -> dict[str, object]:
    return {
        "id": f"PR_{number}",
        "number": number,
        "state": "OPEN",
        "title": f"PR {number}",
        "body": "",
        "headRefName": branch,
        "headRefOid": "c" * 40,
        "baseRefName": "main",
        "isDraft": True,
        "updatedAt": "2026-08-10T00:00:00Z",
        "mergedAt": None,
        "mergeStateStatus": "CLEAN",
        "reviewDecision": None,
        "url": f"https://github.test/owner/repo/pull/{number}",
        "reviews": _github_connection([]),
        "files": _github_connection([]),
        "commits": _github_connection([]),
    }


def _github_payload(*, has_next: bool = False, base_oid: str | None = "a" * 40):
    repository: dict[str, object] = {
        "id": "R_owner_repo",
        "nameWithOwner": "owner/repo",
        "readyIssues": _github_connection([], has_next=has_next),
        "activeIssues": _github_connection([], has_next=has_next),
        "blockedIssues": _github_connection([], has_next=has_next),
        "pullRequests": _github_connection([], has_next=has_next),
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
    assert calls[0][:4] == ("gh", "api", "graphql", "-f")
    assert calls[0][5:] == (
        "-F",
        "owner=owner",
        "-F",
        "name=repo",
        "-F",
        "branch=refs/heads/main",
    )
    query = calls[0][4]
    assert query.startswith("query=\nquery($owner:String!,$name:String!,$branch:String!){")
    assert "id nameWithOwner" in query
    assert "id number state title body updatedAt" in query
    assert "id number state title body headRefName" in query
    assert observed.complete is True
    assert observed.record.read_mode == "COMPLETE_DOUBLE_READ"
    assert observed.record.identity == (("observation_digest", observed.record.content_sha256),)


def test_command_reader_accepts_strict_noncanonical_json_and_canonicalizes_payload():
    payload = _github_payload()
    raw = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    expected_payload = canonical_bytes(payload)
    assert raw != expected_payload

    observed = GitHubDispatchSnapshotReader(
        lambda command: raw,
        "8" * 64,
    ).read("owner/repo")

    assert type(observed) is SourceObservation
    assert observed.complete is True
    assert observed.canonical_payload == expected_payload
    assert observed.record.repository == "owner/repo"
    assert observed.record.role == "legacy.dispatches"
    assert observed.record.read_mode == "COMPLETE_DOUBLE_READ"
    assert observed.record.content_sha256 == digest_value(payload)
    assert observed.record.identity == (
        ("observation_digest", digest_value(payload)),
    )


@pytest.mark.parametrize(
    "raw",
    (
        pytest.param(b'{"data": {}, "data": {}}', id="duplicate-key"),
        pytest.param(b'{"data":', id="malformed-json"),
        pytest.param(b'{"data": {}} trailing garbage', id="trailing-garbage"),
        pytest.param(b'{"data": NaN}', id="nan"),
        pytest.param(b'{"data": Infinity}', id="infinity"),
        pytest.param(b"\xff", id="invalid-utf8"),
    ),
)
def test_command_reader_rejects_duplicate_invalid_or_trailing_json(raw: bytes):
    with pytest.raises(CanonicalJsonError):
        strict_json_loads(raw)

    with pytest.raises(BootstrapError) as error:
        GitHubDispatchSnapshotReader(
            lambda command: raw,
            "8" * 64,
        ).read("owner/repo")
    assert error.value.code == "LEGACY_SOURCE_UNAVAILABLE"


@pytest.mark.parametrize(
    ("reader_type", "expected_detail"),
    (
        (
            PaseoWorkerInventoryReader,
            "legacy.workers command returned invalid strict JSON",
        ),
        (
            CooperativeHostProcessReader,
            "legacy.processes command returned invalid strict JSON",
        ),
    ),
)
def test_paseo_and_process_readers_reject_invalid_strict_json_at_read_entry(
    reader_type: type[object],
    expected_detail: str,
):
    raw = b'{"records":'

    with pytest.raises(BootstrapError) as error:
        reader_type(lambda command: raw, "8" * 64).read("owner/repo")

    assert error.value.code == "LEGACY_SOURCE_UNAVAILABLE"
    assert error.value.detail == expected_detail
    assert "invalid canonical JSON" not in error.value.detail


def test_paseo_worker_reader_accepts_strict_noncanonical_empty_inventory():
    calls: list[tuple[str, ...]] = []
    raw = b" \n[\n]\n"

    def run(command: tuple[str, ...]) -> bytes:
        calls.append(command)
        return raw

    observed = PaseoWorkerInventoryReader(run, "8" * 64).read("owner/repo")
    expected = {"repository": "owner/repo", "workers": [], "inventory": []}
    digest = digest_value(expected)

    assert calls == [
        (
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
    ]
    assert observed == SourceObservation(
        SourceRecord(
            role="legacy.workers",
            locator="paseo://global/worker-inventory/owner/repo",
            repository="owner/repo",
            read_mode="COMPLETE_DOUBLE_READ",
            identity=(("observation_digest", digest),),
            content_sha256=digest,
            readback_digest=None,
            producer_sha256="8" * 64,
        ),
        canonical_bytes(expected),
        True,
    )


def test_process_reader_accepts_strict_noncanonical_empty_inventory():
    calls: list[tuple[str, ...]] = []
    raw = b" \n[\n]\n"

    def run(command: tuple[str, ...]) -> bytes:
        calls.append(command)
        return raw

    observed = CooperativeHostProcessReader(run, "8" * 64).read("owner/repo")
    digest = digest_value([])

    assert calls == [
        (
            "powershell",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            "Get-CimInstance Win32_Process | Select-Object ProcessId, "
            "ParentProcessId, CreationDate, Name, ExecutablePath, CommandLine | "
            "ConvertTo-Json -Compress",
        )
    ]
    assert observed == SourceObservation(
        SourceRecord(
            role="legacy.processes",
            locator="host://cim/win32-process",
            repository="owner/repo",
            read_mode="COMPLETE_DOUBLE_READ",
            identity=(("observation_digest", digest),),
            content_sha256=digest,
            readback_digest=None,
            producer_sha256="8" * 64,
        ),
        canonical_bytes([]),
        True,
    )


def test_github_snapshot_dispatches_are_normalized_into_active_references(
    subject: CutoverSubject,
    attempt: AttemptIdentity,
    writer: WriterAuthorityObservation,
):
    payload = _github_payload()
    payload["data"]["repository"]["activeIssues"]["nodes"] = [
        _github_issue_node(
            number=17,
            label="orch:active",
            dispatch={"id": "17", "status": "running"},
        )
    ]
    payload["data"]["repository"]["activeIssues"]["totalCount"] = 1

    sources = LegacySourceSet(
        dispatches=GitHubDispatchSnapshotReader(
            lambda command: canonical_bytes(payload),
            "8" * 64,
        ),
        workers=FakeSource(records=(), role="fixture.workers"),
        processes=FakeSource(records=(), role="fixture.processes"),
    )
    observed = LegacyAttestor(sources).observe(
        subject=subject,
        attempt=attempt,
        writer=writer,
    )
    assert dict(observed.readbacks)["legacy"].active_dispatches == ("dispatch:17",)


def test_legacy_rejects_github_base_oid_not_equal_to_cutover_subject(
    subject: CutoverSubject,
    attempt: AttemptIdentity,
    writer: WriterAuthorityObservation,
) -> None:
    sources = replace(
        _sources(),
        dispatches=GitHubDispatchSnapshotReader(
            lambda command: canonical_bytes(_github_payload(base_oid="b" * 40)),
            "8" * 64,
        ),
    )
    with pytest.raises(BootstrapError) as error:
        LegacyAttestor(sources).observe(
            subject=subject,
            attempt=attempt,
            writer=writer,
        )
    assert error.value.code == "LEGACY_SOURCE_UNAVAILABLE"


def test_github_issue_and_pr_schema_feed_dispatch_normalization(
    subject: CutoverSubject,
    attempt: AttemptIdentity,
    writer: WriterAuthorityObservation,
) -> None:
    payload = _github_payload()
    issue = _github_issue_node(
        number=17,
        label="orch:active",
        dispatch={
            "branch": "work/issue-17",
            "id": "dispatch-issue-17-a1",
            "status": "running",
        },
    )
    payload["data"]["repository"]["activeIssues"] = _github_connection([issue])
    payload["data"]["repository"]["pullRequests"] = _github_connection(
        [_github_pr_node(number=31, branch="work/issue-17")]
    )
    observed = LegacyAttestor(
        replace(
            _sources(),
            dispatches=GitHubDispatchSnapshotReader(
                lambda command: canonical_bytes(payload),
                "8" * 64,
            ),
        )
    ).observe(subject=subject, attempt=attempt, writer=writer)
    assert dict(observed.readbacks)["legacy"].active_dispatches == (
        "dispatch:dispatch-issue-17-a1",
    )


def test_github_dispatch_reader_accepts_latest_commit_from_multi_commit_pr() -> None:
    payload = _github_payload()
    pull_request = _github_pr_node(number=31, branch="work/issue-17")
    pull_request["commits"] = {
        "totalCount": 3,
        "pageInfo": {"hasNextPage": False},
        "nodes": [{"commit": {"statusCheckRollup": None}}],
    }
    payload["data"]["repository"]["pullRequests"] = _github_connection(
        [pull_request]
    )

    observed = GitHubDispatchSnapshotReader(
        lambda command: canonical_bytes(payload),
        "8" * 64,
    ).read("owner/repo")

    assert observed.complete is True


@pytest.mark.parametrize(
    "case",
    (
        "graphql_errors",
        "missing_connection",
        "missing_nodes",
        "invalid_nodes",
        "missing_page_info",
        "invalid_page_info",
        "invalid_base_oid",
        "wrong_repository",
        "malformed_issue_identity",
        "malformed_issue_state",
    ),
)
def test_github_dispatch_reader_rejects_partial_or_substituted_snapshot(case: str):
    payload = _github_payload()
    repository = payload["data"]["repository"]
    if case == "graphql_errors":
        payload["errors"] = [{"message": "partial response"}]
    elif case == "missing_connection":
        del repository["activeIssues"]
    elif case == "missing_nodes":
        del repository["activeIssues"]["nodes"]
    elif case == "invalid_nodes":
        repository["activeIssues"]["nodes"] = {}
    elif case == "missing_page_info":
        del repository["activeIssues"]["pageInfo"]
    elif case == "invalid_page_info":
        repository["activeIssues"]["pageInfo"] = []
    elif case == "invalid_base_oid":
        repository["ref"]["target"]["oid"] = "not-an-oid"
    elif case == "wrong_repository":
        repository["nameWithOwner"] = "other/repo"
    else:
        node = _github_issue_node(
            number=17,
            label="orch:active",
            dispatch={"id": "17", "status": "running"},
        )
        if case == "malformed_issue_identity":
            node["number"] = "17"
        else:
            node["state"] = "CLOSED"
        repository["activeIssues"] = _github_connection([node])

    with pytest.raises(BootstrapError) as error:
        GitHubDispatchSnapshotReader(
            lambda command: canonical_bytes(payload),
            "8" * 64,
        ).read("owner/repo")
    assert error.value.code == "LEGACY_SOURCE_UNAVAILABLE"


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


@pytest.mark.parametrize("missing", ("repository", "role", "archived"))
def test_paseo_worker_reader_requires_explicit_inspect_authority(missing: str):
    inspection = {
        "id": "worker-1",
        "repository": "owner/repo",
        "role": "worker",
        "status": "running",
        "archived": False,
    }
    del inspection[missing]

    def run(command: tuple[str, ...]) -> bytes:
        if command[1] == "ls":
            return canonical_bytes([{"Id": "worker-1", "Status": "running"}])
        return canonical_bytes(inspection)

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


@pytest.mark.parametrize(
    "payload",
    (
        {"unexpected": []},
        {"agents": {}},
        {"workers": "not-a-list"},
    ),
)
def test_paseo_worker_reader_rejects_unknown_or_malformed_inventory_shape(payload):
    with pytest.raises(BootstrapError) as error:
        PaseoWorkerInventoryReader(
            lambda command: canonical_bytes(payload),
            "8" * 64,
        ).read("owner/repo")
    assert error.value.code == "LEGACY_SOURCE_UNAVAILABLE"


def test_process_reader_requires_complete_cim_fields_and_marks_exact_lease_match():
    calls: list[tuple[str, ...]] = []
    rows = [
        {
            "ProcessId": 17,
            "ParentProcessId": 1,
            "CreationDate": "20260810000000.000000+000",
            "Name": "python.exe",
            "ExecutablePath": r"D:\repo\.venv\Scripts\python.exe",
            "CommandLine": "python -m orch integrate owner/repo v6.1",
        },
    ]

    def run(command: tuple[str, ...]) -> bytes:
        calls.append(command)
        return canonical_bytes(rows)

    reader = CooperativeHostProcessReader(
        run,
        "8" * 64,
        repository_path=r"D:\repo",
    )
    observed = reader.read("owner/repo")
    value = load_canonical_json(observed.canonical_payload)
    assert calls == [
        (
            "powershell",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            "Get-CimInstance Win32_Process | Select-Object ProcessId, "
            "ParentProcessId, CreationDate, Name, ExecutablePath, CommandLine | "
            "ConvertTo-Json -Compress",
        )
    ]
    assert value[0]["integration_lease"] is True


def test_process_reader_keeps_unavailable_fields_for_unrelated_host_processes():
    row = {
        "ProcessId": 17,
        "ParentProcessId": 1,
        "CreationDate": "20260810000000.000000+000",
        "Name": "svchost.exe",
        "ExecutablePath": None,
        "CommandLine": None,
    }
    observed = CooperativeHostProcessReader(
        lambda command: canonical_bytes([row]),
        "8" * 64,
        repository_path=r"D:\repo",
    ).read("owner/repo")
    value = load_canonical_json(observed.canonical_payload)
    assert value == [
        {
            "ProcessId": 17,
            "ParentProcessId": 1,
            "CreationDate": "20260810000000.000000+000",
            "ExecutablePath": None,
            "CommandLine": None,
            "integration_lease": False,
        }
    ]


def test_process_reader_rejects_incomplete_possible_v61_process():
    row = {
        "ProcessId": 17,
        "ParentProcessId": 1,
        "CreationDate": "20260810000000.000000+000",
        "Name": "python.exe",
        "ExecutablePath": None,
        "CommandLine": None,
    }
    with pytest.raises(BootstrapError) as error:
        CooperativeHostProcessReader(
            lambda command: canonical_bytes([row]),
            "8" * 64,
            repository_path=r"D:\repo",
        ).read("owner/repo")
    assert error.value.code == "LEGACY_SOURCE_UNAVAILABLE"


@pytest.mark.parametrize(
    ("executable", "command_line"),
    (
        (r"D:\repo\.venv\Scripts\python.exe", None),
        (None, "python -m orch integrate owner/repo v6.1"),
    ),
)
def test_process_reader_rejects_any_single_invisible_field_for_possible_v61_process(
    executable: str | None,
    command_line: str | None,
):
    row = {
        "ProcessId": 17,
        "ParentProcessId": 1,
        "CreationDate": "20260810000000.000000+000",
        "Name": "python.exe",
        "ExecutablePath": executable,
        "CommandLine": command_line,
    }

    with pytest.raises(BootstrapError) as error:
        CooperativeHostProcessReader(
            lambda command: canonical_bytes([row]),
            "8" * 64,
            repository_path=r"D:\repo",
        ).read("owner/repo")

    assert error.value.code == "LEGACY_SOURCE_UNAVAILABLE"


def test_process_reader_rejects_root_wrapper_with_unavailable_command_line():
    row = {
        "ProcessId": 17,
        "ParentProcessId": 1,
        "CreationDate": "20260810000000.000000+000",
        "Name": "orch.exe",
        "ExecutablePath": r"D:\repo\orch.exe",
        "CommandLine": None,
    }

    with pytest.raises(BootstrapError) as error:
        CooperativeHostProcessReader(
            lambda command: canonical_bytes([row]),
            "8" * 64,
            repository_path=r"D:\repo",
        ).read("owner/repo")

    assert error.value.code == "LEGACY_SOURCE_UNAVAILABLE"


@pytest.mark.parametrize(
    ("command_line", "expected"),
    (
        ("python -m orch integrate owner/repo v6.1", True),
        ("python -m orch deliver owner/repo v6.1", True),
        ("python -m orch integration owner/repo v6.1", False),
        ("python -m orch merge owner/repo v6.1", False),
        ("python -m orch integrate owner/repo legacy", False),
        ("python -m orch integrate owner/repo V6.1", False),
        ("python -m orch integrate other/repo v6.1", False),
    ),
)
def test_process_reader_requires_exact_repository_action_and_v61_tokens(
    command_line: str,
    expected: bool,
):
    row = {
        "ProcessId": 17,
        "ParentProcessId": 1,
        "CreationDate": "20260810000000.000000+000",
        "ExecutablePath": r"D:\repo\.venv\Scripts\python.exe",
        "CommandLine": command_line,
    }
    observed = CooperativeHostProcessReader(
        lambda command: canonical_bytes([row]),
        "8" * 64,
        repository_path=r"D:\repo",
    ).read("owner/repo")
    assert load_canonical_json(observed.canonical_payload)[0]["integration_lease"] is expected


def test_production_process_reader_requires_fixed_production_repository_path():
    rows = [
        {
            "ProcessId": index,
            "ParentProcessId": 1,
            "CreationDate": f"2026081000000{index}.000000+000",
            "ExecutablePath": executable,
            "CommandLine": "python -m orch integrate owner/repo v6.1",
        }
        for index, executable in (
            (1, r"D:\other\.venv\Scripts\python.exe"),
            (
                2,
                r"D:\Workstation\github-work-orchestrator\.venv\Scripts\python.exe",
            ),
        )
    ]
    reader = production_legacy_sources(
        command_runner=lambda command: canonical_bytes(rows),
        producer_sha256="8" * 64,
    ).processes
    observed = reader.read("owner/repo")
    values = load_canonical_json(observed.canonical_payload)
    assert [row["integration_lease"] for row in values] == [False, True]


def test_process_reader_without_repository_path_cannot_match_lease_owner():
    row = {
        "ProcessId": 17,
        "ParentProcessId": 1,
        "CreationDate": "20260810000000.000000+000",
        "ExecutablePath": r"D:\repo\.venv\Scripts\python.exe",
        "CommandLine": "python -m orch integrate owner/repo v6.1",
    }
    observed = CooperativeHostProcessReader(
        lambda command: canonical_bytes([row]),
        "8" * 64,
    ).read("owner/repo")
    assert load_canonical_json(observed.canonical_payload)[0]["integration_lease"] is False


def test_process_reader_rejects_unqueried_repository_path_claim():
    row = {
        "ProcessId": 17,
        "ParentProcessId": 1,
        "CreationDate": "20260810000000.000000+000",
        "ExecutablePath": r"D:\other\.venv\Scripts\python.exe",
        "CommandLine": "python -m orch integrate owner/repo v6.1",
        "RepositoryPath": r"D:\repo",
    }
    with pytest.raises(BootstrapError) as error:
        CooperativeHostProcessReader(
            lambda command: canonical_bytes([row]),
            "8" * 64,
            repository_path=r"D:\repo",
        ).read("owner/repo")
    assert error.value.code == "LEGACY_SOURCE_UNAVAILABLE"


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


@pytest.mark.parametrize(
    "payload",
    (
        {"records": {}},
        {"processes": "not-a-list"},
    ),
)
def test_process_reader_rejects_non_list_inventory_wrapper(payload):
    with pytest.raises(BootstrapError) as error:
        CooperativeHostProcessReader(
            lambda command: canonical_bytes(payload),
            "8" * 64,
        ).read("owner/repo")
    assert error.value.code == "LEGACY_SOURCE_UNAVAILABLE"


@pytest.mark.parametrize("source_name", ("workers", "processes"))
def test_legacy_rejects_unknown_authoritative_payload_shape(
    source_name: str,
    subject: CutoverSubject,
    attempt: AttemptIdentity,
    writer: WriterAuthorityObservation,
):
    sources = _sources()
    malformed = PayloadSource(
        value={"unexpected": []},
        role=f"fixture.{source_name}",
    )
    with pytest.raises(BootstrapError) as error:
        LegacyAttestor(replace(sources, **{source_name: malformed})).observe(
            subject=subject,
            attempt=attempt,
            writer=writer,
        )
    assert error.value.code == "LEGACY_SOURCE_UNAVAILABLE"


def test_legacy_rejects_live_worker_inventory_omitted_from_normalized_records(
    subject: CutoverSubject,
    attempt: AttemptIdentity,
    writer: WriterAuthorityObservation,
) -> None:
    class OmittedWorkerSource:
        def read(self, repository: str) -> SourceObservation:
            value = {
                "repository": repository,
                "inventory": [{"Id": "worker-1", "Status": "running"}],
                "workers": [],
            }
            payload = canonical_bytes(value)
            digest = digest_value(value)
            return SourceObservation(
                SourceRecord(
                    role="legacy.workers",
                    locator="paseo://fixture/omitted",
                    repository=repository,
                    read_mode="COMPLETE_DOUBLE_READ",
                    identity=(("observation_digest", digest),),
                    content_sha256=digest,
                    readback_digest=None,
                    producer_sha256="8" * 64,
                ),
                payload,
                True,
            )

    with pytest.raises(BootstrapError) as error:
        LegacyAttestor(
            replace(_sources(), workers=OmittedWorkerSource())
        ).observe(subject=subject, attempt=attempt, writer=writer)
    assert error.value.code == "LEGACY_SOURCE_UNAVAILABLE"


def test_legacy_rejects_duplicate_normalized_worker_identity_omission(
    subject: CutoverSubject,
    attempt: AttemptIdentity,
    writer: WriterAuthorityObservation,
) -> None:
    inventory = [
        {"Id": "worker-a", "Status": "closed"},
        {"Id": "worker-b", "Status": "running"},
    ]
    closed_worker = {
        "id": "worker-a",
        "inventory": inventory[0],
        "inspect": {
            "id": "worker-a",
            "repository": subject.repository,
            "role": "worker",
            "status": "closed",
            "archived": False,
        },
    }

    class DuplicateWorkerSource:
        def read(self, repository: str) -> SourceObservation:
            value = {
                "repository": repository,
                "inventory": inventory,
                "workers": [closed_worker, dict(closed_worker)],
            }
            payload = canonical_bytes(value)
            digest = digest_value(value)
            return SourceObservation(
                SourceRecord(
                    role="legacy.workers",
                    locator="paseo://fixture/duplicate-normalized-worker",
                    repository=repository,
                    read_mode="COMPLETE_DOUBLE_READ",
                    identity=(("observation_digest", digest),),
                    content_sha256=digest,
                    readback_digest=None,
                    producer_sha256="8" * 64,
                ),
                payload,
                True,
            )

    with pytest.raises(BootstrapError) as error:
        LegacyAttestor(
            replace(_sources(), workers=DuplicateWorkerSource())
        ).observe(subject=subject, attempt=attempt, writer=writer)
    assert error.value.code == "LEGACY_SOURCE_UNAVAILABLE"


def test_legacy_rejects_live_process_record_without_match_derivation(
    subject: CutoverSubject,
    attempt: AttemptIdentity,
    writer: WriterAuthorityObservation,
) -> None:
    class UnderivedProcessSource:
        def read(self, repository: str) -> SourceObservation:
            value = [
                {
                    "ProcessId": 17,
                    "ParentProcessId": 1,
                    "CreationDate": "20260810000000.000000+000",
                    "ExecutablePath": r"D:\repo\.venv\Scripts\python.exe",
                    "CommandLine": "python -m orch integrate owner/repo v6.1",
                }
            ]
            payload = canonical_bytes(value)
            digest = digest_value(value)
            return SourceObservation(
                SourceRecord(
                    role="legacy.processes",
                    locator="host://fixture/underived",
                    repository=repository,
                    read_mode="COMPLETE_DOUBLE_READ",
                    identity=(("observation_digest", digest),),
                    content_sha256=digest,
                    readback_digest=None,
                    producer_sha256="8" * 64,
                ),
                payload,
                True,
            )

    with pytest.raises(BootstrapError) as error:
        LegacyAttestor(
            replace(_sources(), processes=UnderivedProcessSource())
        ).observe(subject=subject, attempt=attempt, writer=writer)
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
