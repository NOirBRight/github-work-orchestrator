"""Shared helpers for loading the Orchestrator scripts as fresh modules."""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills" / "orchestrator" / "scripts"
CORE_PATH = SCRIPTS / "orch_core.py"
FRONTIER_PATH = SCRIPTS / "orch_frontier.py"
CLI_PATH = SCRIPTS / "orch.py"


@pytest.fixture(autouse=True)
def _test_only_fsync_noop(monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest):
    """Keep ordinary acceptance tests fast without weakening production I/O."""

    if (
        request.node.path.name != "test_v8_runtime_gateway_repair.py"
        or request.node.get_closest_marker("real_fsync") is not None
    ):
        return
    monkeypatch.setattr(os, "fsync", lambda _descriptor: None)


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "real_fsync: exercise real file durability instead of the test-only fsync no-op",
    )


def load_module(name: str, path: Path, *, register: bool = False):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    if register:
        sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def load_core(name: str = "orch_core_test"):
    return load_module(name, CORE_PATH)


def load_frontier(name: str = "orch_frontier_test"):
    return load_module(name, FRONTIER_PATH)


def load_modules():
    """Load a fresh (orch_core, orch.py) pair with the CLI bound to that core."""

    core = load_module("orch_core", CORE_PATH, register=True)
    cli = load_module("orch_cli_test", CLI_PATH)
    # Unit tests inject GitHub/Paseo collaborators and must not inherit a
    # developer's authenticated gh session. Fence-specific tests override this.
    cli._legacy_writer_stopped = lambda _repository: False
    return core, cli
