from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = ROOT / "skills" / "github-work-orchestrator" / "scripts"
GWO_PY = SCRIPT_DIR / "gwo.py"


def load_store():
    import importlib.util
    sys.path.insert(0, str(SCRIPT_DIR))
    spec = importlib.util.spec_from_file_location("gwo_store", SCRIPT_DIR / "gwo_store.py")
    if spec is None or spec.loader is None:
        raise AssertionError("cannot load gwo_store")
    module = importlib.util.module_from_spec(spec)
    sys.modules["gwo_store"] = module
    spec.loader.exec_module(module)
    return module


class CliFixture:
    """Run gwo.py against an isolated temporary GWO_HOME."""

    def __init__(self, repo: str = "owner/repo", *, claim: bool = False) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.repo = repo
        self._saved_env = {
            "GWO_HOME": os.environ.get("GWO_HOME"),
            "GWO_REPOSITORY": os.environ.get("GWO_REPOSITORY"),
            "GWO_AGENT_ID": os.environ.get("GWO_AGENT_ID"),
            "PYTHONPATH": os.environ.get("PYTHONPATH"),
        }
        os.environ["GWO_HOME"] = str(self.home)
        os.environ["GWO_REPOSITORY"] = repo
        os.environ["GWO_AGENT_ID"] = "coordinator-001"
        os.environ["PYTHONPATH"] = str(SCRIPT_DIR) + os.pathsep + os.environ.get("PYTHONPATH", "")
        if claim:
            self.run("coordinator", "claim")

    def cleanup(self) -> None:
        for key, value in self._saved_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        self.tmp.cleanup()

    def run(self, *args: str):
        import subprocess
        return subprocess.run(
            [sys.executable, str(GWO_PY), "--repository", self.repo, *args],
            capture_output=True,
            text=True,
            check=False,
        )


class StoreFixture:
    """Open a store against an isolated temporary GWO_HOME."""

    def __init__(self, test_case, *, repo: str = "owner/repo", claim: bool = True):
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
        self.repo = repo
        self.store = self.store_mod.Store.connect(self.home, repo, caller_agent_id="coordinator-001")
        if claim:
            self.store.claim_coordinator()

    def cleanup(self) -> None:
        self.store.close()
        self.tmp.cleanup()
        for key, value in self._saved_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


class LeaseStoreTests(unittest.TestCase):
    """Direct store-level red tests for lease primitives."""

    def setUp(self) -> None:
        os.environ["PYTHONPATH"] = str(SCRIPT_DIR) + os.pathsep + os.environ.get("PYTHONPATH", "")
        self.fixture = StoreFixture(self)
        self.store = self.fixture.store
        self.store_mod = self.fixture.store_mod

    def tearDown(self) -> None:
        self.fixture.cleanup()

    def test_store_has_acquire_integration_lease(self) -> None:
        self.assertTrue(hasattr(self.store, "acquire_integration_lease"))

    def test_store_has_release_integration_lease(self) -> None:
        self.assertTrue(hasattr(self.store, "release_integration_lease"))

    def test_store_has_integration_lease_holder(self) -> None:
        self.assertTrue(hasattr(self.store, "integration_lease_holder"))

    def test_acquire_creates_holder(self) -> None:
        lease = self.store.acquire_integration_lease(scope="repo:owner/repo:integration")
        self.assertEqual("coordinator-001", lease["holder_agent"])
        holder = self.store.integration_lease_holder("repo:owner/repo:integration")
        self.assertEqual("coordinator-001", holder)

    def test_second_acquire_rejected(self) -> None:
        self.store.acquire_integration_lease(scope="repo:owner/repo:integration")
        with self.assertRaises(self.store_mod.StoreError) as ctx:
            self.store.acquire_integration_lease(scope="repo:owner/repo:integration")
        self.assertIn("coordinator-001", str(ctx.exception))

    def test_release_requires_holder(self) -> None:
        self.store.acquire_integration_lease(scope="repo:owner/repo:integration")
        os.environ["GWO_AGENT_ID"] = "integrator-002"
        with self.assertRaises(self.store_mod.TransitionError) as ctx:
            self.store.release_integration_lease(scope="repo:owner/repo:integration")
        os.environ["GWO_AGENT_ID"] = "coordinator-001"
        self.assertIn("holder", str(ctx.exception).lower())

    def test_release_then_reacquire(self) -> None:
        self.store.acquire_integration_lease(scope="repo:owner/repo:integration")
        os.environ["GWO_AGENT_ID"] = "coordinator-001"
        self.store.release_integration_lease(scope="repo:owner/repo:integration")
        lease = self.store.acquire_integration_lease(scope="repo:owner/repo:integration")
        self.assertEqual("coordinator-001", lease["holder_agent"])

    def test_failed_acquire_leaves_no_partial_state(self) -> None:
        self.store.acquire_integration_lease(scope="repo:owner/repo:integration")
        before = self.store.integration_lease_holder("repo:owner/repo:integration")
        with self.assertRaises(self.store_mod.StoreError):
            self.store.acquire_integration_lease(scope="repo:owner/repo:integration")
        after = self.store.integration_lease_holder("repo:owner/repo:integration")
        self.assertEqual(before, after)

    def test_transactional_rollback_on_failed_lease(self) -> None:
        """If a concurrent writer wins, this writer must not leave a partial lease row."""
        import sqlite3
        scope = "repo:owner/repo:integration"
        # Pre-create a released lease row so acquire's update path is exercised.
        self.store.acquire_integration_lease(scope=scope)
        self.store.release_integration_lease(scope=scope)
        slug = self.store_mod._repo_slug("owner/repo")
        db_path = str(self.fixture.home / slug / "state.db")
        raw = sqlite3.connect(db_path)
        raw.isolation_level = None
        try:
            raw.execute("BEGIN IMMEDIATE")
            raw.execute(
                "UPDATE leases SET holder_agent = 'winner', acquired_at = 0, released_at = NULL "
                "WHERE scope = ?",
                (scope,),
            )
            raw.execute("COMMIT")
            with self.assertRaises(self.store_mod.StoreError):
                self.store.acquire_integration_lease(scope=scope)
            rows = raw.execute(
                "SELECT holder_agent, released_at FROM leases WHERE scope = ?", (scope,)
            ).fetchall()
            self.assertEqual(1, len(rows))
            self.assertEqual("winner", rows[0][0])
            self.assertIsNone(rows[0][1])
        finally:
            raw.close()


