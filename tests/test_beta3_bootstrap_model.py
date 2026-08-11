from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import sys

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
EXACT_SCRIPTS = REPO_ROOT / "skills" / "orchestrator" / "scripts"
SCRIPTS = REPO_ROOT / "scripts"
for path in (EXACT_SCRIPTS, SCRIPTS):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from gwo_v8._canonical import digest_value  # noqa: E402
from gwo_v8.cutover_guard import (  # noqa: E402
    CompatibilityPathReadback,
    CutoverReadbackBundle,
    CutoverSubject,
    DurableStateReadback,
    LegacyReadback,
    OwnershipReadback,
    PackageIdentity,
    PackageReadback,
    RuntimePreflightReadback,
    RuntimeSelectorReadback,
    WriterFenceReadback,
)
from beta3_bootstrap_model import (  # noqa: E402
    AttestedCutoverBundle,
    AttemptIdentity,
    BootstrapLease,
    BootstrapError,
    ComponentObservation,
    FieldBinding,
    FrozenReadPort,
    require_read_only_surface,
    SourceRecord,
)


READBACK_NAMES = (
    "legacy",
    "durable_state",
    "writer_fence",
    "ownership",
    "compatibility",
    "runtime",
    "packages",
)


def _digest_readback(value):
    body = value.canonical()
    body.pop("readback_digest")
    return replace(value, readback_digest=digest_value(body))


def _subject():
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


def _readbacks():
    legacy = _digest_readback(
        LegacyReadback(
            repository="owner/repo",
            writer_generation="v6.1",
            authority_state="authoritative_quiescent",
            active_dispatches=(),
            active_workers=(),
            integration_lease_owner=None,
            v2_execution_refs=(),
            v2_execution_state="none",
            original_decoder_readable=True,
            durable_state_digest="1" * 64,
        )
    )
    durable_state = _digest_readback(
        DurableStateReadback(
            repository="owner/repo",
            generation_id="generation:test",
            state_schema="gwo.store.v8",
            compatible=True,
            active_plan_digests=(),
            pending_activation_ids=(),
            predecessor_identity_refs=(),
            readback_digest="",
        )
    )
    writer_fence = _digest_readback(
        WriterFenceReadback(
            repository="owner/repo",
            writer_generation="v8",
            authority_state="cut_over",
            record_id="writer-record:test",
            activation_id="activation:test",
            control_ref_digest="2" * 64,
            readback_digest="",
        )
    )
    ownership = _digest_readback(
        OwnershipReadback(
            repository="owner/repo",
            active_admissions=(),
            active_attempts=(),
            integration_lease_owner=None,
            runtime_resource_refs=(),
            readback_digest="",
        )
    )
    compatibility = _digest_readback(
        CompatibilityPathReadback(
            repository="owner/repo",
            source_commit="a" * 40,
            source_tree_digest="b" * 64,
            audit_version="audit:test",
            reachable_v2_projection_refs=(),
            reachable_v3_compatibility_refs=(),
            reachable_legacy_writer_refs=(),
            proven_unreachable_refs=("legacy:writer",),
            readback_digest="",
        )
    )
    runtime = _digest_readback(
        RuntimePreflightReadback(
            repository="owner/repo",
            selectors=(
                RuntimeSelectorReadback(
                    selector="coordinator",
                    profile_digest="3" * 64,
                    fallback_profile_digest=None,
                    configuration_source="host_global",
                ),
            ),
            configuration_digest="4" * 64,
            provider_action_refs=(),
            persistence_write_refs=(),
            readback_digest="",
        )
    )
    package = PackageIdentity(
        package_name="orchestrator",
        version="8.0.0",
        content_digest="5" * 64,
        manifest_content_digest="6" * 64,
        install_surface=".agents",
    )
    packages = _digest_readback(
        PackageReadback(
            source_packages=(package,),
            installed_packages=(package,),
            drift=(),
            readback_digest="",
        )
    )
    return dict(
        zip(
            READBACK_NAMES,
            (
                legacy,
                durable_state,
                writer_fence,
                ownership,
                compatibility,
                runtime,
                packages,
            ),
        )
    )


