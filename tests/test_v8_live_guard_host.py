from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import sys
import threading
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
GWO_SCRIPTS = ROOT / "skills" / "orchestrator" / "scripts"
for path in (ROOT, SCRIPTS, GWO_SCRIPTS):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))


from gwo_v8_live_guard_host import (  # noqa: E402
    LiveGuardHostError,
    install_live_guard_host,
)


def _subject():
    from gwo_v8.cutover_guard import CutoverSubject

    return CutoverSubject(
        repository="NOirBRight/github-work-orchestrator",
        control_branch="gwo-control",
        target_branch="main",
        source_writer_generation="v6.1",
        target_writer_generation="v8-generation-1",
        store_generation="store:v8:test",
        source_commit="a" * 40,
        source_tree_digest="b" * 64,
        production_entry_refs=(
            "gwo_v8.plan_control_host:ProductionPlanControlStartHost.start",
            "gwo_v8.execution_kernel:advance",
            "gwo_v8.execution_kernel:inspect",
        ),
    )


def test_live_host_requires_every_explicit_path_before_loading_runtime(tmp_path):
    with pytest.raises(LiveGuardHostError) as raised:
        install_live_guard_host(
            subject=_subject(),
            run_id="test-run",
            repository_root=tmp_path,
            runtime_config_path=None,
            gateway_store_path=tmp_path / "gateway.sqlite3",
            artifact_root=tmp_path / "artifacts",
            store_path=tmp_path / "store.sqlite3",
            package_root=tmp_path / "package",
            install_roots=(
                tmp_path / ".agents" / "skills",
                tmp_path / ".codex" / "skills",
                tmp_path / ".claude" / "skills",
            ),
        )

    assert raised.value.code == "LIVE_GUARD_CONFIGURATION_REQUIRED"


def test_live_host_bootstrap_is_explicit_and_does_not_accept_readback_bundle(
    tmp_path,
    monkeypatch,
):
    import gwo_v8_live_guard_host as module

    calls: list[dict[str, object]] = []
    sentinel = object()

    def compose(**kwargs):
        calls.append(kwargs)
        return sentinel

    monkeypatch.setattr(module, "_compose_live_start_host", compose)
    monkeypatch.setattr(module, "_validate_explicit_inputs", lambda **_kwargs: (
        tmp_path,
        tmp_path / "config.json",
        tmp_path / "gateway.sqlite3",
        tmp_path / "artifacts",
        tmp_path / "store.sqlite3",
        (
            tmp_path / ".agents" / "skills",
            tmp_path / ".codex" / "skills",
            tmp_path / ".claude" / "skills",
        ),
    ))
    result = install_live_guard_host(
        subject=_subject(),
        run_id="test-run",
        repository_root=tmp_path,
        runtime_config_path=tmp_path / "config.json",
        gateway_store_path=tmp_path / "gateway.sqlite3",
        artifact_root=tmp_path / "artifacts",
        store_path=tmp_path / "store.sqlite3",
        package_root=tmp_path / "package",
        install_roots=(
            tmp_path / ".agents" / "skills",
            tmp_path / ".codex" / "skills",
            tmp_path / ".claude" / "skills",
        ),
    )

    assert result is sentinel
    assert len(calls) == 1
    assert "readback_bundle" not in calls[0]
    assert calls[0]["subject"] == _subject()


def test_live_host_binds_the_operator_run_id_to_bootstrap_attestation(
    tmp_path,
    monkeypatch,
):
    import gwo_v8_live_guard_host as module

    calls: list[dict[str, object]] = []

    def compose(**kwargs):
        calls.append(kwargs)
        return object()

    monkeypatch.setattr(module, "_compose_live_start_host", compose)
    monkeypatch.setattr(module, "_validate_explicit_inputs", lambda **_kwargs: (
        tmp_path,
        tmp_path / "config.json",
        tmp_path / "gateway.sqlite3",
        tmp_path / "artifacts",
        tmp_path / "store.sqlite3",
        (
            tmp_path / ".agents" / "skills",
            tmp_path / ".codex" / "skills",
            tmp_path / ".claude" / "skills",
        ),
    ))

    install_live_guard_host(
        subject=_subject(),
        run_id="authorized-run-123",
        repository_root=tmp_path,
        runtime_config_path=tmp_path / "config.json",
        gateway_store_path=tmp_path / "gateway.sqlite3",
        artifact_root=tmp_path / "artifacts",
        store_path=tmp_path / "store.sqlite3",
        package_root=tmp_path / "package",
        install_roots=(
            tmp_path / ".agents" / "skills",
            tmp_path / ".codex" / "skills",
            tmp_path / ".claude" / "skills",
        ),
    )

    assert calls[0]["run_id"] == "authorized-run-123"


