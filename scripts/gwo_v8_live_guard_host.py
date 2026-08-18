"""Explicit production bootstrap for the resolver-backed live Guard host.

The activation CLI runs in a fresh process.  The normal V8 application startup
installs ``ProductionPlanControlStartHost`` before the Guard loader is called,
but the activation boundary has no such long-lived process.  This module is
the narrow bridge between those two contracts: it composes the same
read-only, authoritative Beta3 sources and installs the real GitHub-backed
start host in the current process.

No readback bundle is accepted here.  The four Guard ports are backed by a
fresh, read-only Beta3 attestation cycle.  The only public method on each
adapter is ``read``; the writer, Store, and runtime controls are never exposed
through this composition.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import secrets
import stat
import sys
from threading import RLock, Timer, current_thread
from typing import Callable


_ATTESTOR_MODULES = (
    "beta3_bootstrap_model.py",
    "beta3_control_ownership_attestor.py",
    "beta3_legacy_attestor.py",
    "beta3_replay_guard.py",
)


class LiveGuardHostError(RuntimeError):
    """Typed, fail-closed error raised before an unsafe host is returned."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


def _fail(code: str, detail: str) -> None:
    raise LiveGuardHostError(code, detail)


def _absolute(path: object, label: str) -> Path:
    if not isinstance(path, Path) or not path.is_absolute():
        _fail("LIVE_GUARD_CONFIGURATION_INVALID", f"{label} must be absolute")
    for candidate in (path, *path.parents):
        if _is_reparse_or_link(candidate):
            _fail(
                "LIVE_GUARD_CONFIGURATION_INVALID",
                f"{label} has a reparse or link ancestor",
            )
    try:
        return path.resolve(strict=False)
    except (OSError, RuntimeError, ValueError) as error:
        raise LiveGuardHostError(
            "LIVE_GUARD_CONFIGURATION_INVALID",
            f"{label} cannot be resolved",
        ) from error


def _is_reparse_or_link(path: Path) -> bool:
    try:
        information = path.lstat()
    except OSError:
        return False
    if stat.S_ISLNK(information.st_mode):
        return True
    return bool(
        getattr(information, "st_file_attributes", 0)
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    )


def _require_directory(path: Path, label: str) -> None:
    if _is_reparse_or_link(path) or not path.is_dir():
        _fail(
            "LIVE_GUARD_CONFIGURATION_INVALID",
            f"{label} must be an existing non-reparse directory",
        )


def _require_file(path: Path, label: str) -> None:
    if _is_reparse_or_link(path) or not path.is_file():
        _fail(
            "LIVE_GUARD_CONFIGURATION_INVALID",
            f"{label} must be an existing non-reparse regular file",
        )


def _require_parent(path: Path, label: str) -> None:
    _require_directory(path.parent, f"{label} parent")
    if path.exists() and (_is_reparse_or_link(path) or not path.is_file()):
        _fail(
            "LIVE_GUARD_CONFIGURATION_INVALID",
            f"{label} must be a regular file when it already exists",
        )


def _stable_bytes(path: Path, label: str) -> bytes:
    try:
        before = path.stat()
        payload = path.read_bytes()
        after = path.stat()
    except OSError as error:
        raise LiveGuardHostError(
            "LIVE_GUARD_CONFIGURATION_INVALID",
            f"{label} could not be read",
        ) from error
    if (
        before.st_size != after.st_size
        or before.st_mtime_ns != after.st_mtime_ns
        or getattr(before, "st_ino", None) != getattr(after, "st_ino", None)
    ):
        _fail("LIVE_GUARD_CONFIGURATION_INVALID", f"{label} changed during read")
    return payload


