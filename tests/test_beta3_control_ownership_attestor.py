from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[1]
for candidate in (ROOT / "skills" / "orchestrator" / "scripts", ROOT / "scripts"):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from gwo_v8.cutover_guard import CutoverSubject  # noqa: E402
from gwo_v8._canonical import (  # noqa: E402
    canonical_bytes,
    digest_bytes,
    digest_value,
    load_canonical_json,
)
from gwo_v8.cutover_guard import (  # noqa: E402
    CompatibilityPathReadback,
    DurableStateReadback,
    PackageReadback,
)
from gwo_v8.transition import WriterTransitionRecord  # noqa: E402
import beta3_control_ownership_attestor as attestor_module  # noqa: E402
from beta3_bootstrap_model import (  # noqa: E402
    AttemptIdentity,
    BootstrapError,
    SourceObservation,
    SourceRecord,
)
from beta3_control_ownership_attestor import (  # noqa: E402
    ControlOwnershipAttestor,
    ControlOwnershipSourceSet,
)


@dataclass
class _Blob:
    content: bytes
    blob_sha: str


class _ControlFixture:
    oid = "1" * 40

    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []
        self.writer_bytes: bytes | None = b"{}"
        self.active_plan_bytes = b"{}"
        self.legacy_fence_bytes = b"{}"

    def read_ref(self, repository: str, branch: str) -> str:
        self.calls.append(("read_ref", repository, branch))
        return self.oid

    def read_at_oid(self, repository: str, oid: str, path: str) -> _Blob | None:
        self.calls.append(("read_at_oid", repository, oid, path))
        content = {
            ".gwo-v8/writer-transition.json": self.writer_bytes,
            ".gwo/v8/active-plan.json": self.active_plan_bytes,
            ".gwo-v8/legacy-writer-fence.json": self.legacy_fence_bytes,
        }[path]
        if content is None:
            return None
        blob_sha = hashlib.sha1(
            f"blob {len(content)}\0".encode("ascii") + content
        ).hexdigest()
        return _Blob(content, blob_sha)


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


def _attempt(subject: CutoverSubject) -> AttemptIdentity:
    from gwo_v8._canonical import digest_value

    return AttemptIdentity(
        run_id="beta3-test",
        challenge_nonce="ab" * 16,
        repository=subject.repository,
        evidence_root="C:/evidence",
        cutover_subject_digest=digest_value(subject.canonical()),
        runner_sha256="c" * 64,
        attestor_sha256="d" * 64,
    )


@pytest.fixture
def control_fixture() -> _ControlFixture:
    return _ControlFixture()


class _Registry:
    def read(self, _repository: str) -> object:
        return {"runtimes": []}


class _RuntimeConfig:
    def read(self) -> object:
        profile = {
            "provider": "provider",
            "settings": {
                "model": "model",
                "thinkingOptionId": "high",
                "modeId": "write",
                "features": {},
            },
        }
        value = {
            "schema_version": 1,
            "global": {"default_tier": "standard"},
            "tiers": {"standard": profile},
            "role_profiles": {
                "coordinator_auto": profile,
                "reviewer_recovery": profile,
                "reviewer_standard": profile,
                "reviewer_strict": profile,
            },
        }
        payload = canonical_bytes(value)
        config_path = Path.home() / ".orch" / "config.json"
        record = SourceRecord(
            role="runtime.config",
            locator=str(config_path),
            repository="owner/repo",
            read_mode="EXACT_FILE",
            identity=(
                ("byte_sha256", digest_bytes(payload)),
                ("inode", "1"),
                ("mtime_ns", "2"),
                ("path", str(config_path.resolve())),
                ("size", str(len(payload))),
            ),
            content_sha256=digest_bytes(payload),
            readback_digest=None,
            producer_sha256="d" * 64,
        )
        return SourceObservation(record=record, canonical_payload=payload, complete=True)


class _LocalInputs:
    def read(self, _config: object, _subject: CutoverSubject) -> None:
        return None


def _record(role: str, repository: str, producer: str) -> SourceRecord:
    payload = canonical_bytes({"role": role})
    return SourceRecord(
        role=role,
        locator=f"test://{role}",
        repository=repository,
        read_mode="TEST",
        identity=(("role", role),),
        content_sha256=digest_bytes(payload),
        readback_digest=None,
        producer_sha256=producer,
    )


def _readback(value):
    body = value.canonical()
    body.pop("readback_digest")
    return replace(value, readback_digest=digest_value(body))


