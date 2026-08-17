from __future__ import annotations

from dataclasses import replace
from hashlib import sha256
from pathlib import Path
import sqlite3
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
GWO_SCRIPTS = ROOT / "skills" / "orchestrator" / "scripts"
if str(GWO_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(GWO_SCRIPTS))
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from gwo_v8.activation import (  # noqa: E402
    GitHubCliContentClient,
    GitHubDurablePlanControl,
    LocalPlanPublication,
)
from gwo_v8.compiler import CompiledPlan  # noqa: E402
from gwo_v8.cutover_guard import (  # noqa: E402
    CutoverGuardReceipt,
    CutoverSubject,
)
from gwo_v8.plan_control_host import (  # noqa: E402
    ProductionCutoverGuardHost,
    install_cutover_guard,
)
from gwo_v8.production_activation import (  # noqa: E402
    ProductionActivationAuthorization,
    ProductionActivationComposition,
)
from gwo_v8.production_effects import ProductionCompositionError  # noqa: E402
from gwo_v8.transition import (  # noqa: E402
    CanaryAcceptance,
    GitHubCanaryEvidenceControl,
    GitHubLegacyWriterControl,
    GitHubWriterTransitionControl,
    LegacyWriterReadback,
)
from gwo_v8_production_factory import (  # noqa: E402
    ProductionActivationCompositionFactory,
    ProductionCompositionConfig,
    RollbackLineage,
)
from tests.cutover_guard_test_support import GuardHarness  # noqa: E402


REPOSITORY = "NOirBRight/github-work-orchestrator"