def _validate_explicit_inputs(
    *,
    subject: object,
    repository_root: Path,
    runtime_config_path: Path | None,
    gateway_store_path: Path,
    artifact_root: Path,
    store_path: Path,
    package_root: Path,
    install_roots: tuple[Path, Path, Path],
) -> tuple[Path, Path, Path, Path, Path, tuple[Path, Path, Path]]:
    from gwo_v8.cutover_guard import CutoverSubject

    if type(subject) is not CutoverSubject:
        _fail("LIVE_GUARD_CONFIGURATION_INVALID", "subject must be one exact CutoverSubject")
    if runtime_config_path is None:
        _fail(
            "LIVE_GUARD_CONFIGURATION_REQUIRED",
            "runtime_config_path is required for a live host",
        )
    repository_root = _absolute(repository_root, "repository_root")
    runtime_config_path = _absolute(runtime_config_path, "runtime_config_path")
    gateway_store_path = _absolute(gateway_store_path, "gateway_store_path")
    artifact_root = _absolute(artifact_root, "artifact_root")
    store_path = _absolute(store_path, "store_path")
    package_root = _absolute(package_root, "package_root")
    if type(install_roots) is not tuple or len(install_roots) != 3:
        _fail(
            "LIVE_GUARD_CONFIGURATION_INVALID",
            "install_roots must be one exact three-Path tuple",
        )
    install_roots = tuple(
        _absolute(path, f"install_roots[{index}]")
        for index, path in enumerate(install_roots)
    )
    _require_directory(repository_root, "repository_root")
    _require_file(runtime_config_path, "runtime_config_path")
    _require_file(store_path, "store_path")
    _require_directory(package_root, "package_root")
    for index, path in enumerate(install_roots):
        _require_directory(path, f"install_roots[{index}]")
    _require_parent(gateway_store_path, "gateway_store_path")
    if artifact_root.exists():
        _require_directory(artifact_root, "artifact_root")
    else:
        _require_directory(artifact_root.parent, "artifact_root parent")
    return (
        repository_root,
        runtime_config_path,
        gateway_store_path,
        artifact_root,
        store_path,
        install_roots,
    )


def _validate_production_paths(
    *,
    repository_root: Path,
    runtime_config_path: Path,
    gateway_store_path: Path,
    artifact_root: Path,
    package_root: Path,
    install_roots: tuple[Path, Path, Path],
    expected_repository_root: Path,
    expected_runtime_config_path: Path,
    expected_gateway_store_path: Path,
    expected_artifact_root: Path,
    expected_install_roots: tuple[Path, Path, Path],
) -> None:
    """Bind every host input to the exact release-subject production paths."""

    expected_repository_root = _absolute(
        expected_repository_root, "expected_repository_root"
    )
    expected_runtime_config_path = _absolute(
        expected_runtime_config_path, "expected_runtime_config_path"
    )
    expected_gateway_store_path = _absolute(
        expected_gateway_store_path, "expected_gateway_store_path"
    )
    expected_artifact_root = _absolute(
        expected_artifact_root, "expected_artifact_root"
    )
    if package_root != repository_root or package_root != expected_repository_root:
        _fail(
            "LIVE_GUARD_PROVENANCE_MISMATCH",
            "package_root must be the exact reviewed repository root",
        )
    expected = (
        (repository_root, expected_repository_root, "repository_root"),
        (runtime_config_path, expected_runtime_config_path, "runtime_config_path"),
        (gateway_store_path, expected_gateway_store_path, "gateway_store_path"),
        (artifact_root, expected_artifact_root, "artifact_root"),
    )
    for actual, reviewed, label in expected:
        if actual != reviewed:
            _fail(
                "LIVE_GUARD_PROVENANCE_MISMATCH",
                f"{label} is not the exact reviewed production path",
            )
    if type(expected_install_roots) is not tuple or len(expected_install_roots) != 3:
        _fail(
            "LIVE_GUARD_PROVENANCE_MISMATCH",
            "reviewed install roots are not the exact three-surface tuple",
        )
    normalized_expected_roots = tuple(
        _absolute(path, f"expected_install_roots[{index}]")
        for index, path in enumerate(expected_install_roots)
    )
    if install_roots != normalized_expected_roots:
        _fail(
            "LIVE_GUARD_PROVENANCE_MISMATCH",
            "install_roots are not the exact reviewed production surfaces",
        )


class _LiveAttestationSnapshot:
    __slots__ = ("_bundle", "lease")

    def __init__(self, bundle: object, lease: object) -> None:
        self._bundle = bundle
        self.lease = lease

    def __getattr__(self, name: str) -> object:
        return getattr(self._bundle, name)


class _LiveAttestationState:
    __slots__ = ("snapshot", "lease", "next_field", "timer", "closed")

    def __init__(self, snapshot: object, lease: object) -> None:
        self.snapshot = snapshot
        self.lease = lease
        self.next_field = 0
        self.timer: Timer | None = None
        self.closed = False

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        if self.timer is not None:
            self.timer.cancel()
        self.lease.close()


