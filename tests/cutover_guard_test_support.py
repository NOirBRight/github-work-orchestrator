from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from pathlib import Path
from types import SimpleNamespace

from gwo_v8._canonical import digest_value
from gwo_v8.activation import (
    ActivationError,
    ActivationReceipt,
    InMemoryDurablePlanControl,
    LocalPlanPublication,
)
from gwo_v8.compiler import CompiledPlan

from gwo_v8.cutover_guard import (
    CompatibilityPathReadback,
    CutoverGuard,
    CutoverGuardReport,
    CutoverGuardSources,
    CutoverSubject,
    DurableStateReadback,
    LegacyReadback,
    OwnershipReadback,
    PackageIdentity,
    PackageReadback,
    RuntimePreflightReadback,
    RuntimeSelectorReadback,
    WriterFenceReadback,
    DEFAULT_FORBIDDEN_PRODUCTION_REFS,
    REQUIRED_RUNTIME_SELECTORS,
)
from gwo_v8.plan_control_host import (
    ProductionCutoverGuardHost,
    ProductionCutoverReadAdapterResolver,
    install_cutover_guard,
)
from gwo_v8.runtime_gateway import ProfileMapping, RuntimeConfiguration
from gwo_v8.runtime_profile import RuntimeProfile
from gwo_v8.transition import (
    CanaryAcceptance,
    CanaryRunVerifier,
    InMemoryLegacyWriterControl,
    InMemoryCanaryEvidenceControl,
    InMemoryV8OwnershipControl,
    InMemoryWriterTransitionControl,
    V8OwnershipReadback,
    WriterCutoverController,
    WriterTransitionRecord,
)


class MutationTripwire:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def record(self, operation: str) -> None:
        self.calls.append(operation)


_FORBIDDEN = {
    "stop": "repository",
    "restore": "repository",
    "drain": "repository",
    "publish": "github",
    "compare_and_swap": "github",
    "write": "sqlite",
    "prepare": "runtime",
    "command": "runtime",
    "events": "runtime",
    "install": "process",
    "start": "process",
}


class FakeReadPort:
    def __init__(
        self,
        value: object,
        *,
        category: str,
        external_writes: dict[str, int],
    ) -> None:
        self.value = value
        self.category = category
        self.external_writes = external_writes
        self.reads = 0
        self.raise_error: Exception | None = None

    def read(self, *_args: object, **_kwargs: object) -> object:
        self.reads += 1
        if self.raise_error is not None:
            raise self.raise_error
        return self.value

    def __getattr__(self, name: str) -> object:
        category = _FORBIDDEN.get(name)
        if category is not None:
            self.external_writes[category] += 1
            raise AssertionError(f"Guard attempted forbidden {name}()")
        raise AttributeError(name)