def _binding_targets(subject, readbacks):
    return tuple(
        target
        for name, value in (("subject", subject), *readbacks.items())
        for target in (f"{name}.{field}" for field in value.canonical())
    )


@pytest.fixture
def valid_bundle():
    subject = _subject()
    readbacks = _readbacks()
    attempt = AttemptIdentity.create(
        run_id="beta3-prod-001",
        repository="owner/repo",
        evidence_root=r"D:\evidence",
        cutover_subject_digest=digest_value(subject.canonical()),
        runner_sha256="7" * 64,
        attestor_sha256="8" * 64,
        nonce_factory=lambda size: "9" * (size * 2),
    )
    source_record = SourceRecord(
        role="fixture.all",
        locator="fixture://owner/repo/all",
        repository="owner/repo",
        read_mode="FIXTURE",
        identity=(("fixture_id", "fixture-1"),),
        content_sha256="a" * 64,
        readback_digest=None,
        producer_sha256=attempt.attestor_sha256,
    )
    source_digest = source_record.digest
    bindings = tuple(
        FieldBinding(
            target=target,
            source_record_digests=(source_digest,),
            derivation="fixture.canonical",
        )
        for target in _binding_targets(subject, readbacks)
    )
    component = ComponentObservation(
        readbacks=tuple(readbacks.items()),
        source_records=(source_record,),
        field_bindings=bindings,
    )
    return AttestedCutoverBundle.create(
        attempt=attempt,
        subject=subject,
        components=(component,),
    )


def test_attempt_identity_requires_nonce_subject_and_code_bindings():
    with pytest.raises(BootstrapError) as error:
        AttemptIdentity(
            run_id="beta3-prod-001",
            challenge_nonce="ab" * 15,
            repository="owner/repo",
            evidence_root=r"D:\evidence",
            cutover_subject_digest="1" * 64,
            runner_sha256="2" * 64,
            attestor_sha256="3" * 64,
        )
    assert error.value.code == "ATTEMPT_IDENTITY_INVALID"


def test_source_record_identity_is_closed_sorted_and_digest_bound():
    record = SourceRecord(
        role="control.writer",
        locator="github://owner/repo/gwo-control/.gwo-v8/writer-transition.json",
        repository="owner/repo",
        read_mode="GET_AT_OID",
        identity=(("blob_oid", "b" * 40), ("commit_oid", "a" * 40)),
        content_sha256="c" * 64,
        readback_digest=None,
        producer_sha256="d" * 64,
    )
    assert record.digest == digest_value(record.canonical())


@pytest.mark.parametrize(
    "substitution",
    ("run_id", "challenge_nonce", "repository", "evidence_root", "subject"),
)
def test_attested_bundle_rejects_attempt_or_subject_substitution(
    valid_bundle, substitution
):
    if substitution == "subject":
        forged = replace(
            valid_bundle,
            subject=replace(valid_bundle.subject, target_branch="release"),
        )
    elif substitution == "challenge_nonce":
        forged = replace(
            valid_bundle,
            attempt=replace(valid_bundle.attempt, challenge_nonce="a" * 32),
        )
    else:
        forged = replace(
            valid_bundle,
            attempt=replace(
                valid_bundle.attempt,
                **{
                    substitution: (
                        "other/repo"
                        if substitution == "repository"
                        else "other"
                    )
                },
            ),
        )
    with pytest.raises(BootstrapError) as error:
        forged.validate()
    assert error.value.code == "ATTESTATION_INVALID"


def test_attested_bundle_rejects_missing_field_binding(valid_bundle):
    forged = replace(valid_bundle, field_bindings=valid_bundle.field_bindings[:-1])
    with pytest.raises(BootstrapError) as error:
        forged.validate()
    assert error.value.code == "ATTESTATION_INVALID"


