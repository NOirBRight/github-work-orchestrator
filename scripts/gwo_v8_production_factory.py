"""Explicit, live Phase 5 composition for the V8 activation boundary.

The factory never discovers a Store, constructs an in-memory control, or
pretends that a Guard receipt is current.  The operator supplies immutable
Store/rollback identities and the package roots used by the existing
resolver-backed live Guard host.  If that host is not installed, composition
fails before a ProductionActivationComposition is returned.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import re
import sqlite3
import stat

from gwo_v8.activation import (
    GitHubCliContentClient,
    GitHubDurablePlanControl,
    LocalPlanPublication,
)
from gwo_v8._canonical import canonical_bytes, digest_value
from gwo_v8.compiler import CompiledPlan
from gwo_v8.cutover_guard import (
    CutoverGuardReceipt,
    CutoverGuardSources,
    CutoverSubject,
    EXPECTED_SOURCE_WRITER_GENERATION,
    RECEIPT_SCHEMA,
    GuardActivationValidator,
    LegacyReadback,
)
from gwo_v8.plan_control_host import (
    CutoverGuardRequest,
    ProductionCutoverGuardHost,
    load_production_cutover_guard,
)
from gwo_v8.production_activation import (
    ProductionActivationAuthorization,
    ProductionActivationComposition,
    WRITER_TRANSITION,
)
from gwo_v8.production_effects import ProductionCompositionError
from gwo_v8_live_guard_host import LiveGuardHostError, install_live_guard_host
from gwo_v8.transition import (
    CanaryAcceptance,
    GitHubCanaryEvidenceControl,
    GitHubLegacyWriterControl,
    GitHubWriterTransitionControl,
    LegacyWriterReadback,
    WriterCutoverController,
)


_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_STORE_TABLE_COLUMNS = {
    "v8_plan_revisions": {
        "repository",
        "plan_digest",
        "canonical_bytes",
        "compilation_record",
        "writer_generation",
    },
    "v8_active_plans": {
        "repository",
        "plan_digest",
        "writer_generation",
        "activation_id",
    },
    "v8_pending_activations": {
        "repository",
        "plan_digest",
        "expected_previous_digest",
        "writer_generation",
        "activation_id",
        "receipt_json",
    },
    "v8_writer_generations": {"repository", "writer_generation"},
    "v8_writer_fences": {
        "repository",
        "writer_generation",
        "activation_id",
        "state",
    },
}
_STORE_SIDECAR_SUFFIXES = ("-wal", "-shm", "-journal")
_MISSING_INSTALLED_HOST_CODE = "CUTOVER_GUARD_COMPOSITION_INVALID"
_MISSING_INSTALLED_HOST_DETAIL = "installed host is unavailable"


def _error(code: str, detail: str) -> ProductionCompositionError:
    return ProductionCompositionError(code, detail)


def _text(value: object) -> bool:
    return type(value) is str and bool(value.strip())


def _digest(value: object) -> bool:
    return type(value) is str and _HEX64.fullmatch(value) is not None


def _is_missing_installed_host(error: BaseException) -> bool:
    return (
        getattr(error, "code", None) == _MISSING_INSTALLED_HOST_CODE
        and getattr(error, "detail", None) == _MISSING_INSTALLED_HOST_DETAIL
    )


@dataclass(frozen=True, init=False)
class RollbackLineage:
    """Immutable predecessor Store identity supplied by the operator.

    ``store_paths``/``source_writer_generation`` are retained as the narrow
    compatibility spelling used by the earlier Phase 5 draft.  The canonical
    constructor spelling is ``store_path``/``writer_generation``.
    """

    store_paths: tuple[Path, ...]
    source_writer_generation: str
    store_sha256: str | None
    activation_id: str | None

    def __init__(
        self,
        store_path: Path | None = None,
        store_sha256: str | None = None,
        writer_generation: str | None = None,
        activation_id: str | None = None,
        *,
        store_paths: tuple[Path, ...] | None = None,
        source_writer_generation: str | None = None,
    ) -> None:
        if store_paths is None:
            if not isinstance(store_path, Path):
                raise TypeError("rollback store_path must be a Path")
            resolved_paths = (store_path,)
        else:
            if type(store_paths) is not tuple or not store_paths:
                raise TypeError("rollback store_paths must be a non-empty tuple")
            if any(not isinstance(path, Path) for path in store_paths):
                raise TypeError("rollback store_paths must contain only Paths")
            if store_path is not None and store_path != store_paths[0]:
                raise ValueError("store_path and store_paths must identify the same first Store")
            resolved_paths = store_paths
        if writer_generation is None:
            writer_generation = source_writer_generation
        elif (
            source_writer_generation is not None
            and writer_generation != source_writer_generation
        ):
            raise ValueError("writer_generation and source_writer_generation must match")
        if not _text(writer_generation):
            raise ValueError("rollback writer generation is required")
        if store_sha256 is not None and _HEX64.fullmatch(store_sha256) is None:
            raise ValueError("rollback store_sha256 must be a lowercase SHA-256 digest")
        if activation_id is not None and not _text(activation_id):
            raise ValueError("rollback activation_id must be non-empty text")
        object.__setattr__(self, "store_paths", resolved_paths)
        object.__setattr__(self, "source_writer_generation", writer_generation)
        object.__setattr__(self, "store_sha256", store_sha256)
        object.__setattr__(self, "activation_id", activation_id)

    @property
    def store_path(self) -> Path:
        return self.store_paths[0]

    @property
    def writer_generation(self) -> str:
        return self.source_writer_generation


@dataclass(frozen=True)
class ProductionCompositionConfig:
    """Explicit immutable inputs for one live composition.

    The Store and rollback lineage are mandatory.  Guard package roots are
    also mandatory for live composition; without them the existing host
    resolver cannot prove the authoritative read-only Guard sources.
    """

    store_path: Path
    rollback_lineage: RollbackLineage
    target_repository: str | None = None
    control_branch: str = "gwo-control"
    store_generation: str | None = None
    source_writer_generation: str | None = None
    target_writer_generation: str | None = None
    control_root: str = ".gwo/v8"
    github_executable: str = "gh"
    github_timeout_seconds: int = 30
    canary_locations: tuple[tuple[str, str, str, str], ...] = ()
    guard_package_root: Path | None = None
    guard_install_roots: tuple[Path, Path, Path] | None = None
    repository_root: Path | None = None
    runtime_config_path: Path | None = None
    gateway_store_path: Path | None = None
    artifact_root: Path | None = None
    target_branch: str = "main"

    def __post_init__(self) -> None:
        if not isinstance(self.store_path, Path):
            raise TypeError("store_path must be a Path")
        if type(self.rollback_lineage) is not RollbackLineage:
            raise TypeError("rollback_lineage must be RollbackLineage")
        for name in (
            "control_branch",
            "control_root",
            "github_executable",
            "target_branch",
        ):
            if not _text(getattr(self, name)):
                raise ValueError(f"{name} is required")
        for name in (
            "target_repository",
            "store_generation",
            "source_writer_generation",
            "target_writer_generation",
        ):
            value = getattr(self, name)
            if value is not None and not _text(value):
                raise ValueError(f"{name} must be non-empty text when configured")
        if (
            type(self.github_timeout_seconds) is not int
            or isinstance(self.github_timeout_seconds, bool)
            or self.github_timeout_seconds < 1
        ):
            raise ValueError("github_timeout_seconds must be a positive integer")
        if type(self.canary_locations) is not tuple:
            raise TypeError("canary_locations must be an exact tuple")
        for item in self.canary_locations:
            if type(item) is not tuple or len(item) != 4 or any(
                not _text(value) for value in item
            ):
                raise TypeError(
                    "canary_locations must contain (ref, repository, branch, path) tuples"
                )
        if self.guard_package_root is not None and not isinstance(
            self.guard_package_root, Path
        ):
            raise TypeError("guard_package_root must be a Path")
        if self.guard_install_roots is not None:
            if type(self.guard_install_roots) is not tuple or len(self.guard_install_roots) != 3:
                raise TypeError("guard_install_roots must be an exact three-Path tuple")
            if any(not isinstance(path, Path) for path in self.guard_install_roots):
                raise TypeError("guard_install_roots must contain only Paths")
        if (self.guard_package_root is None) != (self.guard_install_roots is None):
            raise ValueError(
                "guard_package_root and guard_install_roots must be configured together"
            )
        live_host_values = (
            self.repository_root,
            self.runtime_config_path,
            self.gateway_store_path,
            self.artifact_root,
        )
        if any(value is not None for value in live_host_values):
            if any(not isinstance(value, Path) for value in live_host_values):
                raise TypeError(
                    "live host paths must be configured as exact Path values"
                )
            if any(value is None for value in live_host_values):
                raise ValueError(
                    "repository_root, runtime_config_path, gateway_store_path, and artifact_root must be configured together"
                )


# Public spellings used by the CLI host and the Phase 5 draft are identical
# immutable values, not alternate implementations.
ProductionFactoryConfig = ProductionCompositionConfig
ProductionRollbackLineage = RollbackLineage


def _absolute_path(value: object, label: str, code: str) -> Path:
    if not isinstance(value, Path) or not value.is_absolute():
        raise _error(code, f"{label} must be an absolute Path")
    return value


def _is_reparse_or_link(path: Path) -> bool:
    try:
        information = path.lstat()
    except OSError:
        return False
    if stat.S_ISLNK(information.st_mode):
        return True
    attributes = getattr(information, "st_file_attributes", 0)
    return bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))


def _validate_regular_path(path: Path, label: str, code: str) -> None:
    if _is_reparse_or_link(path):
        raise _error(code, f"{label} must not be a link or reparse point: {path}")
    try:
        information = path.stat()
    except OSError as error:
        raise _error(code, f"{label} is unavailable: {path}") from error
    if not stat.S_ISREG(information.st_mode):
        raise _error(code, f"{label} must be a regular file: {path}")


def _validate_directory_path(path: Path, label: str) -> None:
    if not path.is_absolute() or _is_reparse_or_link(path):
        raise _error(
            "FACTORY_GUARD_CONFIGURATION_INVALID",
            f"{label} must be an absolute non-reparse directory: {path}",
        )
    try:
        information = path.stat()
    except OSError as error:
        raise _error(
            "FACTORY_GUARD_CONFIGURATION_INVALID",
            f"{label} is unavailable: {path}",
        ) from error
    if not stat.S_ISDIR(information.st_mode):
        raise _error(
            "FACTORY_GUARD_CONFIGURATION_INVALID",
            f"{label} must be a directory: {path}",
        )


def _path_key(path: Path) -> str:
    try:
        return os.path.normcase(str(path.resolve(strict=True)))
    except (OSError, RuntimeError, ValueError) as error:
        raise _error(
            "FACTORY_STORE_UNSAFE",
            f"configured Store path cannot be resolved: {path}",
        ) from error


def _validate_sidecars(path: Path, code: str) -> None:
    for suffix in _STORE_SIDECAR_SUFFIXES:
        sidecar = Path(f"{path}{suffix}")
        if os.path.lexists(sidecar):
            raise _error(code, f"Store has a live SQLite sidecar: {sidecar}")


def _read_stable_bytes(path: Path, code: str) -> bytes:
    try:
        before = path.stat()
        content = path.read_bytes()
        after = path.stat()
    except OSError as error:
        raise _error(code, f"Store identity could not be read: {path}") from error
    if (
        before.st_size != after.st_size
        or before.st_mtime_ns != after.st_mtime_ns
        or getattr(before, "st_ino", None) != getattr(after, "st_ino", None)
    ):
        raise _error(code, f"Store changed while its identity was read: {path}")
    return content


def _open_immutable_store(path: Path) -> sqlite3.Connection:
    try:
        uri = f"{path.as_uri()}?mode=ro&immutable=1"
        connection = sqlite3.connect(uri, uri=True)
        connection.row_factory = sqlite3.Row
        return connection
    except (OSError, ValueError, sqlite3.Error) as error:
        raise _error(
            "FACTORY_STORE_UNSAFE",
            f"Store is not a readable immutable SQLite database: {path}",
        ) from error


def _validate_store(
    path: Path,
    *,
    repository: str,
    expected_store_generation: str,
) -> None:
    _absolute_path(path, "store_path", "FACTORY_STORE_UNSAFE")
    _validate_regular_path(path, "store_path", "FACTORY_STORE_UNSAFE")
    _validate_sidecars(path, "FACTORY_STORE_UNSAFE")
    connection = _open_immutable_store(path)
    try:
        tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        if not set(_STORE_TABLE_COLUMNS).issubset(tables):
            raise _error(
                "FACTORY_STORE_UNSAFE",
                "Store is missing the pre-provisioned V8 activation schema",
            )
        for table, expected_columns in _STORE_TABLE_COLUMNS.items():
            columns = {
                str(row[1])
                for row in connection.execute(f"PRAGMA table_info({table})")
            }
            if not expected_columns.issubset(columns):
                raise _error(
                    "FACTORY_STORE_UNSAFE",
                    f"Store table {table} has an incompatible schema",
                )
        writer_row = connection.execute(
            """
            SELECT writer_generation
            FROM v8_writer_generations
            WHERE repository = ?
            """,
            (repository,),
        ).fetchone()
        if writer_row is not None and writer_row[0] != expected_store_generation:
            raise _error(
                "FACTORY_STORE_WRITER_IDENTITY_INVALID",
                "configured Store already belongs to a different Store generation",
            )
        integrity = connection.execute("PRAGMA integrity_check").fetchone()
        if integrity is None or integrity[0] != "ok":
            raise _error(
                "FACTORY_STORE_UNSAFE",
                "Store integrity check did not return ok",
            )
    except ProductionCompositionError:
        raise
    except (sqlite3.Error, TypeError, ValueError) as error:
        raise _error(
            "FACTORY_STORE_UNSAFE",
            f"Store schema readback failed: {path}",
        ) from error
    finally:
        connection.close()


def _validate_rollback_lineage(
    lineage: RollbackLineage,
    store_path: Path,
    expected_source_writer: str | None,
) -> None:
    if type(lineage) is not RollbackLineage or not lineage.store_paths:
        raise _error(
            "FACTORY_CONFIGURATION_INVALID",
            "rollback_lineage must be one exact immutable value with a Store",
        )
    store_key = _path_key(store_path)
    seen: set[str] = set()
    for index, path in enumerate(lineage.store_paths):
        _absolute_path(
            path,
            f"rollback_lineage.store_paths[{index}]",
            "FACTORY_ROLLBACK_LINEAGE_INVALID",
        )
        key = _path_key(path)
        if key == store_key or key in seen:
            raise _error(
                "FACTORY_STORE_IDENTITY_DISJOINT",
                "activation Store and rollback lineage Store identities must be disjoint",
            )
        seen.add(key)
        _validate_regular_path(
            path,
            f"rollback_lineage.store_paths[{index}]",
            "FACTORY_ROLLBACK_LINEAGE_INVALID",
        )
        _validate_sidecars(path, "FACTORY_ROLLBACK_LINEAGE_INVALID")
    if lineage.source_writer_generation != "v6.1":
        raise _error(
            "FACTORY_ROLLBACK_LINEAGE_INVALID",
            "rollback lineage must name the V6.1 predecessor writer",
        )
    if (
        expected_source_writer is not None
        and lineage.source_writer_generation != expected_source_writer
    ):
        raise _error(
            "FACTORY_ROLLBACK_LINEAGE_INVALID",
            "rollback lineage writer generation does not match the configured source writer",
        )
    if lineage.store_sha256 is not None:
        observed = hashlib.sha256(
            _read_stable_bytes(lineage.store_path, "FACTORY_ROLLBACK_LINEAGE_INVALID")
        ).hexdigest()
        if observed != lineage.store_sha256:
            raise _error(
                "FACTORY_ROLLBACK_LINEAGE_INVALID",
                "rollback lineage Store hash does not match its immutable identity",
            )


def _validate_identity(
    config: ProductionCompositionConfig,
    authorization: ProductionActivationAuthorization,
    compiled_plan: object,
    canary: CanaryAcceptance,
    subject: CutoverSubject,
    receipt: CutoverGuardReceipt,
) -> None:
    if type(authorization) is not ProductionActivationAuthorization:
        raise _error("FACTORY_INPUT_INVALID", "authorization has the wrong exact type")
    if type(compiled_plan) is not CompiledPlan:
        raise _error("FACTORY_INPUT_INVALID", "compiled_plan has the wrong exact type")
    if type(canary) is not CanaryAcceptance or type(subject) is not CutoverSubject:
        raise _error("FACTORY_INPUT_INVALID", "Canary and Guard subject types are invalid")
    if type(receipt) is not CutoverGuardReceipt:
        raise _error("FACTORY_INPUT_INVALID", "guard_receipt has the wrong exact type")

    target = authorization.target_repository
    if config.target_repository is not None and config.target_repository != target:
        raise _error("FACTORY_IDENTITY_DISJOINT", "configured target repository is not authorized")
    expected_source = (
        config.source_writer_generation
        if config.source_writer_generation is not None
        else subject.source_writer_generation
    )
    expected_target = (
        config.target_writer_generation
        if config.target_writer_generation is not None
        else authorization.target_writer_generation
    )
    expected_store = (
        config.store_generation
        if config.store_generation is not None
        else subject.store_generation
    )
    if expected_source != EXPECTED_SOURCE_WRITER_GENERATION:
        raise _error(
            "FACTORY_IDENTITY_DISJOINT",
            "production composition requires the V6.1 source writer generation",
        )
    if (
        compiled_plan.repository != target
        or canary.repository != target
        or subject.repository != target
        or receipt.repository != target
        or subject.control_branch != config.control_branch
        or subject.target_branch != config.target_branch
        or subject.source_writer_generation != expected_source
        or receipt.source_writer_generation != expected_source
        or authorization.target_writer_generation != expected_target
        or subject.target_writer_generation != expected_target
        or receipt.target_writer_generation != expected_target
        or subject.store_generation != expected_store
        or receipt.store_generation != expected_store
        or subject.source_commit != authorization.merged_main_sha
        or authorization.writer_transition != WRITER_TRANSITION
    ):
        raise _error(
            "FACTORY_IDENTITY_DISJOINT",
            "authorization, Plan, Canary, Guard subject, receipt, and configured target are disjoint",
        )

    try:
        plan_valid = compiled_plan.has_valid_digest()
        canonical_bytes(compiled_plan.compilation_record)
    except Exception as error:
        raise _error(
            "FACTORY_PLAN_INVALID",
            "CompiledPlan identity could not be validated",
        ) from error
    if not plan_valid:
        raise _error(
            "FACTORY_PLAN_INVALID",
            "CompiledPlan bytes do not match its digest",
        )

    if (
        canary.accepted is not True
        or canary.blockers
        or not _digest(canary.evidence_package_digest)
        or not _text(canary.manifest_ref)
        or type(canary.evidence_refs) is not tuple
        or not canary.evidence_refs
        or any(not _text(value) for value in canary.evidence_refs)
        or len(set(canary.evidence_refs)) != len(canary.evidence_refs)
    ):
        raise _error(
            "FACTORY_CANARY_INVALID",
            "CanaryAcceptance is not an accepted, blocker-free identity",
        )

    receipt_digest_fields = (
        "subject_digest",
        "readback_digest",
        "writer_control_ref_digest",
        "runtime_configuration_digest",
        "compatibility_audit_digest",
        "package_readback_digest",
        "receipt_digest",
    )
    if any(
        not _digest(getattr(receipt, name, ""))
        for name in receipt_digest_fields
    ):
        raise _error(
            "FACTORY_GUARD_RECEIPT_INVALID",
            "Guard receipt contains a non-canonical digest",
        )
    try:
        subject_digest = digest_value(subject.canonical())
        receipt_digest = digest_value(receipt.canonical_without_digest())
    except Exception as error:
        raise _error(
            "FACTORY_GUARD_RECEIPT_INVALID",
            "Guard subject or receipt is outside the canonical identity domain",
        ) from error
    if (
        receipt.schema != RECEIPT_SCHEMA
        or receipt.subject_digest != subject_digest
        or receipt.receipt_digest != receipt_digest
    ):
        raise _error(
            "FACTORY_GUARD_RECEIPT_INVALID",
            "Guard receipt is not bound to the exact canonical subject",
        )


def _compose_live_guard(
    config: ProductionCompositionConfig,
    subject: CutoverSubject,
    run_id: str,
) -> tuple[ProductionCutoverGuardHost, object]:
    if config.guard_package_root is None or config.guard_install_roots is None:
        raise _error(
            "FACTORY_GUARD_CONFIGURATION_REQUIRED",
            "live Guard requires explicit package_root and three install_roots",
        )
    _validate_directory_path(config.guard_package_root, "guard_package_root")
    for index, root in enumerate(config.guard_install_roots):
        _validate_directory_path(root, f"guard_install_roots[{index}]")
    request = CutoverGuardRequest(
        subject=subject,
        package_root=config.guard_package_root,
        install_roots=config.guard_install_roots,
    )
    try:
        guard = load_production_cutover_guard(request)
    except Exception as error:
        if not _is_missing_installed_host(error):
            source_code = getattr(error, "code", "GUARD_RESOLVER_UNAVAILABLE")
            raise _error(
                "FACTORY_GUARD_LIVE_UNAVAILABLE",
                f"{source_code}: {error}",
            ) from error
        if any(
            value is None
            for value in (
                config.repository_root,
                config.runtime_config_path,
                config.gateway_store_path,
                config.artifact_root,
            )
        ):
            raise _error(
                "FACTORY_GUARD_CONFIGURATION_REQUIRED",
                "live host bootstrap requires repository, runtime, gateway, and artifact paths",
            ) from error
        try:
            install_live_guard_host(
                subject=subject,
                run_id=run_id,
                repository_root=config.repository_root,
                runtime_config_path=config.runtime_config_path,
                gateway_store_path=config.gateway_store_path,
                artifact_root=config.artifact_root,
                store_path=config.store_path,
                package_root=config.guard_package_root,
                install_roots=config.guard_install_roots,
                github_executable=config.github_executable,
                github_timeout_seconds=config.github_timeout_seconds,
            )
        except LiveGuardHostError as host_error:
            raise _error(
                "FACTORY_GUARD_LIVE_UNAVAILABLE",
                f"{host_error.code}: {host_error.detail}",
            ) from host_error
        try:
            guard = load_production_cutover_guard(request)
        except Exception as retry_error:
            source_code = getattr(
                retry_error,
                "code",
                "GUARD_RESOLVER_UNAVAILABLE",
            )
            raise _error(
                "FACTORY_GUARD_LIVE_UNAVAILABLE",
                f"{source_code}: {retry_error}",
            ) from retry_error
    if type(guard) is not ProductionCutoverGuardHost:
        raise _error(
            "FACTORY_GUARD_LIVE_UNAVAILABLE",
            "the live Guard loader did not return one exact ProductionCutoverGuardHost",
        )
    sources = getattr(guard, "_sources", None)
    if type(sources) is not CutoverGuardSources:
        raise _error(
            "FACTORY_GUARD_LIVE_UNAVAILABLE",
            "the live Guard host has no exact resolver-backed read sources",
        )
    if any(not callable(getattr(source, "read", None)) for source in vars(sources).values()):
        raise _error(
            "FACTORY_GUARD_LIVE_UNAVAILABLE",
            "the live Guard sources do not expose read-only operations",
        )
    return guard, sources.legacy


class _LegacyExecutionReadback:
    """Adapt the authoritative Guard legacy read to the transition contract."""

    def __init__(self, source: object):
        self._source = source

    def __call__(self, repository: str) -> LegacyWriterReadback:
        try:
            value = self._source.read(repository)
        except Exception as error:
            raise _error(
                "FACTORY_LEGACY_READBACK_UNAVAILABLE",
                "authoritative Guard legacy readback failed",
            ) from error
        if type(value) is not LegacyReadback or value.repository != repository:
            raise _error(
                "FACTORY_LEGACY_READBACK_UNAVAILABLE",
                "authoritative Guard legacy readback has the wrong identity",
            )
        return LegacyWriterReadback(
            repository=repository,
            stopped=value.authority_state == "stopped",
            active_dispatches=value.active_dispatches,
            integration_lease=value.integration_lease_owner is not None,
            active_workers=value.active_workers,
        )


def _unopened_publication(
    store_path: Path,
    durable: GitHubDurablePlanControl,
    transitions: GitHubWriterTransitionControl,
) -> LocalPlanPublication:
    """Bind the real publication without its schema-provisioning constructor."""

    publication = object.__new__(LocalPlanPublication)
    publication.store_path = store_path
    publication.durable = durable
    publication.writer_authority = transitions
    publication._checkpoint = lambda _name: None
    publication._durable_snapshots = {}
    publication._durable_snapshot_depths = {}
    return publication


class ProductionActivationCompositionFactory:
    """Compose only live controls around one explicitly authorized identity."""

    def __init__(self, config: ProductionCompositionConfig | None = None):
        if config is not None and type(config) is not ProductionCompositionConfig:
            raise TypeError("config must be ProductionCompositionConfig")
        self.config = config

    def compose(
        self,
        *,
        authorization: ProductionActivationAuthorization,
        compiled_plan: CompiledPlan,
        canary: CanaryAcceptance,
        guard_subject: CutoverSubject,
        guard_receipt: CutoverGuardReceipt,
    ) -> ProductionActivationComposition:
        config = self.config
        if config is None:
            raise _error(
                "FACTORY_CONFIGURATION_REQUIRED",
                "production composition requires explicit immutable configuration",
            )
        _validate_identity(
            config,
            authorization,
            compiled_plan,
            canary,
            guard_subject,
            guard_receipt,
        )
        _validate_store(
            config.store_path,
            repository=authorization.target_repository,
            expected_store_generation=(
                config.store_generation
                if config.store_generation is not None
                else guard_subject.store_generation
            ),
        )
        _validate_rollback_lineage(
            config.rollback_lineage,
            config.store_path,
            config.source_writer_generation,
        )
        guard, legacy_source = _compose_live_guard(
            config,
            guard_subject,
            authorization.run_id,
        )

        client = GitHubCliContentClient(
            config.github_executable,
            command_timeout_seconds=config.github_timeout_seconds,
        )
        durable = GitHubDurablePlanControl(
            client,
            branch=config.control_branch,
            root=config.control_root,
        )
        transitions = GitHubWriterTransitionControl(
            client,
            branch=config.control_branch,
            initial_writer=guard_subject.source_writer_generation,
        )
        legacy = GitHubLegacyWriterControl(
            client,
            branch=config.control_branch,
            execution_readback=_LegacyExecutionReadback(legacy_source),
        )
        publication = _unopened_publication(
            config.store_path,
            durable,
            transitions,
        )
        controller = WriterCutoverController(
            legacy=legacy,
            transitions=transitions,
            publication=publication,
            guard=guard,
        )
        locations = {
            source_ref: (repository, branch, path)
            for source_ref, repository, branch, path in config.canary_locations
        }
        canary_control = GitHubCanaryEvidenceControl(
            client,
            locations,
            manifest_repository=authorization.target_repository,
            manifest_branch=config.control_branch,
        )
        composition = ProductionActivationComposition(
            controller=controller,
            canary_evidence_control=canary_control,
        )
        if type(composition) is not ProductionActivationComposition:
            raise _error(
                "FACTORY_COMPOSITION_INVALID",
                "factory did not return one exact ProductionActivationComposition",
            )
        return composition


# Importing the module is safe; no path is guessed and no production adapter is
# selected until an explicitly configured factory is used.
factory = ProductionActivationCompositionFactory()


__all__ = [
    "GuardActivationValidator",
    "ProductionActivationCompositionFactory",
    "ProductionCompositionConfig",
    "ProductionCompositionError",
    "ProductionFactoryConfig",
    "ProductionRollbackLineage",
    "RollbackLineage",
    "factory",
]
