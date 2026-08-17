from __future__ import annotations

from pathlib import Path
import sys

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