class _LiveAttestationCycle:
    """Bind one fresh, bounded attestation to one four-read evaluation."""

    _FIELDS = ("legacy", "durable_state", "writer_fence", "ownership")
    _MAX_HOLD_SECONDS = 30.0

    __slots__ = ("_capture", "_lock", "_states", "_cleanup_error")

    def __init__(self, capture: Callable[[], object]) -> None:
        self._capture = capture
        self._lock = RLock()
        self._states: dict[object, _LiveAttestationState] = {}
        self._cleanup_error: Exception | None = None

    def _remember_cleanup_failure(self, error: Exception) -> None:
        if self._cleanup_error is None:
            self._cleanup_error = error

    def _close_state(self, owner: object, state: _LiveAttestationState) -> None:
        if self._states.get(owner) is state:
            self._states.pop(owner, None)
        try:
            state.close()
        except Exception as error:
            self._remember_cleanup_failure(error)
            raise

    def _expire(self, owner: object, state: _LiveAttestationState) -> None:
        with self._lock:
            if self._states.get(owner) is not state:
                return
            self._states.pop(owner, None)
            try:
                state.close()
            except Exception as error:
                self._remember_cleanup_failure(error)

    def _start_state(self, owner: object) -> _LiveAttestationState:
        snapshot = self._capture()
        lease = getattr(snapshot, "lease", None)
        assert_stable = getattr(lease, "assert_stable", None)
        close = getattr(lease, "close", None)
        if not callable(assert_stable) or not callable(close):
            if callable(close):
                try:
                    close()
                except Exception as error:
                    self._remember_cleanup_failure(error)
                    raise LiveGuardHostError(
                        "LIVE_GUARD_READ_UNAVAILABLE",
                        "live attestation lease contract is unavailable and cleanup failed",
                    ) from error
            _fail(
                "LIVE_GUARD_READ_UNAVAILABLE",
                "live attestation lease contract is unavailable",
            )
        state = _LiveAttestationState(snapshot, lease)
        self._states[owner] = state
        timer = Timer(
            self._MAX_HOLD_SECONDS,
            self._expire,
            args=(owner, state),
        )
        timer.daemon = True
        state.timer = timer
        try:
            timer.start()
        except Exception:
            self._close_state(owner, state)
            raise
        return state

    def read(self, field: str, repository: str) -> object:
        owner = current_thread()
        with self._lock:
            if self._cleanup_error is not None:
                _fail(
                    "LIVE_GUARD_READ_UNAVAILABLE",
                    "previous live attestation lease cleanup failed",
                )
            state = self._states.get(owner)
            if field == "legacy":
                if state is not None:
                    try:
                        state.lease.assert_stable()
                    except Exception:
                        self._close_state(owner, state)
                        raise
                    self._close_state(owner, state)
                state = self._start_state(owner)
            elif state is None:
                _fail(
                    "LIVE_GUARD_READ_UNAVAILABLE",
                    "Guard reads must start with one legacy read",
                )
            if field != self._FIELDS[state.next_field]:
                self._close_state(owner, state)
                _fail(
                    "LIVE_GUARD_READ_UNAVAILABLE",
                    "Guard reads did not consume one ordered live snapshot",
                )
            try:
                state.lease.assert_stable()
                if getattr(state.snapshot, "subject").repository != repository:
                    _fail(
                        "LIVE_GUARD_READ_UNAVAILABLE",
                        "Guard read repository differs from the live subject",
                    )
                value = getattr(state.snapshot, field, None)
                if value is None:
                    _fail(
                        "LIVE_GUARD_READ_UNAVAILABLE",
                        f"live attestation has no {field} readback",
                    )
                if state.next_field == len(self._FIELDS) - 1:
                    state.lease.assert_stable()
            except Exception:
                self._close_state(owner, state)
                raise
            state.next_field += 1
            if state.next_field == len(self._FIELDS):
                self._close_state(owner, state)
            return value


class _LiveReadPort:
    """Exact read-only Guard adapter; no additional public capability."""

    __slots__ = ("_cycle", "_field", "_repository")

    def __init__(self, cycle: _LiveAttestationCycle, field: str, repository: str) -> None:
        self._cycle = cycle
        self._field = field
        self._repository = repository

    def read(self, repository: str) -> object:
        if repository != self._repository:
            _fail(
                "LIVE_GUARD_READ_UNAVAILABLE",
                "Guard read repository differs from the live composition",
            )
        return self._cycle.read(self._field, repository)


