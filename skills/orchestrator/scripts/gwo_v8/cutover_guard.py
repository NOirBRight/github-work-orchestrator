"""Read-only, digest-bound V6.1-to-V8 cutover Guard contract."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
from typing import Any, Callable, Literal, Mapping, Protocol

from ._canonical import canonical_bytes, digest_value, load_canonical_json


GUARD_SCHEMA = "gwo.cutover-guard.v1"
RECEIPT_SCHEMA = "gwo.cutover-guard-receipt.v1"
EXPECTED_SOURCE_WRITER_GENERATION = "v6.1"
REQUIRED_RUNTIME_SELECTORS = (
    "coordinator",
    "worker",
    "recovery_worker",
    "review_primary",
    "review_strong",
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
DEFAULT_FORBIDDEN_PRODUCTION_REFS = (
    "gwo_v8.entry:ImplementGwoEntry",
    "gwo_v8.entry:ImplementGwoLauncher",
    "gwo_v8.goal_driver:GoalDriver",
    "gwo_v8.kernel:Kernel.reconcile_once",
    "gwo_v8.reconstruction:StoreReconstructor",
    "gwo_v8.runtime:PaseoRuntimeAdapter",
    "skills/implement-gwo:PlanCompiler",
    "skills/implement-gwo:LocalPlanPublication",
    "skills/implement-gwo:Kernel.reconcile_once",
)
READBACK_BUNDLE_SCHEMA = "gwo.cutover-readback-bundle.v1"


_GUARD_DIGEST = re.compile(r"^[0-9a-f]{64}$")


def _valid_guard_digest(value: object) -> bool:
    return type(value) is str and _GUARD_DIGEST.fullmatch(value) is not None


def _plain(value: object) -> object:
    if isinstance(value, _CanonicalValue):
        return value.canonical()
    if type(value) is tuple:
        return [_plain(item) for item in value]
    if type(value) is list:
        return [_plain(item) for item in value]
    if type(value) is dict:
        return {key: _plain(child) for key, child in value.items()}
    return value


def _text_fields(value: object, expected: type, names: tuple[str, ...]) -> None:
    if type(value) is not expected:
        raise TypeError(f"{expected.__name__} must be an exact immutable value")
    for name in names:
        if type(getattr(value, name)) is not str:
            raise TypeError(f"{expected.__name__}.{name} must be exact text")


def _tuple_fields(
    value: object,
    expected: type,
    names: tuple[str, ...],
    *,
    item_type: type = str,
) -> None:
    for name in names:
        items = getattr(value, name)
        if type(items) is not tuple or any(type(item) is not item_type for item in items):
            raise TypeError(
                f"{expected.__name__}.{name} must be an exact tuple of "
                f"{item_type.__name__}"
            )


def _optional_text_field(value: object, expected: type, name: str) -> None:
    item = getattr(value, name)
    if item is not None and type(item) is not str:
        raise TypeError(f"{expected.__name__}.{name} must be exact text or None")


def _exact_type(value: object, expected: type, name: str) -> None:
    if type(value) is not expected:
        raise TypeError(f"{name} must be an exact {expected.__name__}")


class _CanonicalValue:
    def canonical(self) -> dict[str, Any]:
        raise NotImplementedError


@dataclass(frozen=True)
class CutoverSubject(_CanonicalValue):
    repository: str
    control_branch: str
    target_branch: str
    source_writer_generation: str
    target_writer_generation: str
    store_generation: str
    source_commit: str
    source_tree_digest: str
    production_entry_refs: tuple[str, ...]
    forbidden_production_refs: tuple[str, ...] = DEFAULT_FORBIDDEN_PRODUCTION_REFS
    required_runtime_selectors: tuple[str, ...] = REQUIRED_RUNTIME_SELECTORS
    package_names: tuple[str, ...] = ("implement-gwo", "orchestrator")
    install_surfaces: tuple[str, ...] = (".agents", ".codex", ".claude")

    def __post_init__(self) -> None:
        _text_fields(
            self,
            CutoverSubject,
            (
                "repository",
                "control_branch",
                "target_branch",
                "source_writer_generation",
                "target_writer_generation",
                "store_generation",
                "source_commit",
                "source_tree_digest",
            ),
        )
        _tuple_fields(
            self,
            CutoverSubject,
            (
                "production_entry_refs",
                "forbidden_production_refs",
                "required_runtime_selectors",
                "package_names",
                "install_surfaces",
            ),
        )

    def canonical(self) -> dict[str, Any]:
        return {
            "repository": self.repository,
            "control_branch": self.control_branch,
            "target_branch": self.target_branch,
            "source_writer_generation": self.source_writer_generation,
            "target_writer_generation": self.target_writer_generation,
            "store_generation": self.store_generation,
            "source_commit": self.source_commit,
            "source_tree_digest": self.source_tree_digest,
            "production_entry_refs": list(self.production_entry_refs),
            "forbidden_production_refs": list(self.forbidden_production_refs),
            "required_runtime_selectors": list(self.required_runtime_selectors),
            "package_names": list(self.package_names),
            "install_surfaces": list(self.install_surfaces),
        }


@dataclass(frozen=True)
class LegacyReadback(_CanonicalValue):
    repository: str
    writer_generation: str
    authority_state: Literal["active", "authoritative_quiescent", "stopped"]
    active_dispatches: tuple[str, ...]
    active_workers: tuple[str, ...]
    integration_lease_owner: str | None
    v2_execution_refs: tuple[str, ...]
    v2_execution_state: Literal["none", "running", "terminal", "quiescent_read_only"]
    original_decoder_readable: bool
    durable_state_digest: str
    readback_digest: str = ""

    def __post_init__(self) -> None:
        _text_fields(
            self,
            LegacyReadback,
            (
                "repository",
                "writer_generation",
                "authority_state",
                "v2_execution_state",
                "durable_state_digest",
                "readback_digest",
            ),
        )
        _tuple_fields(self, LegacyReadback, ("active_dispatches", "active_workers", "v2_execution_refs"))
        _optional_text_field(self, LegacyReadback, "integration_lease_owner")
        _exact_type(self.original_decoder_readable, bool, "original_decoder_readable")

    def canonical(self) -> dict[str, Any]:
        return {
            "repository": self.repository,
            "writer_generation": self.writer_generation,
            "authority_state": self.authority_state,
            "active_dispatches": list(self.active_dispatches),
            "active_workers": list(self.active_workers),
            "integration_lease_owner": self.integration_lease_owner,
            "v2_execution_refs": list(self.v2_execution_refs),
            "v2_execution_state": self.v2_execution_state,
            "original_decoder_readable": self.original_decoder_readable,
            "durable_state_digest": self.durable_state_digest,
            "readback_digest": self.readback_digest,
        }


@dataclass(frozen=True)
class DurableStateReadback(_CanonicalValue):
    repository: str
    generation_id: str
    state_schema: str
    compatible: bool
    active_plan_digests: tuple[str, ...]
    pending_activation_ids: tuple[str, ...]
    predecessor_identity_refs: tuple[str, ...]
    readback_digest: str

    def __post_init__(self) -> None:
        _text_fields(
            self,
            DurableStateReadback,
            ("repository", "generation_id", "state_schema", "readback_digest"),
        )
        _tuple_fields(
            self,
            DurableStateReadback,
            ("active_plan_digests", "pending_activation_ids", "predecessor_identity_refs"),
        )
        _exact_type(self.compatible, bool, "compatible")

    def canonical(self) -> dict[str, Any]:
        return {
            "repository": self.repository,
            "generation_id": self.generation_id,
            "state_schema": self.state_schema,
            "compatible": self.compatible,
            "active_plan_digests": list(self.active_plan_digests),
            "pending_activation_ids": list(self.pending_activation_ids),
            "predecessor_identity_refs": list(self.predecessor_identity_refs),
            "readback_digest": self.readback_digest,
        }


@dataclass(frozen=True)
class WriterFenceReadback(_CanonicalValue):
    repository: str
    writer_generation: str
    authority_state: Literal["authoritative", "draining", "cut_over"]
    record_id: str
    activation_id: str | None
    control_ref_digest: str
    readback_digest: str

    def __post_init__(self) -> None:
        _text_fields(
            self,
            WriterFenceReadback,
            (
                "repository",
                "writer_generation",
                "authority_state",
                "record_id",
                "control_ref_digest",
                "readback_digest",
            ),
        )
        _optional_text_field(self, WriterFenceReadback, "activation_id")

    def canonical(self) -> dict[str, Any]:
        return {
            "repository": self.repository,
            "writer_generation": self.writer_generation,
            "authority_state": self.authority_state,
            "record_id": self.record_id,
            "activation_id": self.activation_id,
            "control_ref_digest": self.control_ref_digest,
            "readback_digest": self.readback_digest,
        }


@dataclass(frozen=True)
class OwnershipReadback(_CanonicalValue):
    repository: str
    active_admissions: tuple[str, ...]
    active_attempts: tuple[str, ...]
    integration_lease_owner: str | None
    runtime_resource_refs: tuple[str, ...]
    readback_digest: str

    def __post_init__(self) -> None:
        _text_fields(self, OwnershipReadback, ("repository", "readback_digest"))
        _tuple_fields(
            self,
            OwnershipReadback,
            ("active_admissions", "active_attempts", "runtime_resource_refs"),
        )
        _optional_text_field(self, OwnershipReadback, "integration_lease_owner")

    def canonical(self) -> dict[str, Any]:
        return {
            "repository": self.repository,
            "active_admissions": list(self.active_admissions),
            "active_attempts": list(self.active_attempts),
            "integration_lease_owner": self.integration_lease_owner,
            "runtime_resource_refs": list(self.runtime_resource_refs),
            "readback_digest": self.readback_digest,
        }


@dataclass(frozen=True)
class CompatibilityPathReadback(_CanonicalValue):
    repository: str
    source_commit: str
    source_tree_digest: str
    audit_version: str
    reachable_v2_projection_refs: tuple[str, ...]
    reachable_v3_compatibility_refs: tuple[str, ...]
    reachable_legacy_writer_refs: tuple[str, ...]
    proven_unreachable_refs: tuple[str, ...]
    readback_digest: str

    def __post_init__(self) -> None:
        _text_fields(
            self,
            CompatibilityPathReadback,
            (
                "repository",
                "source_commit",
                "source_tree_digest",
                "audit_version",
                "readback_digest",
            ),
        )
        _tuple_fields(
            self,
            CompatibilityPathReadback,
            (
                "reachable_v2_projection_refs",
                "reachable_v3_compatibility_refs",
                "reachable_legacy_writer_refs",
                "proven_unreachable_refs",
            ),
        )

    def canonical(self) -> dict[str, Any]:
        return {
            "repository": self.repository,
            "source_commit": self.source_commit,
            "source_tree_digest": self.source_tree_digest,
            "audit_version": self.audit_version,
            "reachable_v2_projection_refs": list(self.reachable_v2_projection_refs),
            "reachable_v3_compatibility_refs": list(self.reachable_v3_compatibility_refs),
            "reachable_legacy_writer_refs": list(self.reachable_legacy_writer_refs),
            "proven_unreachable_refs": list(self.proven_unreachable_refs),
            "readback_digest": self.readback_digest,
        }


@dataclass(frozen=True)
class RuntimeSelectorReadback(_CanonicalValue):
    selector: str
    profile_digest: str
    fallback_profile_digest: str | None
    configuration_source: Literal["campaign_start", "repository", "host_global"]

    def __post_init__(self) -> None:
        _text_fields(
            self,
            RuntimeSelectorReadback,
            ("selector", "profile_digest", "configuration_source"),
        )
        _optional_text_field(self, RuntimeSelectorReadback, "fallback_profile_digest")

    def canonical(self) -> dict[str, Any]:
        return {
            "selector": self.selector,
            "profile_digest": self.profile_digest,
            "fallback_profile_digest": self.fallback_profile_digest,
            "configuration_source": self.configuration_source,
        }


@dataclass(frozen=True)
class RuntimePreflightReadback(_CanonicalValue):
    repository: str
    selectors: tuple[RuntimeSelectorReadback, ...]
    configuration_digest: str
    provider_action_refs: tuple[str, ...]
    persistence_write_refs: tuple[str, ...]
    readback_digest: str

    def __post_init__(self) -> None:
        _text_fields(
            self,
            RuntimePreflightReadback,
            ("repository", "configuration_digest", "readback_digest"),
        )
        if type(self.selectors) is not tuple or any(
            type(item) is not RuntimeSelectorReadback for item in self.selectors
        ):
            raise TypeError("RuntimePreflightReadback.selectors must be an exact tuple")
        _tuple_fields(
            self,
            RuntimePreflightReadback,
            ("provider_action_refs", "persistence_write_refs"),
        )

    def canonical(self) -> dict[str, Any]:
        return {
            "repository": self.repository,
            "selectors": [item.canonical() for item in self.selectors],
            "configuration_digest": self.configuration_digest,
            "provider_action_refs": list(self.provider_action_refs),
            "persistence_write_refs": list(self.persistence_write_refs),
            "readback_digest": self.readback_digest,
        }


@dataclass(frozen=True)
class PackageIdentity(_CanonicalValue):
    package_name: str
    version: str
    content_digest: str
    manifest_content_digest: str
    install_surface: str | None

    def __post_init__(self) -> None:
        _text_fields(
            self,
            PackageIdentity,
            (
                "package_name",
                "version",
                "content_digest",
                "manifest_content_digest",
            ),
        )
        _optional_text_field(self, PackageIdentity, "install_surface")

    def canonical(self) -> dict[str, Any]:
        return {
            "package_name": self.package_name,
            "version": self.version,
            "content_digest": self.content_digest,
            "manifest_content_digest": self.manifest_content_digest,
            "install_surface": self.install_surface,
        }


@dataclass(frozen=True)
class PackageReadback(_CanonicalValue):
    source_packages: tuple[PackageIdentity, ...]
    installed_packages: tuple[PackageIdentity, ...]
    drift: tuple[str, ...]
    readback_digest: str

    def __post_init__(self) -> None:
        if type(self.source_packages) is not tuple or any(
            type(item) is not PackageIdentity for item in self.source_packages
        ):
            raise TypeError("PackageReadback.source_packages must be an exact tuple")
        if type(self.installed_packages) is not tuple or any(
            type(item) is not PackageIdentity for item in self.installed_packages
        ):
            raise TypeError("PackageReadback.installed_packages must be an exact tuple")
        _tuple_fields(self, PackageReadback, ("drift",))
        _text_fields(self, PackageReadback, ("readback_digest",))

    def canonical(self) -> dict[str, Any]:
        return {
            "source_packages": [item.canonical() for item in self.source_packages],
            "installed_packages": [item.canonical() for item in self.installed_packages],
            "drift": list(self.drift),
            "readback_digest": self.readback_digest,
        }


@dataclass(frozen=True)
class CutoverReadbackBundle(_CanonicalValue):
    schema: str
    subject: CutoverSubject
    legacy: LegacyReadback
    durable_state: DurableStateReadback
    writer_fence: WriterFenceReadback
    ownership: OwnershipReadback
    compatibility: CompatibilityPathReadback
    runtime: RuntimePreflightReadback
    packages: PackageReadback

    def __post_init__(self) -> None:
        _text_fields(self, CutoverReadbackBundle, ("schema",))
        expected = {
            "subject": CutoverSubject,
            "legacy": LegacyReadback,
            "durable_state": DurableStateReadback,
            "writer_fence": WriterFenceReadback,
            "ownership": OwnershipReadback,
            "compatibility": CompatibilityPathReadback,
            "runtime": RuntimePreflightReadback,
            "packages": PackageReadback,
        }
        for name, value_type in expected.items():
            if type(getattr(self, name)) is not value_type:
                raise TypeError(f"CutoverReadbackBundle.{name} has the wrong exact type")

    def canonical(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "subject": self.subject.canonical(),
            "readbacks": {
                "legacy": self.legacy.canonical(),
                "durable_state": self.durable_state.canonical(),
                "writer_fence": self.writer_fence.canonical(),
                "ownership": self.ownership.canonical(),
                "compatibility": self.compatibility.canonical(),
                "runtime": self.runtime.canonical(),
                "packages": self.packages.canonical(),
            },
        }


class LegacyReadPort(Protocol):
    def read(self, repository: str) -> LegacyReadback: ...


class DurableStateReadPort(Protocol):
    def read(self, repository: str) -> DurableStateReadback: ...


class WriterFenceReadPort(Protocol):
    def read(self, repository: str) -> WriterFenceReadback: ...


class OwnershipReadPort(Protocol):
    def read(self, repository: str) -> OwnershipReadback: ...


class CompatibilityPathReadPort(Protocol):
    def read(self, subject: CutoverSubject) -> CompatibilityPathReadback: ...


class RuntimePreflightReadPort(Protocol):
    def read(
        self,
        repository: str,
        selectors: tuple[str, ...],
    ) -> RuntimePreflightReadback: ...


class PackageReadPort(Protocol):
    def read(self, subject: CutoverSubject) -> PackageReadback: ...


@dataclass(frozen=True)
class CutoverGuardSources:
    legacy: LegacyReadPort
    durable_state: DurableStateReadPort
    writer_fence: WriterFenceReadPort
    ownership: OwnershipReadPort
    compatibility: CompatibilityPathReadPort
    runtime: RuntimePreflightReadPort
    packages: PackageReadPort


class GuardActivationValidator(Protocol):
    def validate_activation(
        self,
        subject: CutoverSubject,
        receipt: "CutoverGuardReceipt",
    ) -> None: ...


@dataclass(frozen=True)
class GuardCheck(_CanonicalValue):
    check_id: str
    passed: bool
    observed_digest: str | None

    def __post_init__(self) -> None:
        _text_fields(self, GuardCheck, ("check_id",))
        _optional_text_field(self, GuardCheck, "observed_digest")
        _exact_type(self.passed, bool, "GuardCheck.passed")

    def canonical(self) -> dict[str, Any]:
        return {
            "check_id": self.check_id,
            "passed": self.passed,
            "observed_digest": self.observed_digest,
        }


@dataclass(frozen=True)
class CutoverBlocker(_CanonicalValue):
    code: str
    check_id: str
    observed_digest: str | None
    detail: str

    def __post_init__(self) -> None:
        _text_fields(self, CutoverBlocker, ("code", "check_id", "detail"))
        _optional_text_field(self, CutoverBlocker, "observed_digest")

    def canonical(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "check_id": self.check_id,
            "observed_digest": self.observed_digest,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class CutoverGuardReceipt(_CanonicalValue):
    schema: str
    repository: str
    subject_digest: str
    readback_digest: str
    source_writer_generation: str
    target_writer_generation: str
    store_generation: str
    writer_control_ref_digest: str
    runtime_configuration_digest: str
    compatibility_audit_digest: str
    package_readback_digest: str
    receipt_digest: str

    def __post_init__(self) -> None:
        _text_fields(
            self,
            CutoverGuardReceipt,
            (
                "schema",
                "repository",
                "subject_digest",
                "readback_digest",
                "source_writer_generation",
                "target_writer_generation",
                "store_generation",
                "writer_control_ref_digest",
                "runtime_configuration_digest",
                "compatibility_audit_digest",
                "package_readback_digest",
                "receipt_digest",
            ),
        )

    def canonical_without_digest(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "repository": self.repository,
            "subject_digest": self.subject_digest,
            "readback_digest": self.readback_digest,
            "source_writer_generation": self.source_writer_generation,
            "target_writer_generation": self.target_writer_generation,
            "store_generation": self.store_generation,
            "writer_control_ref_digest": self.writer_control_ref_digest,
            "runtime_configuration_digest": self.runtime_configuration_digest,
            "compatibility_audit_digest": self.compatibility_audit_digest,
            "package_readback_digest": self.package_readback_digest,
        }

    def canonical(self) -> dict[str, Any]:
        value = self.canonical_without_digest()
        value["receipt_digest"] = self.receipt_digest
        return value


@dataclass(frozen=True)
class CutoverGuardReport(_CanonicalValue):
    schema: str
    decision: Literal["GO", "NO_GO"]
    repository: str
    subject_digest: str
    readback_digest: str
    checks: tuple[GuardCheck, ...]
    blockers: tuple[CutoverBlocker, ...]
    receipt: CutoverGuardReceipt | None

    def __post_init__(self) -> None:
        _text_fields(
            self,
            CutoverGuardReport,
            ("schema", "decision", "repository", "subject_digest", "readback_digest"),
        )
        if type(self.checks) is not tuple or any(
            type(item) is not GuardCheck for item in self.checks
        ):
            raise TypeError("CutoverGuardReport.checks must be an exact tuple")
        if type(self.blockers) is not tuple or any(
            type(item) is not CutoverBlocker for item in self.blockers
        ):
            raise TypeError("CutoverGuardReport.blockers must be an exact tuple")
        if self.receipt is not None and type(self.receipt) is not CutoverGuardReceipt:
            raise TypeError("CutoverGuardReport.receipt has the wrong exact type")

    def canonical(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "decision": self.decision,
            "repository": self.repository,
            "subject_digest": self.subject_digest,
            "readback_digest": self.readback_digest,
            "checks": [item.canonical() for item in self.checks],
            "blockers": [item.canonical() for item in self.blockers],
            "receipt": None if self.receipt is None else self.receipt.canonical(),
        }


class CutoverGuardError(RuntimeError):
    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code = code
        self.detail = detail


_READ_ERROR_CODES = {
    "legacy": "CUTOVER_LEGACY_READBACK_INVALID",
    "durable_state": "CUTOVER_DURABLE_STATE_READBACK_INVALID",
    "writer_fence": "CUTOVER_WRITER_FENCE_READBACK_INVALID",
    "ownership": "CUTOVER_OWNERSHIP_READBACK_INVALID",
    "compatibility": "CUTOVER_COMPATIBILITY_READBACK_INVALID",
    "runtime": "CUTOVER_RUNTIME_READBACK_INVALID",
    "packages": "CUTOVER_PACKAGES_READBACK_INVALID",
}
_READBACK_TYPES = {
    "legacy": LegacyReadback,
    "durable_state": DurableStateReadback,
    "writer_fence": WriterFenceReadback,
    "ownership": OwnershipReadback,
    "compatibility": CompatibilityPathReadback,
    "runtime": RuntimePreflightReadback,
    "packages": PackageReadback,
}
_READBACK_FIELDS = {
    "legacy": {
        "repository",
        "writer_generation",
        "authority_state",
        "active_dispatches",
        "active_workers",
        "integration_lease_owner",
        "v2_execution_refs",
        "v2_execution_state",
        "original_decoder_readable",
        "durable_state_digest",
        "readback_digest",
    },
    "durable_state": {
        "repository",
        "generation_id",
        "state_schema",
        "compatible",
        "active_plan_digests",
        "pending_activation_ids",
        "predecessor_identity_refs",
        "readback_digest",
    },
    "writer_fence": {
        "repository",
        "writer_generation",
        "authority_state",
        "record_id",
        "activation_id",
        "control_ref_digest",
        "readback_digest",
    },
    "ownership": {
        "repository",
        "active_admissions",
        "active_attempts",
        "integration_lease_owner",
        "runtime_resource_refs",
        "readback_digest",
    },
    "compatibility": {
        "repository",
        "source_commit",
        "source_tree_digest",
        "audit_version",
        "reachable_v2_projection_refs",
        "reachable_v3_compatibility_refs",
        "reachable_legacy_writer_refs",
        "proven_unreachable_refs",
        "readback_digest",
    },
    "runtime": {
        "repository",
        "selectors",
        "configuration_digest",
        "provider_action_refs",
        "persistence_write_refs",
        "readback_digest",
    },
    "packages": {
        "source_packages",
        "installed_packages",
        "drift",
        "readback_digest",
    },
}
_VALID_LEGACY_AUTHORITY_STATES = {
    "active",
    "authoritative_quiescent",
    "stopped",
}
_VALID_V2_EXECUTION_STATES = {
    "none",
    "running",
    "terminal",
    "quiescent_read_only",
}
_VALID_WRITER_AUTHORITY_STATES = {"authoritative", "draining", "cut_over"}
_VALID_RUNTIME_CONFIGURATION_SOURCES = {
    "campaign_start",
    "repository",
    "host_global",
}


def _validate_readback_fields(name: str, value: object) -> None:
    if type(value) is not _READBACK_TYPES[name]:
        raise TypeError(f"{name} readback has the wrong exact type")

    if name == "legacy":
        _text_fields(
            value,
            LegacyReadback,
            (
                "repository",
                "writer_generation",
                "authority_state",
                "v2_execution_state",
                "durable_state_digest",
                "readback_digest",
            ),
        )
        _tuple_fields(value, LegacyReadback, ("active_dispatches", "active_workers", "v2_execution_refs"))
        _optional_text_field(value, LegacyReadback, "integration_lease_owner")
        _exact_type(value.original_decoder_readable, bool, "original_decoder_readable")
        if value.authority_state not in _VALID_LEGACY_AUTHORITY_STATES:
            raise ValueError("legacy authority state is invalid")
        if value.v2_execution_state not in _VALID_V2_EXECUTION_STATES:
            raise ValueError("legacy V2 execution state is invalid")
        if not _valid_guard_digest(value.durable_state_digest):
            raise ValueError("legacy durable state digest is invalid")
        return

    if name == "durable_state":
        _text_fields(
            value,
            DurableStateReadback,
            ("repository", "generation_id", "state_schema", "readback_digest"),
        )
        _tuple_fields(
            value,
            DurableStateReadback,
            ("active_plan_digests", "pending_activation_ids", "predecessor_identity_refs"),
        )
        _exact_type(value.compatible, bool, "compatible")
        return

    if name == "writer_fence":
        _text_fields(
            value,
            WriterFenceReadback,
            (
                "repository",
                "writer_generation",
                "authority_state",
                "record_id",
                "control_ref_digest",
                "readback_digest",
            ),
        )
        _optional_text_field(value, WriterFenceReadback, "activation_id")
        if value.authority_state not in _VALID_WRITER_AUTHORITY_STATES:
            raise ValueError("writer authority state is invalid")
        if not _valid_guard_digest(value.control_ref_digest):
            raise ValueError("writer control reference digest is invalid")
        return

    if name == "ownership":
        _text_fields(value, OwnershipReadback, ("repository", "readback_digest"))
        _tuple_fields(
            value,
            OwnershipReadback,
            ("active_admissions", "active_attempts", "runtime_resource_refs"),
        )
        _optional_text_field(value, OwnershipReadback, "integration_lease_owner")
        return

    if name == "compatibility":
        _text_fields(
            value,
            CompatibilityPathReadback,
            (
                "repository",
                "source_commit",
                "source_tree_digest",
                "audit_version",
                "readback_digest",
            ),
        )
        _tuple_fields(
            value,
            CompatibilityPathReadback,
            (
                "reachable_v2_projection_refs",
                "reachable_v3_compatibility_refs",
                "reachable_legacy_writer_refs",
                "proven_unreachable_refs",
            ),
        )
        return

    if name == "runtime":
        _text_fields(
            value,
            RuntimePreflightReadback,
            ("repository", "configuration_digest", "readback_digest"),
        )
        if type(value.selectors) is not tuple or any(
            type(item) is not RuntimeSelectorReadback for item in value.selectors
        ):
            raise TypeError("runtime selectors must be an exact tuple of readbacks")
        _tuple_fields(
            value,
            RuntimePreflightReadback,
            ("provider_action_refs", "persistence_write_refs"),
        )
        if not _valid_guard_digest(value.configuration_digest):
            raise ValueError("runtime configuration digest is invalid")
        for selector in value.selectors:
            _text_fields(
                selector,
                RuntimeSelectorReadback,
                ("selector", "profile_digest", "configuration_source"),
            )
            _optional_text_field(selector, RuntimeSelectorReadback, "fallback_profile_digest")
            if selector.configuration_source not in _VALID_RUNTIME_CONFIGURATION_SOURCES:
                raise ValueError("runtime configuration source is invalid")
            if not _valid_guard_digest(selector.profile_digest):
                raise ValueError("runtime selector profile digest is invalid")
            if selector.fallback_profile_digest is not None and not _valid_guard_digest(
                selector.fallback_profile_digest
            ):
                raise ValueError("runtime selector fallback profile digest is invalid")
        return

    if name == "packages":
        if type(value.source_packages) is not tuple or any(
            type(item) is not PackageIdentity for item in value.source_packages
        ):
            raise TypeError("source packages must be an exact tuple of identities")
        if type(value.installed_packages) is not tuple or any(
            type(item) is not PackageIdentity for item in value.installed_packages
        ):
            raise TypeError("installed packages must be an exact tuple of identities")
        _tuple_fields(value, PackageReadback, ("drift",))
        for package in value.source_packages + value.installed_packages:
            _text_fields(
                package,
                PackageIdentity,
                (
                    "package_name",
                    "version",
                    "content_digest",
                    "manifest_content_digest",
                ),
            )
            _optional_text_field(package, PackageIdentity, "install_surface")
            if not _valid_guard_digest(package.content_digest):
                raise ValueError("package content digest is invalid")
            if not _valid_guard_digest(package.manifest_content_digest):
                raise ValueError("package manifest digest is invalid")
        return

    raise ValueError(f"unknown readback {name}")


def _validate_typed_readback(name: str, value: object) -> None:
    _validate_readback_fields(name, value)
    canonical = value.canonical()
    if type(canonical) is not dict or set(canonical) != _READBACK_FIELDS[name]:
        raise ValueError(f"{name} canonical projection has the wrong closed shape")
    canonical_bytes(canonical)
    readback_digest = canonical["readback_digest"]
    if not _valid_guard_digest(readback_digest):
        raise ValueError(f"{name} readback digest has the wrong shape")
    body = dict(canonical)
    del body["readback_digest"]
    if digest_value(body) != readback_digest:
        raise ValueError(f"{name} readback digest does not match its canonical body")


class _ReplayReadPort:
    def __init__(self, value: object) -> None:
        self._value = value

    def read(self, *_args: object, **_kwargs: object) -> object:
        return self._value


def _exact_object(value: object, expected: set[str], label: str) -> dict[str, object]:
    if type(value) is not dict or set(value) != expected:
        raise CutoverGuardError("CUTOVER_BUNDLE_INVALID", f"{label} keys are not exact")
    return value


def _tuple_field(data: dict[str, object], name: str) -> None:
    value = data[name]
    if type(value) is not list:
        raise CutoverGuardError("CUTOVER_BUNDLE_INVALID", f"{name} must be an array")
    data[name] = tuple(value)


def _decode_subject(value: object) -> CutoverSubject:
    fields = {
        "repository",
        "control_branch",
        "target_branch",
        "source_writer_generation",
        "target_writer_generation",
        "store_generation",
        "source_commit",
        "source_tree_digest",
        "production_entry_refs",
        "forbidden_production_refs",
        "required_runtime_selectors",
        "package_names",
        "install_surfaces",
    }
    data = _exact_object(value, fields, "subject")
    for name in (
        "production_entry_refs",
        "forbidden_production_refs",
        "required_runtime_selectors",
        "package_names",
        "install_surfaces",
    ):
        _tuple_field(data, name)
    try:
        return CutoverSubject(**data)
    except (TypeError, ValueError) as error:
        raise CutoverGuardError("CUTOVER_BUNDLE_INVALID", "subject is malformed") from error


def _decode_readback(
    value: object,
    expected_fields: set[str],
    tuple_fields: tuple[str, ...],
    value_type: type,
    label: str,
) -> object:
    try:
        return _decode_readback_inner(
            value,
            expected_fields,
            tuple_fields,
            value_type,
            label,
        )
    except CutoverGuardError as error:
        if error.code == _READ_ERROR_CODES[label]:
            raise
        raise CutoverGuardError(
            _READ_ERROR_CODES[label], f"{label} readback is malformed"
        ) from error
    except Exception as error:
        raise CutoverGuardError(
            _READ_ERROR_CODES[label], f"{label} readback is malformed"
        ) from error


def _decode_readback_inner(
    value: object,
    expected_fields: set[str],
    tuple_fields: tuple[str, ...],
    value_type: type,
    label: str,
) -> object:
    data = _exact_object(value, expected_fields, label)
    for name in tuple_fields:
        _tuple_field(data, name)
    try:
        readback = value_type(**data)
        _validate_typed_readback(label, readback)
        return readback
    except Exception as error:
        raise CutoverGuardError(
            _READ_ERROR_CODES[label], f"{label} readback is malformed"
        ) from error


def _decode_runtime(value: object) -> RuntimePreflightReadback:
    try:
        return _decode_runtime_inner(value)
    except CutoverGuardError as error:
        if error.code == _READ_ERROR_CODES["runtime"]:
            raise
        raise CutoverGuardError(
            _READ_ERROR_CODES["runtime"], "runtime readback is malformed"
        ) from error
    except Exception as error:
        raise CutoverGuardError(
            _READ_ERROR_CODES["runtime"], "runtime readback is malformed"
        ) from error


def _decode_runtime_inner(value: object) -> RuntimePreflightReadback:
    data = _exact_object(
        value,
        {
            "repository",
            "selectors",
            "configuration_digest",
            "provider_action_refs",
            "persistence_write_refs",
            "readback_digest",
        },
        "runtime",
    )
    _tuple_field(data, "selectors")
    _tuple_field(data, "provider_action_refs")
    _tuple_field(data, "persistence_write_refs")
    selectors = []
    for item in data["selectors"]:
        selector = _exact_object(
            item,
            {"selector", "profile_digest", "fallback_profile_digest", "configuration_source"},
            "runtime selector",
        )
        try:
            selectors.append(RuntimeSelectorReadback(**selector))
        except (TypeError, ValueError) as error:
            raise CutoverGuardError(
                "CUTOVER_BUNDLE_INVALID", "runtime selector is malformed"
            ) from error
    data["selectors"] = tuple(selectors)
    try:
        runtime = RuntimePreflightReadback(**data)
        _validate_typed_readback("runtime", runtime)
        return runtime
    except Exception as error:
        raise CutoverGuardError(
            _READ_ERROR_CODES["runtime"], "runtime readback is malformed"
        ) from error


def _decode_package(value: object) -> PackageReadback:
    try:
        return _decode_package_inner(value)
    except CutoverGuardError as error:
        if error.code == _READ_ERROR_CODES["packages"]:
            raise
        raise CutoverGuardError(
            _READ_ERROR_CODES["packages"], "packages readback is malformed"
        ) from error
    except Exception as error:
        raise CutoverGuardError(
            _READ_ERROR_CODES["packages"], "packages readback is malformed"
        ) from error


def _decode_package_inner(value: object) -> PackageReadback:
    data = _exact_object(
        value,
        {"source_packages", "installed_packages", "drift", "readback_digest"},
        "packages",
    )
    _tuple_field(data, "source_packages")
    _tuple_field(data, "installed_packages")
    _tuple_field(data, "drift")
    for key in ("source_packages", "installed_packages"):
        packages = []
        for item in data[key]:
            package = _exact_object(
                item,
                {
                    "package_name",
                    "version",
                    "content_digest",
                    "manifest_content_digest",
                    "install_surface",
                },
                "package identity",
            )
            try:
                packages.append(PackageIdentity(**package))
            except (TypeError, ValueError) as error:
                raise CutoverGuardError(
                    "CUTOVER_BUNDLE_INVALID", "package identity is malformed"
                ) from error
        data[key] = tuple(packages)
    try:
        packages = PackageReadback(**data)
        _validate_typed_readback("packages", packages)
        return packages
    except Exception as error:
        raise CutoverGuardError(
            _READ_ERROR_CODES["packages"], "packages readback is malformed"
        ) from error


def _decode_bundle(value: object) -> CutoverReadbackBundle:
    data = _exact_object(value, {"schema", "subject", "readbacks"}, "bundle")
    if data["schema"] != READBACK_BUNDLE_SCHEMA or type(data["readbacks"]) is not dict:
        raise CutoverGuardError("CUTOVER_BUNDLE_INVALID", "bundle schema is invalid")
    readbacks = _exact_object(
        data["readbacks"],
        {"legacy", "durable_state", "writer_fence", "ownership", "compatibility", "runtime", "packages"},
        "bundle readbacks",
    )
    subject = _decode_subject(data["subject"])
    legacy = _decode_readback(
        readbacks["legacy"],
        {
            "repository",
            "writer_generation",
            "authority_state",
            "active_dispatches",
            "active_workers",
            "integration_lease_owner",
            "v2_execution_refs",
            "v2_execution_state",
            "original_decoder_readable",
            "durable_state_digest",
            "readback_digest",
        },
        ("active_dispatches", "active_workers", "v2_execution_refs"),
        LegacyReadback,
        "legacy",
    )
    durable_state = _decode_readback(
        readbacks["durable_state"],
        {
            "repository",
            "generation_id",
            "state_schema",
            "compatible",
            "active_plan_digests",
            "pending_activation_ids",
            "predecessor_identity_refs",
            "readback_digest",
        },
        ("active_plan_digests", "pending_activation_ids", "predecessor_identity_refs"),
        DurableStateReadback,
        "durable_state",
    )
    writer_fence = _decode_readback(
        readbacks["writer_fence"],
        {
            "repository",
            "writer_generation",
            "authority_state",
            "record_id",
            "activation_id",
            "control_ref_digest",
            "readback_digest",
        },
        (),
        WriterFenceReadback,
        "writer_fence",
    )
    ownership = _decode_readback(
        readbacks["ownership"],
        {
            "repository",
            "active_admissions",
            "active_attempts",
            "integration_lease_owner",
            "runtime_resource_refs",
            "readback_digest",
        },
        ("active_admissions", "active_attempts", "runtime_resource_refs"),
        OwnershipReadback,
        "ownership",
    )
    compatibility = _decode_readback(
        readbacks["compatibility"],
        {
            "repository",
            "source_commit",
            "source_tree_digest",
            "audit_version",
            "reachable_v2_projection_refs",
            "reachable_v3_compatibility_refs",
            "reachable_legacy_writer_refs",
            "proven_unreachable_refs",
            "readback_digest",
        },
        (
            "reachable_v2_projection_refs",
            "reachable_v3_compatibility_refs",
            "reachable_legacy_writer_refs",
            "proven_unreachable_refs",
        ),
        CompatibilityPathReadback,
        "compatibility",
    )
    runtime = _decode_runtime(readbacks["runtime"])
    packages = _decode_package(readbacks["packages"])
    try:
        return CutoverReadbackBundle(
            schema=data["schema"],
            subject=subject,
            legacy=legacy,
            durable_state=durable_state,
            writer_fence=writer_fence,
            ownership=ownership,
            compatibility=compatibility,
            runtime=runtime,
            packages=packages,
        )
    except (TypeError, ValueError) as error:
        raise CutoverGuardError("CUTOVER_BUNDLE_INVALID", "bundle is malformed") from error


@dataclass(frozen=True)
class JsonCutoverReadPorts:
    """One-read, immutable JSON replay of the seven typed read ports."""

    subject: CutoverSubject
    bundle: CutoverReadbackBundle

    @classmethod
    def load(cls, path: Path) -> "JsonCutoverReadPorts":
        try:
            raw = path.read_bytes()
            value = load_canonical_json(raw)
            bundle = _decode_bundle(value)
        except CutoverGuardError:
            raise
        except Exception as error:
            raise CutoverGuardError(
                "CUTOVER_BUNDLE_INVALID", "offline readback bundle is malformed or unavailable"
            ) from error
        return cls(subject=bundle.subject, bundle=bundle)

    def sources(self) -> CutoverGuardSources:
        return CutoverGuardSources(
            legacy=_ReplayReadPort(self.bundle.legacy),
            durable_state=_ReplayReadPort(self.bundle.durable_state),
            writer_fence=_ReplayReadPort(self.bundle.writer_fence),
            ownership=_ReplayReadPort(self.bundle.ownership),
            compatibility=_ReplayReadPort(self.bundle.compatibility),
            runtime=_ReplayReadPort(self.bundle.runtime),
            packages=_ReplayReadPort(self.bundle.packages),
        )


class CutoverGuard:
    def __init__(self, sources: CutoverGuardSources) -> None:
        if type(sources) is not CutoverGuardSources:
            raise TypeError("Guard sources must be one exact immutable source value")
        self._sources = sources

    def evaluate(self, subject: CutoverSubject) -> CutoverGuardReport:
        if type(subject) is not CutoverSubject:
            raise CutoverGuardError(
                "CUTOVER_SUBJECT_INVALID",
                "Guard subject must be one exact CutoverSubject",
            )
        readbacks: dict[str, object] = {}
        read_errors: dict[str, str] = {}
        blockers: list[CutoverBlocker] = []
        checks: list[GuardCheck] = []

        def read(
            name: str,
            operation: Callable[[], object],
        ) -> object | None:
            try:
                value = operation()
                _validate_typed_readback(name, value)
            except Exception as error:
                code = _READ_ERROR_CODES[name]
                detail = f"{type(error).__name__}: readback unavailable"
                read_errors[name] = code
                readbacks[name] = {"error_code": code, "detail": detail}
                blockers.append(CutoverBlocker(code, name, None, detail))
                return None
            readbacks[name] = value
            return value

        legacy = read("legacy", lambda: self._sources.legacy.read(subject.repository))
        durable = read(
            "durable_state",
            lambda: self._sources.durable_state.read(subject.repository),
        )
        writer = read(
            "writer_fence",
            lambda: self._sources.writer_fence.read(subject.repository),
        )
        ownership = read(
            "ownership",
            lambda: self._sources.ownership.read(subject.repository),
        )
        compatibility = read(
            "compatibility",
            lambda: self._sources.compatibility.read(subject),
        )
        runtime = read(
            "runtime",
            lambda: self._sources.runtime.read(
                subject.repository, subject.required_runtime_selectors
            ),
        )
        packages = read(
            "packages",
            lambda: self._sources.packages.read(subject),
        )

        def observed(value: object | None) -> str | None:
            return None if value is None else digest_value(value.canonical())

        def check(
            check_id: str,
            passed: bool,
            code: str,
            detail: str,
            value: object | None,
        ) -> None:
            checks.append(GuardCheck(check_id, passed, observed(value)))
            if not passed and value is not None:
                blockers.append(CutoverBlocker(code, check_id, observed(value), detail))

        source_writer_ok = (
            writer is not None
            and writer.repository == subject.repository
            and writer.writer_generation == subject.source_writer_generation == "v6.1"
            and writer.authority_state == "authoritative"
            and writer.activation_id is None
            and _valid_guard_digest(writer.control_ref_digest)
            and _valid_guard_digest(writer.readback_digest)
        )
        check(
            "source_writer",
            source_writer_ok,
            "CUTOVER_SOURCE_WRITER_INVALID",
            "the V6.1 writer fence is not authoritative and read-back valid",
            writer,
        )

        legacy_state = None if legacy is None else legacy.v2_execution_state
        legacy_code = (
            "CUTOVER_V2_ACTIVE"
            if legacy_state == "running"
            else "CUTOVER_LEGACY_NOT_QUIESCENT"
        )
        legacy_ok = (
            legacy is not None
            and legacy.repository == subject.repository
            and legacy.writer_generation == subject.source_writer_generation
            and legacy.authority_state == "authoritative_quiescent"
            and not legacy.active_dispatches
            and not legacy.active_workers
            and legacy.integration_lease_owner is None
            and legacy_state in {"none", "terminal", "quiescent_read_only"}
            and legacy.original_decoder_readable
            and _valid_guard_digest(legacy.durable_state_digest)
        )
        if legacy is not None and legacy_state not in {
            "none",
            "running",
            "terminal",
            "quiescent_read_only",
        }:
            legacy_code = "CUTOVER_LEGACY_STATE_INVALID"
        check(
            "legacy_quiescence",
            legacy_ok,
            legacy_code,
            "V6.1 has active work, an invalid V2 state, or an unreadable decoder",
            legacy,
        )

        durable_ok = (
            durable is not None
            and durable.repository == subject.repository
            and durable.generation_id == subject.store_generation
            and durable.state_schema == "gwo.v8.store.v1"
            and durable.compatible is True
            and not durable.active_plan_digests
            and not durable.pending_activation_ids
            and not durable.predecessor_identity_refs
            and _valid_guard_digest(durable.readback_digest)
        )
        check(
            "durable_state",
            durable_ok,
            "CUTOVER_DURABLE_STATE_INVALID",
            "the fresh V8 store read-back is incompatible or contains state",
            durable,
        )

        ownership_ok = (
            ownership is not None
            and ownership.repository == subject.repository
            and not ownership.active_admissions
            and not ownership.active_attempts
            and ownership.integration_lease_owner is None
            and not ownership.runtime_resource_refs
            and _valid_guard_digest(ownership.readback_digest)
        )
        check(
            "writer_and_lease",
            ownership_ok,
            "CUTOVER_WRITER_OR_LEASE_UNAVAILABLE",
            "a Worker admission, attempt, lease, or Runtime resource is active",
            ownership,
        )

        path_reachable = (
            compatibility is not None
            and (
                compatibility.reachable_v2_projection_refs
                or compatibility.reachable_v3_compatibility_refs
                or compatibility.reachable_legacy_writer_refs
            )
        )
        path_shape_ok = (
            compatibility is not None
            and compatibility.repository == subject.repository
            and compatibility.source_commit == subject.source_commit
            and compatibility.source_tree_digest == subject.source_tree_digest
            and compatibility.audit_version == "gwo.cutover-path-audit.v1"
            and compatibility.proven_unreachable_refs
            == tuple(sorted(subject.forbidden_production_refs))
            and _valid_guard_digest(compatibility.readback_digest)
        )
        check(
            "production_paths",
            path_shape_ok and not path_reachable,
            "CUTOVER_COMPATIBILITY_PATH_REACHABLE"
            if path_reachable
            else "CUTOVER_COMPATIBILITY_AUDIT_INVALID",
            "a forbidden predecessor path is reachable or its audit is invalid",
            compatibility,
        )

        runtime_ok = (
            runtime is not None
            and runtime.repository == subject.repository
            and tuple(item.selector for item in runtime.selectors)
            == subject.required_runtime_selectors
            and all(
                _valid_guard_digest(item.profile_digest)
                and (
                    item.fallback_profile_digest is None
                    or _valid_guard_digest(item.fallback_profile_digest)
                )
                for item in runtime.selectors
            )
            and _valid_guard_digest(runtime.configuration_digest)
            and not runtime.provider_action_refs
            and not runtime.persistence_write_refs
            and _valid_guard_digest(runtime.readback_digest)
        )
        check(
            "runtime_configuration",
            runtime_ok,
            "CUTOVER_RUNTIME_CONFIGURATION_INVALID",
            "required Runtime selector identities are incomplete or effectful",
            runtime,
        )

        expected_source = tuple(sorted((name, None) for name in subject.package_names))
        expected_installed = tuple(
            sorted(
                (name, surface)
                for surface in subject.install_surfaces
                for name in subject.package_names
            )
        )
        actual_source = (
            tuple(
                sorted(
                    (item.package_name, item.install_surface)
                    for item in packages.source_packages
                )
            )
            if packages is not None
            else ()
        )
        actual_installed = (
            tuple(
                sorted(
                    (item.package_name, item.install_surface)
                    for item in packages.installed_packages
                )
            )
            if packages is not None
            else ()
        )
        source_by_name = (
            {item.package_name: item for item in packages.source_packages}
            if packages is not None
            else {}
        )
        installed_by_key = (
            {
                (item.package_name, item.install_surface): item
                for item in packages.installed_packages
            }
            if packages is not None
            else {}
        )
        package_ok = (
            packages is not None
            and actual_source == expected_source
            and actual_installed == expected_installed
            and not packages.drift
            and all(
                source_by_name[name].version == "8.0.0"
                and _valid_guard_digest(source_by_name[name].content_digest)
                and _valid_guard_digest(source_by_name[name].manifest_content_digest)
                and installed_by_key[(name, surface)].version == "8.0.0"
                and installed_by_key[(name, surface)].content_digest
                == source_by_name[name].content_digest
                and installed_by_key[(name, surface)].manifest_content_digest
                == source_by_name[name].manifest_content_digest
                for name in subject.package_names
                for surface in subject.install_surfaces
            )
            and _valid_guard_digest(packages.readback_digest)
        )
        check(
            "package_installation",
            package_ok,
            "CUTOVER_PACKAGE_INVALID",
            "source or installed Skill package identity has drifted",
            packages,
        )

        ordered_names = (
            "legacy",
            "durable_state",
            "writer_fence",
            "ownership",
            "compatibility",
            "runtime",
            "packages",
        )
        readback_bundle = tuple((name, readbacks[name]) for name in ordered_names)

        def canonical_readback(value: object) -> object:
            if type(value) is dict:
                return value
            canonical = getattr(value, "canonical", None)
            if not callable(canonical):
                raise CutoverGuardError(
                    "CUTOVER_READBACK_INVALID",
                    "readback has no canonical projection",
                )
            return canonical()

        readback_digest = digest_value(
            {
                name: canonical_readback(value)
                for name, value in readback_bundle
            }
        )
        ordered_blockers = tuple(
            sorted(blockers, key=lambda item: (item.check_id, item.code, item.detail))
        )
        receipt = None
        if not ordered_blockers:
            receipt_without_digest = {
                "schema": RECEIPT_SCHEMA,
                "repository": subject.repository,
                "subject_digest": digest_value(subject.canonical()),
                "readback_digest": readback_digest,
                "source_writer_generation": subject.source_writer_generation,
                "target_writer_generation": subject.target_writer_generation,
                "store_generation": subject.store_generation,
                "writer_control_ref_digest": writer.control_ref_digest,
                "runtime_configuration_digest": runtime.configuration_digest,
                "compatibility_audit_digest": compatibility.readback_digest,
                "package_readback_digest": packages.readback_digest,
            }
            receipt = CutoverGuardReceipt(
                **receipt_without_digest,
                receipt_digest=digest_value(receipt_without_digest),
            )
        return CutoverGuardReport(
            schema=GUARD_SCHEMA,
            decision="GO" if receipt is not None else "NO_GO",
            repository=subject.repository,
            subject_digest=digest_value(subject.canonical()),
            readback_digest=readback_digest,
            checks=tuple(checks),
            blockers=ordered_blockers,
            receipt=receipt,
        )

    def validate_activation_token(
        self,
        subject: CutoverSubject,
        receipt: CutoverGuardReceipt,
    ) -> None:
        if type(subject) is not CutoverSubject:
            raise CutoverGuardError(
                "CUTOVER_GUARD_TOKEN_STALE",
                "Guard subject type is invalid",
            )
        if type(receipt) is not CutoverGuardReceipt:
            raise CutoverGuardError("CUTOVER_GUARD_TOKEN_STALE", "Guard receipt type is invalid")
        if (
            receipt.schema != RECEIPT_SCHEMA
            or receipt.repository != subject.repository
            or receipt.subject_digest != digest_value(subject.canonical())
            or receipt.receipt_digest
            != digest_value(receipt.canonical_without_digest())
        ):
            raise CutoverGuardError(
                "CUTOVER_GUARD_TOKEN_STALE",
                "Guard receipt no longer matches authoritative readback",
            )
        fresh = self.evaluate(subject)
        if fresh.decision != "GO" or fresh.receipt != receipt:
            raise CutoverGuardError(
                "CUTOVER_GUARD_TOKEN_STALE",
                "Guard receipt no longer matches authoritative readback",
            )