def _writer_record(**values: object) -> WriterTransitionRecord:
    identity = {key: value for key, value in values.items() if key != "created_at"}
    record_id = f"writer-transition:{digest_value(identity)[:24]}"
    return WriterTransitionRecord(record_id=record_id, **values)


def _control_bytes(subject: CutoverSubject) -> tuple[bytes, bytes, bytes]:
    plan = "1" * 64
    pending = _writer_record(
        repository=subject.repository,
        kind="cutover_pending",
        status="pending",
        previous_writer_generation="v6.1",
        writer_generation="v8",
        activation_id=None,
        plan_digest=plan,
        canary_evidence_digest="2" * 64,
        canary_evidence_refs=("canary:1",),
        canary_manifest_ref="manifest:1",
        worker_capacity=0,
        coordinator_capacity=0,
        reason=None,
        created_at="2026-08-10T00:00:00Z",
    )
    draining = _writer_record(
        repository=subject.repository,
        kind="drain",
        status="draining",
        previous_writer_generation="v8",
        writer_generation="v8",
        activation_id=None,
        plan_digest=plan,
        canary_evidence_digest="2" * 64,
        canary_evidence_refs=("canary:1",),
        canary_manifest_ref="manifest:1",
        worker_capacity=0,
        coordinator_capacity=0,
        reason="drain",
        created_at="2026-08-10T00:00:01Z",
    )
    rollback = _writer_record(
        repository=subject.repository,
        kind="rollback",
        status="rolled_back",
        previous_writer_generation="v8",
        writer_generation="v6.1",
        activation_id=None,
        plan_digest=plan,
        canary_evidence_digest=None,
        canary_evidence_refs=("canary:1",),
        canary_manifest_ref="manifest:1",
        worker_capacity=0,
        coordinator_capacity=0,
        reason="drain",
        created_at="2026-08-10T00:00:02Z",
    )
    writer = {
        "schema_version": 1,
        "current": {
            "repository": subject.repository,
            "writer_generation": "v6.1",
            "record_id": rollback.record_id,
        },
        "records": [
            {
                **pending.__dict__,
                "canary_evidence_refs": list(pending.canary_evidence_refs),
            },
            {
                **draining.__dict__,
                "canary_evidence_refs": list(draining.canary_evidence_refs),
            },
            {
                **rollback.__dict__,
                "canary_evidence_refs": list(rollback.canary_evidence_refs),
            },
        ],
    }
    active = {
        "schema_version": 1,
        "repository": subject.repository,
        "active_plan_digest": plan,
        "receipts": [
            {
                "schema_version": 1,
                "repository": subject.repository,
                "writer_generation": "v8",
                "activation_id": "activation:1",
                "plan_digest": plan,
                "expected_previous_digest": None,
                "plan_record_ref": "plan:1",
                "created_at": "2026-08-10T00:00:00Z",
            }
        ],
    }
    legacy = {
        "schema_version": 1,
        "repository": subject.repository,
        "stopped": True,
        "events": [{"action_key": "stop:1", "operation": "stop"}],
    }
    return canonical_bytes(writer), canonical_bytes(active), canonical_bytes(legacy)


def _sources(control: _ControlFixture) -> ControlOwnershipSourceSet:
    subject = _subject()
    control.writer_bytes, control.active_plan_bytes, control.legacy_fence_bytes = _control_bytes(subject)
    return ControlOwnershipSourceSet(
        control=control,
        runtime_registry=_Registry(),
        runtime_config=_RuntimeConfig(),
        local_inputs=_LocalInputs(),
    )


def _config(tmp_path: Path) -> SimpleNamespace:
    return SimpleNamespace(
        expected_head="a" * 40,
        expected_tree="b" * 64,
        repository_root=tmp_path,
        install_roots=(tmp_path / ".agents", tmp_path / ".codex", tmp_path / ".claude"),
        fresh_store=tmp_path / "store.sqlite3",
        fresh_receipt=tmp_path / "receipt.json",
        expected_fresh_store_sha256="e" * 64,
        store_generation="store:v8:test",
        expected_store_tables=(),
    )


