from __future__ import annotations

import importlib.util
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = ROOT / "skills" / "github-work-orchestrator" / "scripts"


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise AssertionError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def load_store():
    return load_module("gwo_store", SCRIPT_DIR / "gwo_store.py")


def load_status():
    return load_module("gwo_status", SCRIPT_DIR / "gwo_status.py")


class RecoveryFixture:
    def __init__(self, test_case: unittest.TestCase, *, repo: str = "owner/repo"):
        self.test_case = test_case
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self._saved_env = {
            "GWO_HOME": os.environ.get("GWO_HOME"),
            "GWO_AGENT_ID": os.environ.get("GWO_AGENT_ID"),
        }
        os.environ["GWO_HOME"] = str(self.home)
        os.environ["GWO_AGENT_ID"] = "coordinator-001"
        self.store_mod = load_store()
        self.status_mod = load_status()
        self.repo = repo
        self.store = self.store_mod.Store.connect(self.home, repo)
        self.store.claim_coordinator()

    def cleanup(self) -> None:
        self.store.close()
        self.tmp.cleanup()
        for key, value in self._saved_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


class DoctorRebuildTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = RecoveryFixture(self)
        self.store = self.fixture.store
        self.status_mod = self.fixture.status_mod

    def tearDown(self) -> None:
        self.fixture.cleanup()

    def test_rebuild_empty_snapshot_is_safe(self) -> None:
        result = self.store.doctor_rebuild(
            github_snapshot={"issues": [], "agents": [], "worktrees": []},
            adapter_listing=[],
            git_worktrees=[],
        )
        self.assertTrue(result["rebuilt"])
        self.assertEqual([], result["ambiguities"])

    def test_rebuild_reconstructs_task_from_issue(self) -> None:
        result = self.store.doctor_rebuild(
            github_snapshot={
                "issues": [
                    {
                        "number": 42,
                        "labels": ["gwo-v7", "enhancement"],
                        "group": "g-42",
                        "risk": "standard",
                        "hotset": ["src/auth/"],
                        "deps": [],
                    }
                ],
                "agents": [],
                "worktrees": [],
            },
            adapter_listing=[],
            git_worktrees=[],
        )
        self.assertTrue(result["rebuilt"])
        tasks = self.store.list_tasks()
        self.assertEqual(1, len(tasks))
        self.assertEqual(42, tasks[0]["issue"])

    def test_rebuild_surfaces_ambiguity_for_conflicting_agent(self) -> None:
        result = self.store.doctor_rebuild(
            github_snapshot={"issues": [], "agents": [], "worktrees": []},
            adapter_listing=[
                {"agent_id": "worker-001", "status": "running"},
                {"agent_id": "worker-001", "status": "exited"},
            ],
            git_worktrees=[],
        )
        self.assertTrue(result["rebuilt"])
        self.assertGreater(len(result["ambiguities"]), 0)

    def test_rebuild_preserves_existing_store_data(self) -> None:
        self.store.create_task(issue=99, group_label="g-99", risk="fast")
        result = self.store.doctor_rebuild(
            github_snapshot={"issues": [], "agents": [], "worktrees": []},
            adapter_listing=[],
            git_worktrees=[],
        )
        self.assertTrue(result["rebuilt"])
        tasks = self.store.list_tasks()
        issues = [t["issue"] for t in tasks]
        self.assertIn(99, issues)

    def test_rebuild_does_not_destructively_infer_missing_risk(self) -> None:
        result = self.store.doctor_rebuild(
            github_snapshot={
                "issues": [
                    {"number": 77, "labels": [], "group": "g-77"},
                ],
                "agents": [],
                "worktrees": [],
            },
            adapter_listing=[],
            git_worktrees=[],
        )
        self.assertTrue(result["rebuilt"])
        self.assertGreater(len(result["ambiguities"]), 0)


class ConfigCheckTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = RecoveryFixture(self)
        self.store = self.fixture.store
        self.status_mod = self.fixture.status_mod

    def tearDown(self) -> None:
        self.fixture.cleanup()

    def test_config_check_valid_with_defaults(self) -> None:
        result = self.store.config_check()
        self.assertTrue(result["valid"])
        self.assertEqual([], result["errors"])

    def test_config_check_reports_missing_home(self) -> None:
        result = self.store.config_check(gwo_home=str(self.fixture.home / "nonexistent"))
        self.assertFalse(result["valid"])
        self.assertGreater(len(result["errors"]), 0)


if __name__ == "__main__":
    unittest.main()