class LeaseSerialChainTests(unittest.TestCase):
    """Red tests for serial integration chain enforcement."""

    def setUp(self) -> None:
        os.environ["PYTHONPATH"] = str(SCRIPT_DIR) + os.pathsep + os.environ.get("PYTHONPATH", "")
        self.fixture = StoreFixture(self)
        self.store = self.fixture.store
        self.store_mod = self.fixture.store_mod

    def tearDown(self) -> None:
        self.fixture.cleanup()

    def test_store_has_append_integration_chain(self) -> None:
        self.assertTrue(hasattr(self.store, "append_integration_chain"))

    def test_store_has_list_integration_chain(self) -> None:
        self.assertTrue(hasattr(self.store, "list_integration_chain"))

    def test_append_integration_chain_requires_active_lease(self) -> None:
        with self.assertRaises(self.store_mod.IdentityError) as ctx:
            self.store.append_integration_chain(
                scope="repo:owner/repo:integration", candidate_sha="a" * 40, task_id="t-24"
            )
        self.assertIn("lease", str(ctx.exception).lower())

    def test_chain_appends_serially(self) -> None:
        self.store.acquire_integration_lease(scope="repo:owner/repo:integration")
        first = self.store.append_integration_chain(
            scope="repo:owner/repo:integration", candidate_sha="a" * 40, task_id="t-24"
        )
        second = self.store.append_integration_chain(
            scope="repo:owner/repo:integration", candidate_sha="b" * 40, task_id="t-25"
        )
        chain = self.store.list_integration_chain(scope="repo:owner/repo:integration")
        self.assertEqual(["a" * 40, "b" * 40], [c["candidate_sha"] for c in chain])
        self.assertEqual(first["chain_id"], second["prior_chain_id"])


class LeaseCliTests(unittest.TestCase):
    """Red CLI tests for lease commands."""

    def setUp(self) -> None:
        self.fixture = CliFixture(claim=True)

    def tearDown(self) -> None:
        self.fixture.cleanup()

    def test_cli_has_lease_subcommand(self) -> None:
        help_result = self.fixture.run("--help")
        self.assertIn("lease", help_result.stdout)

    def test_lease_acquire_serializes(self) -> None:
        os.environ["GWO_AGENT_ID"] = "integrator-001"
        result = self.fixture.run(
            "lease", "acquire",
            "--scope", "repo:owner/repo:integration",
        )
        self.assertEqual(0, result.returncode, result.stderr)
        import json
        payload = json.loads(result.stdout)
        self.assertEqual("repo:owner/repo:integration", payload["scope"])
        self.assertEqual("integrator-001", payload["holder_agent"])

    def test_lease_second_acquire_rejected(self) -> None:
        os.environ["GWO_AGENT_ID"] = "integrator-001"
        self.fixture.run(
            "lease", "acquire",
            "--scope", "repo:owner/repo:integration",
        )
        os.environ["GWO_AGENT_ID"] = "integrator-002"
        result = self.fixture.run(
            "lease", "acquire",
            "--scope", "repo:owner/repo:integration",
        )
        os.environ["GWO_AGENT_ID"] = "coordinator-001"
        self.assertNotEqual(0, result.returncode)
        self.assertIn("integrator-001", result.stderr)

    def test_lease_release_by_non_holder_rejected(self) -> None:
        os.environ["GWO_AGENT_ID"] = "integrator-001"
        self.fixture.run(
            "lease", "acquire",
            "--scope", "repo:owner/repo:integration",
        )
        os.environ["GWO_AGENT_ID"] = "integrator-002"
        result = self.fixture.run(
            "lease", "release",
            "--scope", "repo:owner/repo:integration",
        )
        os.environ["GWO_AGENT_ID"] = "coordinator-001"
        self.assertNotEqual(0, result.returncode)
        self.assertIn("holder", result.stderr.lower())


if __name__ == "__main__":
    unittest.main()