def test_attested_bundle_rejects_unknown_field_binding(valid_bundle):
    source_digest = valid_bundle.source_records[0].digest
    forged = replace(
        valid_bundle,
        field_bindings=valid_bundle.field_bindings[:-1]
        + (
            FieldBinding(
                target="unknown.field",
                source_record_digests=(source_digest,),
                derivation="fixture.canonical",
            ),
        ),
    )
    with pytest.raises(BootstrapError) as error:
        forged.validate()
    assert error.value.code == "ATTESTATION_INVALID"


def test_attested_bundle_binds_digest_and_returns_exact_cutover_bundle(valid_bundle):
    valid_bundle.validate()
    assert valid_bundle.attestation_digest == digest_value(
        valid_bundle.canonical_without_attestation_digest()
    )
    cutover = valid_bundle.cutover_bundle()
    assert type(cutover) is CutoverReadbackBundle
    assert cutover.schema == "gwo.cutover-readback-bundle.v1"
    assert cutover.subject is valid_bundle.subject
    assert cutover.runtime is valid_bundle.runtime


def _bundle_with_source_records(bundle, source_records):
    source_digests = tuple(record.digest for record in source_records)
    component = ComponentObservation(
        readbacks=tuple((name, getattr(bundle, name)) for name in READBACK_NAMES),
        source_records=tuple(source_records),
        field_bindings=tuple(
            replace(
                binding,
                source_record_digests=source_digests,
            )
            for binding in bundle.field_bindings
        ),
    )
    return AttestedCutoverBundle.create(
        attempt=bundle.attempt,
        subject=bundle.subject,
        components=(component,),
    )


def test_attested_bundle_rejects_reversed_source_record_digest_order(valid_bundle):
    second = replace(
        valid_bundle.source_records[0],
        role="fixture.second",
        locator="fixture://owner/repo/second",
        identity=(("fixture_id", "fixture-2"),),
        content_sha256="b" * 64,
    )
    ordered_records = tuple(
        sorted(
            (valid_bundle.source_records[0], second),
            key=lambda record: record.digest,
        )
    )
    ordered = _bundle_with_source_records(valid_bundle, ordered_records)
    assert tuple(record.digest for record in ordered.source_records) == tuple(
        sorted(record.digest for record in ordered_records)
    )
    with pytest.raises(BootstrapError) as error:
        _bundle_with_source_records(valid_bundle, ordered_records[::-1])
    assert error.value.code == "ATTESTATION_INVALID"


def test_attested_bundle_rejects_wrong_exact_main_readback_type(valid_bundle):
    forged = replace(valid_bundle, legacy=valid_bundle.subject)
    with pytest.raises(BootstrapError) as error:
        forged.validate()
    assert error.value.code == "ATTESTATION_INVALID"


def test_attested_bundle_rejects_stale_inner_readback_digest(valid_bundle):
    forged = replace(
        valid_bundle,
        legacy=replace(valid_bundle.legacy, readback_digest="0" * 64),
    )
    with pytest.raises(BootstrapError) as error:
        forged.validate()
    assert error.value.code == "ATTESTATION_INVALID"


def test_attested_bundle_rejects_source_record_producer_mismatch(valid_bundle):
    forged = replace(
        valid_bundle,
        source_records=(
            replace(
                valid_bundle.source_records[0],
                producer_sha256="f" * 64,
            ),
        ),
    )
    with pytest.raises(BootstrapError) as error:
        forged.validate()
    assert error.value.code == "ATTESTATION_INVALID"


def test_attested_bundle_rejects_direct_stale_attestation_digest(valid_bundle):
    forged = replace(valid_bundle, attestation_digest="0" * 64)
    with pytest.raises(BootstrapError) as error:
        forged.validate()
    assert error.value.code == "ATTESTATION_INVALID"


@pytest.fixture
def valid_record():
    return SourceRecord(
        role="fixture.source",
        locator="fixture://owner/repo/source",
        repository="owner/repo",
        read_mode="FIXTURE",
        identity=(("fixture_id", "fixture-1"),),
        content_sha256="a" * 64,
        readback_digest=None,
        producer_sha256="b" * 64,
    )