def test_live_host_rejects_noncanonical_production_paths_before_start_install(
    tmp_path,
):
    import gwo_v8_live_guard_host as module

    with pytest.raises(module.LiveGuardHostError) as raised:
        module._validate_production_paths(
            repository_root=tmp_path,
            runtime_config_path=tmp_path / "config.json",
            gateway_store_path=tmp_path / "gateway.sqlite3",
            artifact_root=tmp_path / "artifacts",
            package_root=tmp_path / "package",
            install_roots=(
                tmp_path / ".agents" / "skills",
                tmp_path / ".codex" / "skills",
                tmp_path / ".claude" / "skills",
            ),
            expected_repository_root=tmp_path,
            expected_runtime_config_path=tmp_path / "config.json",
            expected_gateway_store_path=tmp_path / "gateway.sqlite3",
            expected_artifact_root=tmp_path / "artifacts",
            expected_install_roots=(
                tmp_path / ".agents" / "skills",
                tmp_path / ".codex" / "skills",
                tmp_path / ".claude" / "skills",
            ),
        )

    assert raised.value.code == "LIVE_GUARD_PROVENANCE_MISMATCH"


def test_live_host_rejects_reparse_path_before_resolving_it(tmp_path, monkeypatch):
    import gwo_v8_live_guard_host as module

    reparse_path = tmp_path / "reparse"
    reparse_path.mkdir()
    monkeypatch.setattr(
        module,
        "_is_reparse_or_link",
        lambda path: path == reparse_path,
    )

    with pytest.raises(LiveGuardHostError) as raised:
        module._absolute(reparse_path, "repository_root")

    assert raised.value.code == "LIVE_GUARD_CONFIGURATION_INVALID"


def test_live_host_uses_the_release_subject_bound_attestor_path(monkeypatch, tmp_path):
    import gwo_v8_live_guard_host as module
    import run_beta3_live_guard as runner

    subject = _subject()
    release_subject = SimpleNamespace(
        repository=subject.repository,
        repository_root=str(tmp_path),
        runner=SimpleNamespace(sha256="runner-sha256"),
        attestor_bundle_sha256="attestor-sha256",
    )
    config = replace(
        runner.DEFAULT_CONFIG,
        repository_root=tmp_path,
        evidence_root=tmp_path,
        repository=subject.repository,
        runtime_config_path=tmp_path / "config.json",
        gateway_store_path=tmp_path / "gateway.sqlite3",
        artifact_root=tmp_path / "artifacts",
        fresh_store=tmp_path / "store.sqlite3",
    )
    config.runtime_config_path.write_bytes(b"{}")
    dependencies = SimpleNamespace(
        control_ownership_attestor=object(),
        legacy_attestor=object(),
    )
    runtime_configuration = object()
    attestor_calls: list[dict[str, object]] = []

    class FakeAttestor:
        def __init__(self, **kwargs):
            attestor_calls.append(kwargs)

    monkeypatch.setattr(runner, "_validate_v8_module_origins", lambda: None)
    monkeypatch.setattr(
        runner,
        "load_production_release_subject",
        lambda: SimpleNamespace(subject=release_subject),
    )
    monkeypatch.setattr(runner, "_bind_runner_config_from_subject", lambda _subject: config)
    monkeypatch.setattr(
        runner,
        "_default_subject_factory",
        lambda _config, _release_subject: subject,
    )
    monkeypatch.setattr(runner, "_runbook_hash", lambda: "runner-sha256")
    monkeypatch.setattr(runner, "_attestor_source_sha256", lambda: "attestor-sha256")
    monkeypatch.setattr(runner, "_production_dependencies", lambda *_args, **_kwargs: dependencies)
    monkeypatch.setattr(runner, "_validate_loaded_module_origin", lambda *_args: None)
    monkeypatch.setattr(runner, "ProductionBootstrapAttestor", FakeAttestor)
    monkeypatch.setattr(module, "_validate_production_paths", lambda **_kwargs: None)
    monkeypatch.setitem(
        sys.modules,
        "beta3_control_ownership_attestor",
        SimpleNamespace(
            _runtime_config_value=lambda *_args: (runtime_configuration, object())
        ),
    )

    module._compose_live_read_ports(
        subject=subject,
        run_id="test-run",
        repository_root=tmp_path,
        runtime_config_path=config.runtime_config_path,
        store_path=config.fresh_store,
        package_root=tmp_path,
        install_roots=(tmp_path, tmp_path, tmp_path),
        gateway_store_path=config.gateway_store_path,
        artifact_root=config.artifact_root,
    )

    assert attestor_calls == [
        {
            "control_ownership_attestor": dependencies.control_ownership_attestor,
            "legacy_attestor": dependencies.legacy_attestor,
        }
    ]