class GuardHarness:
    def __init__(self) -> None:
        self.external_writes = {
            "repository": 0,
            "sqlite": 0,
            "github": 0,
            "process": 0,
            "runtime": 0,
        }
        self._tripwire = MutationTripwire()

    @classmethod
    def valid(cls) -> "GuardHarness":
        harness = cls()
        subject = CutoverSubject(
            repository="owner/repo",
            control_branch="gwo-control",
            target_branch="main",
            source_writer_generation="v6.1",
            target_writer_generation="v8",
            store_generation="store:v8:0001",
            source_commit="a" * 40,
            source_tree_digest="b" * 64,
            production_entry_refs=(
                "gwo_v8.plan_control_host:ProductionPlanControlStartHost.start",
                "gwo_v8.execution_kernel:advance",
                "gwo_v8.execution_kernel:inspect",
            ),
        )
        source_packages = tuple(
            PackageIdentity(name, "8.0.0", "c" * 64, "d" * 64, None)
            for name in subject.package_names
        )
        installed_packages = tuple(
            PackageIdentity(name, "8.0.0", "c" * 64, "d" * 64, surface)
            for surface in subject.install_surfaces
            for name in subject.package_names
        )
        harness.subject = subject
        harness.legacy = FakeReadPort(
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
                durable_state_digest="e" * 64,
            ),
            category="repository",
            external_writes=harness.external_writes,
        )
        harness.durable = FakeReadPort(
            DurableStateReadback(
                repository="owner/repo",
                generation_id="store:v8:0001",
                state_schema="gwo.v8.store.v1",
                compatible=True,
                active_plan_digests=(),
                pending_activation_ids=(),
                predecessor_identity_refs=(),
                readback_digest="f" * 64,
            ),
            category="sqlite",
            external_writes=harness.external_writes,
        )
        harness.writer = FakeReadPort(
            WriterFenceReadback(
                repository="owner/repo",
                writer_generation="v6.1",
                authority_state="authoritative",
                record_id="writer-record:one",
                activation_id=None,
                control_ref_digest="1" * 64,
                readback_digest="2" * 64,
            ),
            category="github",
            external_writes=harness.external_writes,
        )
        harness.ownership = FakeReadPort(
            OwnershipReadback(
                repository="owner/repo",
                active_admissions=(),
                active_attempts=(),
                integration_lease_owner=None,
                runtime_resource_refs=(),
                readback_digest="3" * 64,
            ),
            category="repository",
            external_writes=harness.external_writes,
        )
        harness.compatibility = FakeReadPort(
            CompatibilityPathReadback(
                repository="owner/repo",
                source_commit="a" * 40,
                source_tree_digest="b" * 64,
                audit_version="gwo.cutover-path-audit.v1",
                reachable_v2_projection_refs=(),
                reachable_v3_compatibility_refs=(),
                reachable_legacy_writer_refs=(),
                proven_unreachable_refs=tuple(sorted(DEFAULT_FORBIDDEN_PRODUCTION_REFS)),
                readback_digest="4" * 64,
            ),
            category="process",
            external_writes=harness.external_writes,
        )
        harness.runtime = FakeReadPort(
            RuntimePreflightReadback(
                repository="owner/repo",
                selectors=tuple(
                    RuntimeSelectorReadback(
                        selector=selector,
                        profile_digest="5" * 64,
                        fallback_profile_digest=None,
                        configuration_source="host_global",
                    )
                    for selector in REQUIRED_RUNTIME_SELECTORS
                ),
                configuration_digest="6" * 64,
                provider_action_refs=(),
                persistence_write_refs=(),
                readback_digest="7" * 64,
            ),
            category="runtime",
            external_writes=harness.external_writes,
        )
        harness.packages = FakeReadPort(
            PackageReadback(
                source_packages=source_packages,
                installed_packages=installed_packages,
                drift=(),
                readback_digest="8" * 64,
            ),
            category="process",
            external_writes=harness.external_writes,
        )
        for port in (
            harness.legacy,
            harness.durable,
            harness.writer,
            harness.ownership,
            harness.compatibility,
            harness.runtime,
            harness.packages,
        ):
            body = asdict(port.value)
            body.pop("readback_digest")
            port.value = replace(
                port.value,
                readback_digest=digest_value(body),
            )
        harness.sources = CutoverGuardSources(
            legacy=harness.legacy,
            durable_state=harness.durable,
            writer_fence=harness.writer,
            ownership=harness.ownership,
            compatibility=harness.compatibility,
            runtime=harness.runtime,
            packages=harness.packages,
        )
        return harness

    def read_call_counts(self) -> dict[str, int]:
        return {
            "legacy": self.legacy.reads,
            "durable_state": self.durable.reads,
            "writer_fence": self.writer.reads,
            "ownership": self.ownership.reads,
            "compatibility": self.compatibility.reads,
            "runtime": self.runtime.reads,
            "packages": self.packages.reads,
        }

    def mutation_calls(self) -> tuple[str, ...]:
        return tuple(self._tripwire.calls)


def valid_runtime_configuration() -> RuntimeConfiguration:
    profile = RuntimeProfile(
        name="test-profile",
        provider="test-provider",
        model="test-model",
        thinking="standard",
        mode="batch",
        features={},
    )
    mapping = {
        selector: ProfileMapping(profile.digest)
        for selector in REQUIRED_RUNTIME_SELECTORS
    }
    return RuntimeConfiguration(
        profiles={profile.digest: profile},
        host_mappings=mapping,
        repository_mappings={"owner/repo": mapping},
    )


def runtime_configuration_without(selector: str) -> RuntimeConfiguration:
    configuration = valid_runtime_configuration()
    host_mappings = {
        key: value
        for key, value in configuration.host_mappings.items()
        if getattr(key, "value", key) != selector
    }
    repository_mappings = {
        repository: {
            key: value
            for key, value in mappings.items()
            if getattr(key, "value", key) != selector
        }
        for repository, mappings in configuration.repository_mappings.items()
    }
    return RuntimeConfiguration(
        profiles=dict(configuration.profiles),
        host_mappings=host_mappings,
        repository_mappings=repository_mappings,
    )


def forbidden_runtime_gateway_constructor(*_args: object, **_kwargs: object) -> None:
    raise AssertionError("RuntimeConfigurationReader constructed RuntimeGateway")