def test_control_reads_every_blob_at_one_fixed_oid(control_fixture, tmp_path, monkeypatch):
    subject = _subject()
    store_record = _record("store.sqlite", subject.repository, _attempt(subject).attestor_sha256)
    receipt_record = _record("store.receipt", subject.repository, _attempt(subject).attestor_sha256)
    durable = _readback(
        DurableStateReadback(
            repository=subject.repository,
            generation_id=subject.store_generation,
            state_schema="gwo.v8.store.v1",
            compatible=True,
            active_plan_digests=(),
            pending_activation_ids=(),
            predecessor_identity_refs=(),
            readback_digest="",
        )
    )
    store = SimpleNamespace(
        store_record=store_record,
        receipt_record=receipt_record,
        durable=durable,
        active_admissions=(),
        active_attempts=(),
        integration_lease_owner=None,
        resource_claims=(),
    )
    compatibility = _readback(
        CompatibilityPathReadback(
            repository=subject.repository,
            source_commit=subject.source_commit,
            source_tree_digest=subject.source_tree_digest,
            audit_version="test",
            reachable_v2_projection_refs=(),
            reachable_v3_compatibility_refs=(),
            reachable_legacy_writer_refs=(),
                proven_unreachable_refs=tuple(sorted(subject.forbidden_production_refs)),
            readback_digest="",
        )
    )
    packages = _readback(PackageReadback(source_packages=(), installed_packages=(), drift=(), readback_digest=""))
    static_record = _record("compatibility.module", subject.repository, _attempt(subject).attestor_sha256)
    package_record = _record("package.file", subject.repository, _attempt(subject).attestor_sha256)
    monkeypatch.setattr(attestor_module, "_read_store", lambda *_args: store)
    monkeypatch.setattr(attestor_module, "_static_records", lambda *_args, **_kwargs: [static_record])
    monkeypatch.setattr(attestor_module, "_package_records", lambda *_args, **_kwargs: [package_record])
    monkeypatch.setattr(attestor_module, "ProductionPathScanner", lambda _root: SimpleNamespace(read=lambda _subject: compatibility))
    monkeypatch.setattr(attestor_module, "ReadOnlyPackageValidator", lambda *_args: SimpleNamespace(read=lambda _subject: packages))
    config = _config(tmp_path)
    observation = ControlOwnershipAttestor(_sources(control_fixture)).observe(
        config=config, subject=subject, attempt=_attempt(subject)
    )
    assert observation.writer_authority is not None
    assert control_fixture.calls == [
        ("read_ref", subject.repository, "gwo-control"),
        ("read_at_oid", subject.repository, control_fixture.oid, ".gwo-v8/writer-transition.json"),
        ("read_at_oid", subject.repository, control_fixture.oid, ".gwo/v8/active-plan.json"),
        ("read_at_oid", subject.repository, control_fixture.oid, ".gwo-v8/legacy-writer-fence.json"),
    ]


def test_control_source_records_retain_each_blob_byte_digest(
    control_fixture, tmp_path, monkeypatch
):
    subject = _subject()
    attempt = _attempt(subject)
    store_record = _record("store.sqlite", subject.repository, attempt.attestor_sha256)
    receipt_record = _record("store.receipt", subject.repository, attempt.attestor_sha256)
    durable = _readback(
        DurableStateReadback(
            repository=subject.repository,
            generation_id=subject.store_generation,
            state_schema="gwo.v8.store.v1",
            compatible=True,
            active_plan_digests=(),
            pending_activation_ids=(),
            predecessor_identity_refs=(),
            readback_digest="",
        )
    )
    store = SimpleNamespace(
        store_record=store_record,
        receipt_record=receipt_record,
        durable=durable,
        active_admissions=(),
        active_attempts=(),
        integration_lease_owner=None,
        resource_claims=(),
    )
    compatibility = _readback(
        CompatibilityPathReadback(
            repository=subject.repository,
            source_commit=subject.source_commit,
            source_tree_digest=subject.source_tree_digest,
            audit_version="test",
            reachable_v2_projection_refs=(),
            reachable_v3_compatibility_refs=(),
            reachable_legacy_writer_refs=(),
            proven_unreachable_refs=tuple(sorted(subject.forbidden_production_refs)),
            readback_digest="",
        )
    )
    packages = _readback(
        PackageReadback(source_packages=(), installed_packages=(), drift=(), readback_digest="")
    )
    static_record = _record("compatibility.module", subject.repository, attempt.attestor_sha256)
    package_record = _record("package.file", subject.repository, attempt.attestor_sha256)
    monkeypatch.setattr(attestor_module, "_read_store", lambda *_args: store)
    monkeypatch.setattr(attestor_module, "_static_records", lambda *_args, **_kwargs: [static_record])
    monkeypatch.setattr(attestor_module, "_package_records", lambda *_args, **_kwargs: [package_record])
    monkeypatch.setattr(
        attestor_module,
        "ProductionPathScanner",
        lambda _root: SimpleNamespace(read=lambda _subject: compatibility),
    )
    monkeypatch.setattr(
        attestor_module,
        "ReadOnlyPackageValidator",
        lambda *_args: SimpleNamespace(read=lambda _subject: packages),
    )

    observation = ControlOwnershipAttestor(_sources(control_fixture)).observe(
        config=_config(tmp_path), subject=subject, attempt=attempt
    )

    control_records = {
        record.role: dict(record.identity)
        for record in observation.source_records
        if record.role.startswith("control.")
    }
    assert control_records["control.writer"]["byte_sha256"] == digest_bytes(
        control_fixture.writer_bytes
    )
    assert control_records["control.active_plan"]["byte_sha256"] == digest_bytes(
        control_fixture.active_plan_bytes
    )
    assert control_records["control.legacy_fence"]["byte_sha256"] == digest_bytes(
        control_fixture.legacy_fence_bytes
    )