def test_live_attestation_cycle_holds_each_lease_and_isolates_evaluations():
    import gwo_v8_live_guard_host as module

    class Lease:
        def __init__(self):
            self.close_count = 0

        def assert_stable(self):
            return None

        def close(self):
            self.close_count += 1

    snapshots: list[SimpleNamespace] = []

    def capture():
        generation = len(snapshots) + 1
        lease = Lease()
        snapshot = SimpleNamespace(
            subject=SimpleNamespace(repository="owner/repo"),
            legacy=f"legacy-{generation}",
            durable_state=f"durable-{generation}",
            writer_fence=f"writer-{generation}",
            ownership=f"ownership-{generation}",
            lease=lease,
        )
        snapshots.append(snapshot)
        return snapshot

    cycle = module._LiveAttestationCycle(capture)
    first_read = threading.Event()
    allow_first_evaluation = threading.Event()
    second_read_finished = threading.Event()
    errors: list[BaseException] = []

    def first_evaluation():
        try:
            assert cycle.read("legacy", "owner/repo") == "legacy-1"
            first_read.set()
            assert allow_first_evaluation.wait(2)
            for field in ("durable_state", "writer_fence", "ownership"):
                cycle.read(field, "owner/repo")
        except BaseException as error:
            errors.append(error)

    second_values: list[str] = []

    def second_evaluation():
        try:
            assert first_read.wait(2)
            second_values.append(cycle.read("legacy", "owner/repo"))
            for field in ("durable_state", "writer_fence", "ownership"):
                cycle.read(field, "owner/repo")
        except BaseException as error:
            errors.append(error)
        finally:
            second_read_finished.set()

    first_thread = threading.Thread(target=first_evaluation)
    second_thread = threading.Thread(target=second_evaluation)
    first_thread.start()
    assert first_read.wait(2)
    second_thread.start()
    try:
        assert second_read_finished.wait(1)
    finally:
        allow_first_evaluation.set()
    first_thread.join(2)
    second_thread.join(2)

    assert not first_thread.is_alive()
    assert not second_thread.is_alive()
    assert errors == []
    assert second_values == ["legacy-2"]
    assert [snapshot.lease.close_count for snapshot in snapshots] == [1, 1]


def test_live_attestation_cycle_restarts_after_a_one_shot_legacy_read():
    import gwo_v8_live_guard_host as module

    class Lease:
        def __init__(self):
            self.close_count = 0

        def assert_stable(self):
            return None

        def close(self):
            self.close_count += 1

    snapshots: list[SimpleNamespace] = []

    def capture():
        generation = len(snapshots) + 1
        snapshot = SimpleNamespace(
            subject=SimpleNamespace(repository="owner/repo"),
            legacy=f"legacy-{generation}",
            durable_state=f"durable-{generation}",
            writer_fence=f"writer-{generation}",
            ownership=f"ownership-{generation}",
            lease=Lease(),
        )
        snapshots.append(snapshot)
        return snapshot

    cycle = module._LiveAttestationCycle(capture)

    assert cycle.read("legacy", "owner/repo") == "legacy-1"
    values = tuple(
        cycle.read(field, "owner/repo")
        for field in ("legacy", "durable_state", "writer_fence", "ownership")
    )

    assert values == ("legacy-2", "durable-2", "writer-2", "ownership-2")
    assert [snapshot.lease.close_count for snapshot in snapshots] == [1, 1]


def test_live_attestation_cycle_releases_an_abandoned_thread_snapshot(monkeypatch):
    import gwo_v8_live_guard_host as module

    monkeypatch.setattr(module._LiveAttestationCycle, "_MAX_HOLD_SECONDS", 0.05, raising=False)

    class Lease:
        def __init__(self):
            self.close_count = 0
            self.closed = threading.Event()

        def assert_stable(self):
            return None

        def close(self):
            self.close_count += 1
            self.closed.set()

    snapshots: list[SimpleNamespace] = []

    def capture():
        generation = len(snapshots) + 1
        snapshot = SimpleNamespace(
            subject=SimpleNamespace(repository="owner/repo"),
            legacy=f"legacy-{generation}",
            durable_state=f"durable-{generation}",
            writer_fence=f"writer-{generation}",
            ownership=f"ownership-{generation}",
            lease=Lease(),
        )
        snapshots.append(snapshot)
        return snapshot

    cycle = module._LiveAttestationCycle(capture)

    abandoned = threading.Thread(
        target=lambda: cycle.read("legacy", "owner/repo"),
        daemon=True,
    )
    abandoned.start()
    abandoned.join(1)
    assert not abandoned.is_alive()

    values: list[str] = []
    errors: list[BaseException] = []

    def evaluate():
        try:
            values.extend(
                cycle.read(field, "owner/repo")
                for field in ("legacy", "durable_state", "writer_fence", "ownership")
            )
        except BaseException as error:
            errors.append(error)

    survivor = threading.Thread(target=evaluate, daemon=True)
    survivor.start()
    survivor.join(1)

    assert not survivor.is_alive()
    assert errors == []
    assert values == ["legacy-2", "durable-2", "writer-2", "ownership-2"]
    assert snapshots[0].lease.closed.wait(1)
    assert [snapshot.lease.close_count for snapshot in snapshots] == [1, 1]