def forbidden_sqlite_connect(*_args: object, **_kwargs: object) -> None:
    raise AssertionError("RuntimeConfigurationReader opened SQLite")


class _StartHostReadbackDouble:
    def __init__(self) -> None:
        self.read_active = object()
        self.start = object()

    def install_cutover_guard(
        self,
        *,
        sources: CutoverGuardSources,
    ) -> ProductionCutoverGuardHost:
        return install_cutover_guard(sources=sources)


def valid_production_start_host(_tmp_path: Path) -> _StartHostReadbackDouble:
    return _StartHostReadbackDouble()


class RecordingLegacyControl(InMemoryLegacyWriterControl):
    def __init__(self, calls: list[str]) -> None:
        super().__init__()
        self._calls = calls

    def stop(self, repository: str, *, action_key: str) -> None:
        self._calls.append("legacy.stop")
        super().stop(repository, action_key=action_key)

    def restore(self, repository: str, *, action_key: str) -> None:
        self._calls.append("legacy.restore")
        super().restore(repository, action_key=action_key)

    def readback(self, repository: str) -> SimpleNamespace:
        readback = super().readback(repository)
        return SimpleNamespace(
            repository=readback.repository,
            stopped=readback.stopped,
            active_dispatches=readback.active_dispatches,
            integration_lease=readback.integration_lease,
            active_workers=readback.active_workers,
            authority_state=(
                "stopped" if readback.stopped else "authoritative_quiescent"
            ),
        )


class RecordingTransitions(InMemoryWriterTransitionControl):
    def __init__(self, calls: list[str]) -> None:
        super().__init__(initial_writer="v6.1")
        self._calls = calls

    def publish(self, record: WriterTransitionRecord) -> None:
        self._calls.append("transitions.publish")
        super().publish(record)


class RecordingPublication(LocalPlanPublication):
    def __init__(self, *args: object, calls: list[str], **kwargs: object) -> None:
        super().__init__(*args, **kwargs)
        self._calls = calls

    def publish_and_activate(self, *args: object, **kwargs: object) -> ActivationReceipt:
        self._calls.append("publication.publish_and_activate")
        return super().publish_and_activate(*args, **kwargs)

    def read_active(self, repository: str) -> object | None:
        try:
            return super().read_active(repository)
        except ActivationError as error:
            if error.code != "ACTIVATION_PENDING":
                raise
            return None


@dataclass
class ActivationFixture:
    repository: str
    compiled_plan: CompiledPlan
    accepted_canary: CanaryAcceptance
    canary_evidence_control: InMemoryCanaryEvidenceControl
    subject: CutoverSubject
    guard: ProductionCutoverGuardHost
    writer_readback: FakeReadPort
    legacy: RecordingLegacyControl
    transitions: RecordingTransitions
    publication: RecordingPublication
    ownership: InMemoryV8OwnershipControl
    controller: WriterCutoverController
    calls: list[str]

    def mutation_calls(self) -> tuple[str, ...]:
        return tuple(self.calls)


def activation_fixture(
    tmp_path: Path,
    *,
    fail_after: set[str] | frozenset[str] = frozenset(),
) -> ActivationFixture:
    from test_orchestrator_v8_phase4bc import _accepted_canary, _compiled

    calls: list[str] = []
    harness = GuardHarness.valid()
    compiled_plan = _compiled()
    harness.subject = replace(
        harness.subject,
        repository=compiled_plan.repository,
    )
    for port in (
        harness.legacy,
        harness.durable,
        harness.writer,
        harness.ownership,
        harness.compatibility,
        harness.runtime,
    ):
        value = replace(port.value, repository=compiled_plan.repository)
        body = asdict(value)
        body.pop("readback_digest")
        port.value = replace(
            value,
            readback_digest=digest_value(body),
        )
    canary_readback = _accepted_canary(repository=compiled_plan.repository)
    evidence = (
        *canary_readback.scenario_evidence.values(),
        *canary_readback.candidate_evidence.values(),
        *canary_readback.review_evidence.values(),
    )
    canary_evidence_control = InMemoryCanaryEvidenceControl(tuple(evidence))
    accepted_canary = CanaryRunVerifier(canary_evidence_control).verify(
        canary_readback,
    )
    legacy = RecordingLegacyControl(calls)
    transitions = RecordingTransitions(calls)

    def checkpoint(name: str) -> None:
        if name in fail_after:
            raise RuntimeError(f"isolated rehearsal failure at {name}")

    durable = InMemoryDurablePlanControl(
        fail_once_after=(
            {"publish_activation"}
            if "publish_activation" in fail_after
            else set()
        )
    )
    publication = RecordingPublication(
        tmp_path / "v8.sqlite3",
        durable=durable,
        writer_authority=transitions,
        checkpoint=checkpoint,
        calls=calls,
    )
    guard = install_cutover_guard(sources=harness.sources)
    ownership = InMemoryV8OwnershipControl(
        V8OwnershipReadback(
            active_admissions=(),
            active_attempts=(),
            integration_lease=False,
            runtime_resources=(),
        )
    )
    controller = WriterCutoverController(
        legacy=legacy,
        transitions=transitions,
        publication=publication,
        guard=guard,
    )
    return ActivationFixture(
        repository=compiled_plan.repository,
        compiled_plan=compiled_plan,
        accepted_canary=accepted_canary,
        canary_evidence_control=canary_evidence_control,
        subject=harness.subject,
        guard=guard,
        writer_readback=harness.writer,
        legacy=legacy,
        transitions=transitions,
        publication=publication,
        ownership=ownership,
        controller=controller,
        calls=calls,
    )