def _compose_live_read_ports(
    *,
    subject: object,
    run_id: str,
    repository_root: Path,
    runtime_config_path: Path,
    store_path: Path,
    package_root: Path,
    install_roots: tuple[Path, Path, Path],
    gateway_store_path: Path,
    artifact_root: Path,
) -> tuple[object, object, object, object, object]:
    """Load and bind the real Beta3 read sources without captured doubles."""

    try:
        import run_beta3_live_guard as runner

        # This call intentionally omits repository_root.  Passing it would
        # install the runner's captured package loader and create a second set
        # of GWO contract classes inside this already composed process.
        runner._validate_v8_module_origins()
        binding = runner.load_production_release_subject()
        release_subject = binding.subject
        runner_config = runner._bind_runner_config_from_subject(release_subject)
        expected_root = Path(release_subject.repository_root).resolve()
        _validate_production_paths(
            repository_root=repository_root,
            runtime_config_path=runtime_config_path,
            gateway_store_path=gateway_store_path,
            artifact_root=artifact_root,
            package_root=package_root,
            install_roots=install_roots,
            expected_repository_root=expected_root,
            expected_runtime_config_path=Path(runner_config.runtime_config_path),
            expected_gateway_store_path=Path(runner_config.gateway_store_path),
            expected_artifact_root=Path(runner_config.artifact_root),
            expected_install_roots=tuple(runner_config.install_roots),
        )
        if Path(runner_config.fresh_store).resolve() != store_path:
            _fail(
                "LIVE_GUARD_PROVENANCE_MISMATCH",
                "fresh Store receipt differs from explicit Store path",
            )
        if Path(runner_config.runtime_config_path).resolve() != runtime_config_path:
            _fail(
                "LIVE_GUARD_PROVENANCE_MISMATCH",
                "runtime configuration differs from the reviewed production path",
            )
        expected_subject = runner._default_subject_factory(
            runner_config,
            release_subject,
        )
        if expected_subject.canonical() != subject.canonical():
            _fail(
                "LIVE_GUARD_PROVENANCE_MISMATCH",
                "release subject and activation Guard subject are not identical",
            )
        runner_hash = runner._runbook_hash()
        attestor_hash = runner._attestor_source_sha256()
        if release_subject.runner.sha256 != runner_hash:
            _fail(
                "LIVE_GUARD_PROVENANCE_MISMATCH",
                "release subject runner hash does not match current runner bytes",
            )
        if release_subject.attestor_bundle_sha256 != attestor_hash:
            _fail(
                "LIVE_GUARD_PROVENANCE_MISMATCH",
                "release subject attestor hash does not match current attestor bytes",
            )
        dependencies = runner._production_dependencies(
            runner_config,
            attestor_hash,
            strict=False,
        )
        scripts_root = Path(runner.__file__).resolve().parent
        for filename in _ATTESTOR_MODULES:
            runner._validate_loaded_module_origin(filename, scripts_root / filename)
        control_module = sys.modules.get("beta3_control_ownership_attestor")
        runtime_config_value = getattr(control_module, "_runtime_config_value", None)
        if not callable(runtime_config_value):
            _fail(
                "LIVE_GUARD_COMPOSITION_INVALID",
                "current attestor module has no runtime configuration reader",
            )
        runtime_configuration, _runtime_readback = runtime_config_value(
            _stable_bytes(runtime_config_path, "runtime_config_path"),
            subject.repository,
        )
        bound_config = replace(
            runner_config,
            gateway_store_path=gateway_store_path,
            artifact_root=artifact_root,
        )
        from beta3_bootstrap_model import AttemptIdentity
        from gwo_v8._canonical import digest_value
        from run_beta3_live_guard import ProductionBootstrapAttestor

        attestor = ProductionBootstrapAttestor(
            control_ownership_attestor=dependencies.control_ownership_attestor,
            legacy_attestor=dependencies.legacy_attestor,
        )

        def capture() -> _LiveAttestationSnapshot:
            attempt = AttemptIdentity.create(
                run_id=run_id,
                repository=subject.repository,
                evidence_root=str(bound_config.evidence_root),
                cutover_subject_digest=digest_value(subject.canonical()),
                runner_sha256=runner_hash,
                attestor_sha256=attestor_hash,
                nonce_factory=secrets.token_hex,
            )
            bundle, lease, _metadata = attestor.attest(
                bound_config,
                attempt,
                release_subject,
            )
            try:
                bundle.validate()
            except Exception:
                lease.close()
                raise
            return _LiveAttestationSnapshot(bundle, lease)

        cycle = _LiveAttestationCycle(capture)
        return (
            _LiveReadPort(cycle, "legacy", subject.repository),
            _LiveReadPort(cycle, "durable_state", subject.repository),
            _LiveReadPort(cycle, "writer_fence", subject.repository),
            _LiveReadPort(cycle, "ownership", subject.repository),
            runtime_configuration,
        )
    except LiveGuardHostError:
        raise
    except Exception as error:
        code = getattr(error, "code", "LIVE_GUARD_COMPOSITION_INVALID")
        raise LiveGuardHostError(
            str(code),
            "authoritative live Guard source composition failed",
        ) from error


