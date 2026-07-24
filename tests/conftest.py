"""Shared helpers for loading the Orchestrator scripts as fresh modules."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills" / "orchestrator" / "scripts"
CORE_PATH = SCRIPTS / "orch_core.py"
FRONTIER_PATH = SCRIPTS / "orch_frontier.py"
CLI_PATH = SCRIPTS / "orch.py"


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
    return core, load_module("orch_cli_test", CLI_PATH)