class MutatingLegacyReader:
    def read(self, repository: str) -> LegacyReadback:
        return GuardHarness.valid().legacy.read(repository)

    def stop(self, repository: str) -> None:
        raise AssertionError(f"unexpected stop({repository})")

    def restore(self, repository: str) -> None:
        raise AssertionError(f"unexpected restore({repository})")


def valid_cutover_read_adapter_resolver(
    runtime_configuration: RuntimeConfiguration | None = None,
) -> ProductionCutoverReadAdapterResolver:
    harness = GuardHarness.valid()
    return ProductionCutoverReadAdapterResolver(
        legacy=harness.legacy,
        durable_state=harness.durable,
        writer_fence=harness.writer,
        ownership=harness.ownership,
        runtime_configuration=(
            valid_runtime_configuration()
            if runtime_configuration is None
            else runtime_configuration
        ),
    )


EXPECTED_CHECK_IDS = (
    "source_writer",
    "legacy_quiescence",
    "durable_state",
    "writer_and_lease",
    "production_paths",
    "runtime_configuration",
    "package_installation",
)


def write_valid_bundle(path: Path, *, running_v2: bool = False) -> Path:
    from dataclasses import asdict, replace
    from gwo_v8._canonical import canonical_bytes, digest_value
    from gwo_v8.cutover_guard import (
        CutoverReadbackBundle,
        READBACK_BUNDLE_SCHEMA,
    )

    harness = GuardHarness.valid()
    if running_v2:
        harness.legacy.value = replace(
            harness.legacy.value,
            v2_execution_refs=("v2:running",),
            v2_execution_state="running",
        )
        legacy_body = asdict(harness.legacy.value)
        legacy_body.pop("readback_digest")
        harness.legacy.value = replace(
            harness.legacy.value,
            readback_digest=digest_value(legacy_body),
        )
    bundle = CutoverReadbackBundle(
        schema=READBACK_BUNDLE_SCHEMA,
        subject=harness.subject,
        legacy=harness.legacy.value,
        durable_state=harness.durable.value,
        writer_fence=harness.writer.value,
        ownership=harness.ownership.value,
        compatibility=harness.compatibility.value,
        runtime=harness.runtime.value,
        packages=harness.packages.value,
    )
    path.write_bytes(canonical_bytes(bundle.canonical()))
    return path


class RecordingGuard:
    def __init__(self, sources: CutoverGuardSources, calls: list[str]) -> None:
        self._sources = sources
        self._calls = calls

    def evaluate(self, subject: CutoverSubject) -> CutoverGuardReport:
        self._calls.append("evaluate")
        return CutoverGuard(self._sources).evaluate(subject)


class RecordingLiveHost:
    def __init__(self, subject: CutoverSubject, calls: list[str]) -> None:
        self._subject = subject
        self._calls = calls

    def check(self, subject: CutoverSubject) -> CutoverGuardReport:
        self._calls.append("check")
        if subject != self._subject:
            raise AssertionError("live factory received a different subject")
        harness = GuardHarness.valid()
        compatibility = replace(
            harness.compatibility.value,
            source_tree_digest=subject.source_tree_digest,
        )
        compatibility_body = asdict(compatibility)
        compatibility_body.pop("readback_digest")
        harness.compatibility.value = replace(
            compatibility,
            readback_digest=digest_value(compatibility_body),
        )
        return CutoverGuard(harness.sources).evaluate(subject)