def test_control_rejects_missing_record_instead_of_initial_writer_fallback(
    control_fixture,
    tmp_path,
):
    subject = _subject()
    sources = _sources(control_fixture)
    control_fixture.writer_bytes = None
    with pytest.raises(Exception) as error:
        ControlOwnershipAttestor(sources).observe(
            config=_config(tmp_path), subject=subject, attempt=_attempt(subject)
        )
    assert getattr(error.value, "code", None) == "WRITER_FENCE_SOURCE_UNAVAILABLE"


def test_control_rejects_blob_identity_mismatch(control_fixture, tmp_path):
    subject = _subject()
    sources = _sources(control_fixture)

    class MismatchedBlob(_ControlFixture):
        def read_ref(self, repository: str, branch: str) -> str:
            return control_fixture.read_ref(repository, branch)

        def read_at_oid(self, repository: str, oid: str, path: str) -> _Blob | None:
            value = control_fixture.read_at_oid(repository, oid, path)
            if value is None:
                return None
            return _Blob(value.content, "0" * 40)

    with pytest.raises(Exception) as error:
        ControlOwnershipAttestor(
            replace(sources, control=MismatchedBlob())
        ).observe(config=_config(tmp_path), subject=subject, attempt=_attempt(subject))
    assert getattr(error.value, "code", None) == "WRITER_FENCE_SOURCE_UNAVAILABLE"


def test_control_rejects_unsafe_source_capability(control_fixture):
    class UnsafeControl(_ControlFixture):
        def compare_and_swap(self):
            raise AssertionError("must not be called")

    with pytest.raises(Exception) as error:
        ControlOwnershipAttestor(
            replace(_sources(control_fixture), control=UnsafeControl())
        )
    assert getattr(error.value, "code", None) == "UNSAFE_SOURCE_CAPABILITY"


def test_runtime_config_rejects_replaced_exact_path(control_fixture, tmp_path, monkeypatch):
    subject = _subject()
    attempt = _attempt(subject)
    sources = _sources(control_fixture)
    payload = _RuntimeConfig().read().canonical_payload
    config_path = Path.home() / ".orch" / "config.json"
    record = SourceRecord(
        role="runtime.config",
        locator=str(Path("C:/replaced/config.json")),
        repository=subject.repository,
        read_mode="EXACT_FILE",
        identity=(
            ("byte_sha256", digest_bytes(payload)),
            ("inode", "1"),
            ("mtime_ns", "2"),
            ("path", str(config_path)),
            ("size", str(len(payload))),
        ),
        content_sha256=digest_bytes(payload),
        readback_digest=None,
        producer_sha256=attempt.attestor_sha256,
    )
    replaced = SourceObservation(record=record, canonical_payload=payload, complete=True)
    sources = replace(sources, runtime_config=SimpleNamespace(read=lambda: replaced))
    store = SimpleNamespace(
        store_record=_record("store.sqlite", subject.repository, attempt.attestor_sha256),
        receipt_record=_record("store.receipt", subject.repository, attempt.attestor_sha256),
        durable=_readback(
            DurableStateReadback(
                repository=subject.repository,
                generation_id=subject.store_generation,
                state_schema="gwo.v8.store.v1",
                compatible=True,
                active_plan_digests=(),
                pending_activation_ids=(),
                predecessor_identity_refs=(),
                readback_digest="",
            )
        ),
        active_admissions=(),
        active_attempts=(),
        integration_lease_owner=None,
        resource_claims=(),
    )
    compatibility = _readback(
        CompatibilityPathReadback(
            repository=subject.repository,
            source_commit=subject.source_commit,
            source_tree_digest=subject.source_tree_digest,
            audit_version="test",
            reachable_v2_projection_refs=(),
            reachable_v3_compatibility_refs=(),
            reachable_legacy_writer_refs=(),
            proven_unreachable_refs=tuple(sorted(subject.forbidden_production_refs)),
            readback_digest="",
        )
    )
    packages = _readback(
        PackageReadback(source_packages=(), installed_packages=(), drift=(), readback_digest="")
    )
    monkeypatch.setattr(attestor_module, "_read_store", lambda *_args: store)
    monkeypatch.setattr(
        attestor_module,
        "ProductionPathScanner",
        lambda _root: SimpleNamespace(read=lambda _subject: compatibility),
    )
    monkeypatch.setattr(
        attestor_module,
        "ReadOnlyPackageValidator",
        lambda *_args: SimpleNamespace(read=lambda _subject: packages),
    )
    with pytest.raises(Exception) as error:
        ControlOwnershipAttestor(sources).observe(
            config=_config(tmp_path), subject=subject, attempt=attempt
        )
    assert getattr(error.value, "code", None) == "RUNTIME_CONFIGURATION_SOURCE_UNAVAILABLE"