def test_frozen_port_returns_one_exact_value_and_rejects_wrong_arguments():
    value = object()
    port = FrozenReadPort(value, expected_args=("owner/repo",))
    assert port.read("owner/repo") is value
    assert port.read("owner/repo") is value
    with pytest.raises(BootstrapError):
        port.read("other/repo")
    with pytest.raises(BootstrapError):
        port.read(repository="owner/repo")


def test_capability_check_rejects_any_mutator_surface():
    class Unsafe:
        def read(self):
            return object()

        def compare_and_swap(self):
            raise AssertionError("must not be called")

    with pytest.raises(BootstrapError) as error:
        require_read_only_surface(Unsafe(), required_method="read")
    assert error.value.code == "UNSAFE_SOURCE_CAPABILITY"


def test_bootstrap_lease_maps_changed_source_record_to_input_drift(valid_record):
    lease = BootstrapLease(
        expected_records=(valid_record,),
        probes=(lambda: replace(valid_record, content_sha256="e" * 64),),
        local_assertions=(),
        closers=(),
    )
    with pytest.raises(BootstrapError) as error:
        lease.assert_stable()
    assert error.value.code == "LIVE_INPUT_DRIFT"


def test_bootstrap_lease_closes_in_reverse_order_only_once():
    calls = []
    lease = BootstrapLease(
        expected_records=(),
        probes=(),
        local_assertions=(),
        closers=(lambda: calls.append("first"), lambda: calls.append("second")),
    )
    lease.close()
    lease.close()
    assert calls == ["second", "first"]


# --- RED: P2-4 exact read-only capability allowlist ---

def test_capability_check_rejects_unlisted_public_method():
    """A source with an extra public method beyond 'read' must be rejected."""

    class Sneaky:
        def read(self):
            return object()

        def write_file(self):
            raise AssertionError("must not be callable")

    with pytest.raises(BootstrapError) as error:
        require_read_only_surface(Sneaky(), required_method="read")
    assert error.value.code == "UNSAFE_SOURCE_CAPABILITY"


def test_capability_check_rejects_custom_dir_hiding_mutator():
    """A source that overrides __dir__ to hide a mutator must still be rejected."""

    class Hiding:
        def read(self):
            return object()

        def synchronize(self):
            raise AssertionError("must not be callable")

        def __dir__(self):
            return ["read"]

    with pytest.raises(BootstrapError) as error:
        require_read_only_surface(Hiding(), required_method="read")
    assert error.value.code == "UNSAFE_SOURCE_CAPABILITY"


def test_capability_check_accepts_exact_read_only_surface():
    """A source exposing only 'read' must pass."""

    class Exact:
        def read(self):
            return object()

    require_read_only_surface(Exact(), required_method="read")


def test_capability_check_rejects_instance_assigned_public_callable():
    class InstanceAssigned:
        def __init__(self):
            self.publish = lambda: object()

        def read(self):
            return object()

    with pytest.raises(BootstrapError) as error:
        require_read_only_surface(InstanceAssigned(), required_method="read")
    assert error.value.code == "UNSAFE_SOURCE_CAPABILITY"


def test_capability_check_rejects_callable_property():
    class CallableProperty:
        def read(self):
            return object()

        @property
        def publish(self):
            return lambda: object()

    with pytest.raises(BootstrapError) as error:
        require_read_only_surface(CallableProperty(), required_method="read")
    assert error.value.code == "UNSAFE_SOURCE_CAPABILITY"


def test_capability_check_rejects_dynamic_getattr_callable():
    class Dynamic:
        def read(self):
            return object()

        def __getattr__(self, name):
            if name == "publish":
                return lambda: object()
            raise AttributeError(name)

    with pytest.raises(BootstrapError) as error:
        require_read_only_surface(Dynamic(), required_method="read")
    assert error.value.code == "UNSAFE_SOURCE_CAPABILITY"