def _compose_live_start_host(
    *,
    subject: object,
    run_id: str,
    repository_root: Path,
    runtime_config_path: Path,
    gateway_store_path: Path,
    artifact_root: Path,
    store_path: Path,
    package_root: Path,
    install_roots: tuple[Path, Path, Path],
    github_executable: str = "gh",
    github_timeout_seconds: int = 30,
) -> object:
    from gwo_v8.activation import GitHubCliContentClient
    from gwo_v8.plan_control_host import (
        install_github_plan_control_start,
        make_production_cutover_read_adapter_resolver,
    )
    from gwo_v8.runtime_gateway import RuntimeRepositoryContext

    legacy, durable, writer, ownership, runtime_configuration = _compose_live_read_ports(
        subject=subject,
        run_id=run_id,
        repository_root=repository_root,
        runtime_config_path=runtime_config_path,
        store_path=store_path,
        package_root=package_root,
        install_roots=install_roots,
        gateway_store_path=gateway_store_path,
        artifact_root=artifact_root,
    )
    resolver = make_production_cutover_read_adapter_resolver(
        v61_legacy_read=legacy,
        v8_store_read=durable,
        writer_generation_read=writer,
        v8_ownership_read=ownership,
        runtime_configuration=runtime_configuration,
    )
    client = GitHubCliContentClient(
        github_executable,
        command_timeout_seconds=github_timeout_seconds,
    )
    return install_github_plan_control_start(
        repository=subject.repository,
        control_branch=subject.control_branch,
        target_branch=subject.target_branch,
        writer_generation=subject.source_writer_generation,
        runtime_configuration=runtime_configuration,
        repository_contexts={
            subject.repository: RuntimeRepositoryContext(repository_root, subject.target_branch),
        },
        gateway_store_path=gateway_store_path,
        artifact_root=artifact_root,
        _content_client=client,
        cutover_read_adapter_resolver=resolver,
    )


def install_live_guard_host(
    *,
    subject: object,
    run_id: str,
    repository_root: Path,
    runtime_config_path: Path | None,
    gateway_store_path: Path,
    artifact_root: Path,
    store_path: Path,
    package_root: Path,
    install_roots: tuple[Path, Path, Path],
    github_executable: str = "gh",
    github_timeout_seconds: int = 30,
) -> object:
    """Install one exact live V3 start host in the current process.

    The function accepts only explicit paths and a typed Guard subject.  It has
    no readback-file, replay, or dependency-injection parameter.  Construction
    performs reads only; the returned host is not started and no writer method
    is called.
    """

    if runtime_config_path is None:
        _fail(
            "LIVE_GUARD_CONFIGURATION_REQUIRED",
            "runtime_config_path is required for a live host",
        )
    if type(run_id) is not str or not run_id.strip():
        _fail(
            "LIVE_GUARD_CONFIGURATION_REQUIRED",
            "run_id is required for a live host",
        )
    paths = _validate_explicit_inputs(
        subject=subject,
        repository_root=repository_root,
        runtime_config_path=runtime_config_path,
        gateway_store_path=gateway_store_path,
        artifact_root=artifact_root,
        store_path=store_path,
        package_root=package_root,
        install_roots=install_roots,
    )
    (
        repository_root,
        runtime_config_path,
        gateway_store_path,
        artifact_root,
        store_path,
        install_roots,
    ) = paths
    try:
        return _compose_live_start_host(
            subject=subject,
            run_id=run_id,
            repository_root=repository_root,
            runtime_config_path=runtime_config_path,
            gateway_store_path=gateway_store_path,
            artifact_root=artifact_root,
            store_path=store_path,
            package_root=package_root,
            install_roots=install_roots,
            github_executable=github_executable,
            github_timeout_seconds=github_timeout_seconds,
        )
    except LiveGuardHostError:
        raise
    except Exception as error:
        raise LiveGuardHostError(
            getattr(error, "code", "LIVE_GUARD_COMPOSITION_INVALID"),
            "live Guard start host installation failed",
        ) from error


__all__ = ["LiveGuardHostError", "install_live_guard_host"]