def test_runtime_config_accepts_current_optional_configuration_keys():
    valid = load_canonical_json(_RuntimeConfig().read().canonical_payload)
    extended = {
        **valid,
        "schema_version": 1,
        "global": {**valid["global"], "execution_slots": 3},
        "reviewer_tiers": {"standard": "standard", "strict": "heavy"},
        "repositories": {"owner/repo": {"default_tier": "standard"}},
    }
    _configuration, runtime = attestor_module._runtime_config_value(
        canonical_bytes(extended), "owner/repo"
    )
    assert tuple(item.selector for item in runtime.selectors) == attestor_module.RUNTIME_SELECTORS


def test_source_observation_rejects_content_hash_drift():
    subject = _subject()
    attempt = _attempt(subject)
    payload = canonical_bytes({"observed": "bytes"})
    record = SourceRecord(
        role="runtime.registry",
        locator="runtime-registry://owner/repo",
        repository=subject.repository,
        read_mode="COMPLETE_DOUBLE_READ",
        identity=(("observation_digest", digest_bytes(payload)),),
        content_sha256=digest_bytes(payload),
        readback_digest=None,
        producer_sha256=attempt.attestor_sha256,
    )
    object.__setattr__(record, "content_sha256", digest_bytes(b"different"))
    observation = object.__new__(SourceObservation)
    object.__setattr__(observation, "record", record)
    object.__setattr__(observation, "canonical_payload", payload)
    object.__setattr__(observation, "complete", True)

    with pytest.raises(BootstrapError) as error:
        attestor_module._source_observation(
            observation,
            role="runtime.registry",
            repository=subject.repository,
            producer_sha256=attempt.attestor_sha256,
            default_locator="runtime-registry://owner/repo",
            default_read_mode="COMPLETE_DOUBLE_READ",
        )
    assert error.value.code == "RUNTIME_REGISTRY_SOURCE_UNAVAILABLE"


def test_source_observation_rejects_role_or_read_mode_substitution():
    subject = _subject()
    attempt = _attempt(subject)
    payload = canonical_bytes({"observed": "bytes"})
    record = SourceRecord(
        role="substituted.role",
        locator="fixture://source",
        repository=subject.repository,
        read_mode="FIXTURE",
        identity=(("observation_digest", digest_bytes(payload)),),
        content_sha256=digest_bytes(payload),
        readback_digest=None,
        producer_sha256=attempt.attestor_sha256,
    )
    with pytest.raises(BootstrapError) as error:
        attestor_module._source_observation(
            SourceObservation(record=record, canonical_payload=payload, complete=True),
            role="runtime.registry",
            repository=subject.repository,
            producer_sha256=attempt.attestor_sha256,
            default_locator="runtime-registry://owner/repo",
            default_read_mode="COMPLETE_DOUBLE_READ",
        )
    assert error.value.code == "RUNTIME_REGISTRY_SOURCE_UNAVAILABLE"