def _create_store(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE v8_plan_revisions (
                repository TEXT NOT NULL,
                plan_digest TEXT NOT NULL,
                canonical_bytes BLOB NOT NULL,
                compilation_record TEXT NOT NULL,
                writer_generation TEXT NOT NULL,
                PRIMARY KEY (repository, plan_digest)
            );
            CREATE TABLE v8_active_plans (
                repository TEXT PRIMARY KEY,
                plan_digest TEXT NOT NULL,
                writer_generation TEXT NOT NULL,
                activation_id TEXT
            );
            CREATE TABLE v8_pending_activations (
                repository TEXT PRIMARY KEY,
                plan_digest TEXT NOT NULL,
                expected_previous_digest TEXT,
                writer_generation TEXT NOT NULL,
                activation_id TEXT NOT NULL,
                receipt_json TEXT NOT NULL
            );
            CREATE TABLE v8_writer_generations (
                repository TEXT PRIMARY KEY,
                writer_generation TEXT NOT NULL
            );
            CREATE TABLE v8_writer_fences (
                repository TEXT PRIMARY KEY,
                writer_generation TEXT NOT NULL,
                activation_id TEXT NOT NULL,
                state TEXT NOT NULL
            );
            """
        )


def _guard_paths(tmp_path: Path) -> tuple[Path, tuple[Path, Path, Path]]:
    package_root = tmp_path / "package"
    package_root.mkdir()
    install_roots = tuple(
        tmp_path / name / "orchestrator"
        for name in (".agents", ".codex", ".claude")
    )
    for path in install_roots:
        path.mkdir(parents=True)
    return package_root, install_roots


def _config(
    tmp_path: Path,
    *,
    store_path: Path | None = None,
    target_repository: str = REPOSITORY,
    guard_paths: bool = True,
) -> ProductionCompositionConfig:
    store = tmp_path / "store.sqlite3" if store_path is None else store_path
    if store_path is None:
        _create_store(store)
    rollback = tmp_path / "rollback.sqlite3"
    rollback.write_bytes(b"immutable rollback lineage")
    kwargs: dict[str, object] = {}
    if guard_paths:
        package_root, install_roots = _guard_paths(tmp_path)
        kwargs.update(
            guard_package_root=package_root,
            guard_install_roots=install_roots,
        )
    return ProductionCompositionConfig(
        target_repository=target_repository,
        control_branch="gwo-control",
        store_path=store,
        store_generation="store:v8:test",
        source_writer_generation="v6.1",
        target_writer_generation="v8",
        rollback_lineage=RollbackLineage(
            store_path=rollback,
            store_sha256=sha256(rollback.read_bytes()).hexdigest(),
            writer_generation="v6.1",
            activation_id="rollback:v61",
        ),
        **kwargs,
    )


def _authorization(*, target_repository: str = REPOSITORY):
    return ProductionActivationAuthorization(
        run_id="phase5-factory-test",
        repository=REPOSITORY,
        merged_main_sha="a" * 40,
        merged_main_git_tree="b" * 40,
        release_subject_digest="c" * 64,
        evidence_root="D:/evidence",
        target_repository=target_repository,
        writer_transition="v6.1 -> v8",
        target_writer_generation="v8",
    )


def _compiled_plan() -> CompiledPlan:
    return CompiledPlan(
        repository=REPOSITORY,
        canonical_bytes=b"{}",
        digest="d" * 64,
        compilation_record={},
    )


def _canary() -> CanaryAcceptance:
    return CanaryAcceptance(
        accepted=True,
        repository=REPOSITORY,
        evidence_package_digest="e" * 64,
        manifest_ref="github://canary-manifest/" + "e" * 64,
        blockers=(),
        evidence_refs=("github://evidence/1",),
    )


def _subject(
    *,
    repository: str = REPOSITORY,
    control_branch: str = "gwo-control",
) -> CutoverSubject:
    return CutoverSubject(
        repository=repository,
        control_branch=control_branch,
        target_branch="main",
        source_writer_generation="v6.1",
        target_writer_generation="v8",
        store_generation="store:v8:test",
        source_commit="a" * 40,
        source_tree_digest="f" * 64,
        production_entry_refs=(),
    )


def _guard_receipt(*, repository: str = REPOSITORY) -> CutoverGuardReceipt:
    return CutoverGuardReceipt(
        schema="gwo.cutover-guard.v1",
        repository=repository,
        subject_digest="1" * 64,
        readback_digest="2" * 64,
        source_writer_generation="v6.1",
        target_writer_generation="v8",
        store_generation="store:v8:test",
        writer_control_ref_digest="3" * 64,
        runtime_configuration_digest="4" * 64,
        compatibility_audit_digest="5" * 64,
        package_readback_digest="6" * 64,
        receipt_digest="7" * 64,
    )


def _compose(
    factory: ProductionActivationCompositionFactory,
    *,
    authorization: ProductionActivationAuthorization | None = None,
    compiled_plan: CompiledPlan | None = None,
    canary: CanaryAcceptance | None = None,
    subject: CutoverSubject | None = None,
    receipt: CutoverGuardReceipt | None = None,
) -> ProductionActivationComposition:
    return factory.compose(
        authorization=_authorization() if authorization is None else authorization,
        compiled_plan=_compiled_plan() if compiled_plan is None else compiled_plan,
        canary=_canary() if canary is None else canary,
        guard_subject=_subject() if subject is None else subject,
        guard_receipt=_guard_receipt() if receipt is None else receipt,
    )


def _install_test_live_guard(monkeypatch):
    import gwo_v8_production_factory as module

    harness = GuardHarness.valid()
    harness.legacy.value = replace(harness.legacy.value, repository=REPOSITORY)
    host = install_cutover_guard(sources=harness.sources)
    monkeypatch.setattr(module, "load_production_cutover_guard", lambda _request: host)
    return host


def test_factory_requires_explicit_immutable_configuration():
    with pytest.raises(ProductionCompositionError) as raised:
        _compose(ProductionActivationCompositionFactory())

    assert raised.value.code == "FACTORY_CONFIGURATION_REQUIRED"


def test_factory_rejects_disjoint_activation_identity_before_store_access(
    tmp_path,
    monkeypatch,
):
    config = _config(tmp_path)
    factory = ProductionActivationCompositionFactory(config)
    monkeypatch.setattr(
        "gwo_v8_production_factory._validate_store",
        lambda *args, **kwargs: pytest.fail(
            "store must not be opened for disjoint identity"
        ),
    )

    with pytest.raises(ProductionCompositionError) as raised:
        _compose(factory, authorization=_authorization(target_repository="other/repo"))

    assert raised.value.code == "FACTORY_IDENTITY_DISJOINT"


@pytest.mark.parametrize("case", ("missing", "directory", "invalid_sqlite", "sidecar"))
def test_factory_rejects_missing_wrong_or_unsafe_store(tmp_path, case):
    if case == "missing":
        path = tmp_path / "missing.sqlite3"
        config = _config(tmp_path, store_path=path)
    elif case == "directory":
        path = tmp_path / "store.sqlite3"
        path.mkdir()
        config = _config(tmp_path, store_path=path)
    elif case == "invalid_sqlite":
        path = tmp_path / "store.sqlite3"
        path.write_bytes(b"not sqlite")
        config = _config(tmp_path, store_path=path)
    else:
        path = tmp_path / "store.sqlite3"
        _create_store(path)
        Path(f"{path}-wal").write_bytes(b"sidecar")
        config = _config(tmp_path, store_path=path)

    with pytest.raises(ProductionCompositionError) as raised:
        _compose(ProductionActivationCompositionFactory(config))

    assert raised.value.code == "FACTORY_STORE_UNSAFE"


def test_factory_rejects_activation_and_rollback_store_identity_collision(tmp_path):
    config = _config(tmp_path)
    store = config.store_path
    lineage = RollbackLineage(
        store_path=store,
        store_sha256=sha256(store.read_bytes()).hexdigest(),
        writer_generation="v6.1",
        activation_id="rollback:v61",
    )
    config = replace(config, rollback_lineage=lineage)

    with pytest.raises(ProductionCompositionError) as raised:
        _compose(ProductionActivationCompositionFactory(config))

    assert raised.value.code == "FACTORY_STORE_IDENTITY_DISJOINT"


def test_factory_separates_store_identity_from_writer_generation(
    tmp_path,
    monkeypatch,
):
    _install_test_live_guard(monkeypatch)
    store_generation = "store:v8:production:20260817T205916Z"
    writer_generation = "v8-generation-1"
    config = replace(
        _config(tmp_path),
        store_generation=store_generation,
        target_writer_generation=writer_generation,
    )
    authorization = replace(
        _authorization(),
        target_writer_generation=writer_generation,
    )
    subject = replace(
        _subject(),
        store_generation=store_generation,
        target_writer_generation=writer_generation,
    )
    receipt = replace(
        _guard_receipt(),
        store_generation=store_generation,
        target_writer_generation=writer_generation,
    )

    composition = _compose(
        ProductionActivationCompositionFactory(config),
        authorization=authorization,
        subject=subject,
        receipt=receipt,
    )

    assert composition.controller.transitions.initial_writer == "v6.1"
    assert composition.controller.publication.store_path == config.store_path


def test_factory_accepts_provisioned_store_genesis_without_mutating_store(
    tmp_path,
    monkeypatch,
):
    _install_test_live_guard(monkeypatch)
    store_generation = "store:v8:production:20260817T205916Z"
    writer_generation = "v8-generation-1"
    config = replace(
        _config(tmp_path),
        store_generation=store_generation,
        target_writer_generation=writer_generation,
    )
    authorization = replace(
        _authorization(),
        target_writer_generation=writer_generation,
    )
    subject = replace(
        _subject(),
        store_generation=store_generation,
        target_writer_generation=writer_generation,
    )
    receipt = replace(
        _guard_receipt(),
        store_generation=store_generation,
        target_writer_generation=writer_generation,
    )
    with sqlite3.connect(config.store_path) as connection:
        connection.execute(
            "INSERT INTO v8_writer_generations(repository, writer_generation) VALUES (?, ?)",
            (REPOSITORY, store_generation),
        )
    before = config.store_path.read_bytes()

    composition = _compose(
        ProductionActivationCompositionFactory(config),
        authorization=authorization,
        subject=subject,
        receipt=receipt,
    )

    assert composition.controller.publication.store_path == config.store_path
    assert config.store_path.read_bytes() == before
    with sqlite3.connect(config.store_path) as connection:
        assert connection.execute(
            "SELECT repository, writer_generation FROM v8_writer_generations"
        ).fetchall() == [(REPOSITORY, store_generation)]


def test_factory_rejects_non_genesis_store_writer_identity(
    tmp_path,
    monkeypatch,
):
    _install_test_live_guard(monkeypatch)
    store_generation = "store:v8:production:20260817T205916Z"
    writer_generation = "v8-generation-1"
    config = replace(
        _config(tmp_path),
        store_generation=store_generation,
        target_writer_generation=writer_generation,
    )
    authorization = replace(
        _authorization(),
        target_writer_generation=writer_generation,
    )
    subject = replace(
        _subject(),
        store_generation=store_generation,
        target_writer_generation=writer_generation,
    )
    receipt = replace(
        _guard_receipt(),
        store_generation=store_generation,
        target_writer_generation=writer_generation,
    )
    with sqlite3.connect(config.store_path) as connection:
        connection.execute(
            "INSERT INTO v8_writer_generations(repository, writer_generation) VALUES (?, ?)",
            (REPOSITORY, writer_generation),
        )

    with pytest.raises(ProductionCompositionError) as raised:
        _compose(
            ProductionActivationCompositionFactory(config),
            authorization=authorization,
            subject=subject,
            receipt=receipt,
        )

    assert raised.value.code == "FACTORY_STORE_WRITER_IDENTITY_INVALID"


def test_factory_binds_real_controls_to_one_client_and_publication(
    tmp_path,
    monkeypatch,
):
    _install_test_live_guard(monkeypatch)
    config = _config(tmp_path)
    before = config.store_path.read_bytes()

    composition = _compose(ProductionActivationCompositionFactory(config))

    assert type(composition) is ProductionActivationComposition
    controller = composition.controller
    assert type(controller.publication) is LocalPlanPublication
    assert type(controller.transitions) is GitHubWriterTransitionControl
    assert type(controller.legacy) is GitHubLegacyWriterControl
    assert type(controller.publication.durable) is GitHubDurablePlanControl
    assert type(composition.canary_evidence_control) is GitHubCanaryEvidenceControl
    assert type(controller.guard) is ProductionCutoverGuardHost

    client = controller.transitions.client
    assert type(client) is GitHubCliContentClient
    assert controller.legacy.client is client
    assert controller.publication.durable.client is client
    assert composition.canary_evidence_control.client is client
    assert controller.publication.writer_authority is controller.transitions
    assert controller.publication.store_path == config.store_path
    assert config.store_path.read_bytes() == before

    execution = controller.legacy.execution_readback(REPOSITORY)
    assert type(execution) is LegacyWriterReadback
    assert execution.repository == REPOSITORY


def test_factory_live_guard_unavailability_is_a_concrete_fail_closed_error(
    tmp_path,
    monkeypatch,
):
    import gwo_v8_production_factory as module

    def unavailable(_request):
        raise RuntimeError("installed V3 host has no resolver-backed read ports")

    monkeypatch.setattr(module, "load_production_cutover_guard", unavailable)

    with pytest.raises(ProductionCompositionError) as raised:
        _compose(ProductionActivationCompositionFactory(_config(tmp_path)))

    assert raised.value.code == "FACTORY_GUARD_LIVE_UNAVAILABLE"
    assert "resolver-backed read ports" in raised.value.detail


def test_factory_requires_guard_composition_inputs_instead_of_static_go(tmp_path):
    with pytest.raises(ProductionCompositionError) as raised:
        _compose(
            ProductionActivationCompositionFactory(
                _config(tmp_path, guard_paths=False)
            )
        )

    assert raised.value.code == "FACTORY_GUARD_CONFIGURATION_REQUIRED"


def test_factory_configuration_and_rollback_lineage_are_immutable(tmp_path):
    config = _config(tmp_path)

    with pytest.raises((AttributeError, TypeError)):
        config.store_path = tmp_path / "other.sqlite3"  # type: ignore[misc]
    with pytest.raises((AttributeError, TypeError)):
        config.rollback_lineage.store_path = tmp_path / "other.sqlite3"  # type: ignore[misc]


def test_guard_host_is_not_called_during_compose(tmp_path, monkeypatch):
    calls: list[object] = []
    import gwo_v8_production_factory as module

    harness = GuardHarness.valid()
    host = install_cutover_guard(sources=harness.sources)

    def compose_guard(request):
        calls.append(request)
        return host

    monkeypatch.setattr(module, "load_production_cutover_guard", compose_guard)
    _compose(ProductionActivationCompositionFactory(_config(tmp_path)))

    assert len(calls) == 1
    assert harness.external_writes == {
        "repository": 0,
        "sqlite": 0,
        "github": 0,
        "process": 0,
        "runtime": 0,
    }