def test_live_attestation_cycle_asserts_lease_stability_before_each_read():
    import gwo_v8_live_guard_host as module

    class Lease:
        def __init__(self):
            self.assertions = 0
            self.close_count = 0
            self.drifted = False

        def assert_stable(self):
            self.assertions += 1
            if self.drifted:
                raise RuntimeError("source drift")

        def close(self):
            self.close_count += 1

    lease = Lease()
    snapshot = SimpleNamespace(
        subject=SimpleNamespace(repository="owner/repo"),
        legacy="legacy",
        durable_state="durable",
        writer_fence="writer",
        ownership="ownership",
        lease=lease,
    )
    cycle = module._LiveAttestationCycle(lambda: snapshot)

    assert cycle.read("legacy", "owner/repo") == "legacy"
    lease.drifted = True
    with pytest.raises(RuntimeError, match="source drift"):
        cycle.read("durable_state", "owner/repo")

    assert lease.assertions == 2
    assert lease.close_count == 1


def test_live_attestation_cycle_closes_lease_rejected_for_missing_stability_contract():
    import gwo_v8_live_guard_host as module

    class Lease:
        def __init__(self):
            self.close_count = 0

        def close(self):
            self.close_count += 1

    lease = Lease()
    cycle = module._LiveAttestationCycle(
        lambda: SimpleNamespace(lease=lease),
    )

    with pytest.raises(LiveGuardHostError, match="lease contract is unavailable"):
        cycle.read("legacy", "owner/repo")

    assert lease.close_count == 1


def test_live_attestation_cycle_fails_closed_after_timer_close_failure(monkeypatch):
    import gwo_v8_live_guard_host as module

    monkeypatch.setattr(module._LiveAttestationCycle, "_MAX_HOLD_SECONDS", 0.05, raising=False)

    class Lease:
        def __init__(self):
            self.close_count = 0
            self.close_attempted = threading.Event()

        def assert_stable(self):
            return None

        def close(self):
            self.close_count += 1
            self.close_attempted.set()
            raise RuntimeError("close failed")

    lease = Lease()
    snapshots: list[SimpleNamespace] = []

    def capture():
        snapshots.append(
            SimpleNamespace(
                subject=SimpleNamespace(repository="owner/repo"),
                legacy="legacy",
                durable_state="durable",
                writer_fence="writer",
                ownership="ownership",
                lease=lease,
            )
        )
        return snapshots[-1]

    cycle = module._LiveAttestationCycle(capture)

    assert cycle.read("legacy", "owner/repo") == "legacy"
    assert lease.close_attempted.wait(1)

    with pytest.raises(LiveGuardHostError, match="lease cleanup failed"):
        cycle.read("legacy", "owner/repo")

    assert len(snapshots) == 1
    assert lease.close_count == 1


def test_live_attestation_cycle_asserts_stability_after_a_successful_snapshot():
    import gwo_v8_live_guard_host as module

    class Lease:
        def __init__(self):
            self.assertions = 0
            self.close_count = 0

        def assert_stable(self):
            self.assertions += 1

        def close(self):
            self.close_count += 1

    lease = Lease()
    snapshot = SimpleNamespace(
        subject=SimpleNamespace(repository="owner/repo"),
        legacy="legacy",
        durable_state="durable",
        writer_fence="writer",
        ownership="ownership",
        lease=lease,
    )
    cycle = module._LiveAttestationCycle(lambda: snapshot)

    values = tuple(
        cycle.read(field, "owner/repo")
        for field in ("legacy", "durable_state", "writer_fence", "ownership")
    )

    assert values == ("legacy", "durable", "writer", "ownership")
    assert lease.assertions == 5
    assert lease.close_count == 1