def test_runtime_registry_rejects_unknown_mapping_shape():
    with pytest.raises(BootstrapError) as error:
        attestor_module._registry_refs({"epoch": "registry:1"})
    assert error.value.code == "RUNTIME_REGISTRY_SOURCE_UNAVAILABLE"


def test_bindings_do_not_fallback_to_unrelated_source_records():
    subject = _subject()
    readback = _readback(
        DurableStateReadback(
            repository=subject.repository,
            generation_id=subject.store_generation,
            state_schema="gwo.v8.store.v1",
            compatible=True,
            active_plan_digests=(),
            pending_activation_ids=(),
            predecessor_identity_refs=(),
            readback_digest="",
        )
    )
    record = _record("unrelated", subject.repository, _attempt(subject).attestor_sha256)
    with pytest.raises(BootstrapError) as error:
        attestor_module._bindings(
            {"durable_state": readback},
            (record,),
            {"durable_state": ()},
        )
    assert error.value.code == "COMPONENT_INVALID"


def test_runtime_config_requires_complete_file_identity():
    observation = _RuntimeConfig().read()
    record = replace(
        observation.record,
        identity=tuple(
            pair for pair in observation.record.identity if pair[0] != "inode"
        ),
    )
    with pytest.raises(BootstrapError) as error:
        attestor_module._validate_runtime_config_source(
            SourceObservation(
                record=record,
                canonical_payload=observation.canonical_payload,
                complete=True,
            )
        )
    assert error.value.code == "RUNTIME_CONFIGURATION_SOURCE_UNAVAILABLE"


def test_package_records_reject_missing_package_provenance(tmp_path):
    subject = _subject()
    with pytest.raises(BootstrapError) as error:
        attestor_module._package_records(
            tmp_path,
            {surface: tmp_path / surface for surface in subject.install_surfaces},
            subject,
            producer_sha256="d" * 64,
            readback_digest="c" * 64,
        )
    assert error.value.code == "PACKAGE_SOURCE_UNAVAILABLE"


def test_runtime_config_requires_schema_version():
    value = load_canonical_json(_RuntimeConfig().read().canonical_payload)
    value.pop("schema_version", None)

    with pytest.raises(BootstrapError) as error:
        attestor_module._runtime_config_value(canonical_bytes(value), "owner/repo")
    assert error.value.code == "RUNTIME_CONFIGURATION_SOURCE_UNAVAILABLE"


def test_runtime_config_resolves_repository_tier_and_role_overrides():
    value = load_canonical_json(_RuntimeConfig().read().canonical_payload)
    override = {
        "provider": "override-provider",
        "settings": {
            "model": "override-model",
            "thinkingOptionId": "override-thinking",
            "modeId": "override-mode",
            "features": {},
        },
    }
    value["repositories"] = {
        "owner/repo": {
            "default_tier": "standard",
            "tiers": {"standard": override},
            "role_profiles": {"coordinator_auto": override},
        }
    }

    _configuration, runtime = attestor_module._runtime_config_value(
        canonical_bytes(value), "owner/repo"
    )
    selectors = {item.selector: item for item in runtime.selectors}
    expected_digest = attestor_module.RuntimeProfile(
        name="standard",
        provider="override-provider",
        model="override-model",
        thinking="override-thinking",
        mode="override-mode",
        features={},
    ).digest
    assert selectors["worker"].profile_digest == expected_digest
    assert selectors["worker"].configuration_source == "repository"
    assert selectors["coordinator"].profile_digest != selectors["worker"].profile_digest
    assert selectors["coordinator"].configuration_source == "repository"


def test_runtime_config_uses_current_main_empty_features_default():
    value = load_canonical_json(_RuntimeConfig().read().canonical_payload)
    for profile in (
        *value["tiers"].values(),
        *value["role_profiles"].values(),
    ):
        profile["settings"].pop("features")
    _configuration, runtime = attestor_module._runtime_config_value(
        canonical_bytes(value), "owner/repo"
    )
    assert tuple(item.selector for item in runtime.selectors) == attestor_module.RUNTIME_SELECTORS


def test_static_records_do_not_fallback_to_empty_provenance(tmp_path):
    with pytest.raises(BootstrapError) as error:
        attestor_module._static_records(
            tmp_path,
            repository="owner/repo",
            producer_sha256="d" * 64,
            role="compatibility.module",
            source_commit="a" * 40,
            source_tree_digest="b" * 64,
            readback_digest="c" * 64,
        )
    assert error.value.code == "STATIC_INPUT_SOURCE_UNAVAILABLE"
