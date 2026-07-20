from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


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


class StoreFixture:
    """Open a store against an isolated temporary GWO_HOME."""

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
        self.repo = repo
        self.store = self.store_mod.Store.connect(
            self.home, repo, caller_agent_id="coordinator-001"
        )

    def cleanup(self) -> None:
        self.store.close()
        self.tmp.cleanup()
        for key, value in self._saved_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


class SchemaTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = StoreFixture(self)
        self.store = self.fixture.store
        self.store_mod = self.fixture.store_mod

    def tearDown(self) -> None:
        self.fixture.cleanup()

    def test_store_creates_database_under_gwo_home(self) -> None:
        db_path = self.fixture.home / "owner-repo" / "state.db"
        self.assertTrue(db_path.is_file(), f"expected database at {db_path}")

    def test_store_runs_in_wal_mode(self) -> None:
        self.assertEqual("wal", self.store.journal_mode())

    def test_initialization_creates_all_required_tables(self) -> None:
        tables = self.store.table_names()
        expected = {
            "coordinator",
            "agents",
            "tasks",
            "dispatches",
            "messages",
            "review_rounds",
            "review_results",
            "leases",
            "schema_migrations",
        }
        self.assertTrue(expected.issubset(set(tables)), f"missing: {expected - set(tables)}")

    def test_reopen_preserves_state_and_reuses_wal_mode(self) -> None:
        self.store_mod = load_store()
        self.store.claim_coordinator()
        self.store.close()
        reopened = self.store_mod.Store.connect(
            self.fixture.home, self.fixture.repo, caller_agent_id="coordinator-001"
        )
        try:
            self.assertEqual("wal", reopened.journal_mode())
            holder = reopened.coordinator_holder()
            self.assertEqual("coordinator-001", holder)
        finally:
            reopened.close()

    def test_migration_is_idempotent(self) -> None:
        self.store.run_migrations()
        self.store.run_migrations()
        rows = self.store.db.execute(
            "SELECT name FROM schema_migrations ORDER BY name"
        ).fetchall()
        self.assertGreater(len(rows), 0)


class IdentityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = StoreFixture(self, repo="owner/repo")
        self.store = self.fixture.store
        self.store_mod = self.fixture.store_mod

    def tearDown(self) -> None:
        self.fixture.cleanup()

    def test_store_requires_caller_agent_id(self) -> None:
        with self.assertRaises(self.store_mod.IdentityError):
            self.store_mod.Store.connect(
                self.fixture.home, self.fixture.repo, caller_agent_id=""
            )

    def test_create_task_records_caller_identity_not_supplied(self) -> None:
        task = self.store.create_task(issue=42, group_label="g-42", risk="standard")
        self.assertEqual("coordinator-001", task["created_by"])
        row = self.store.db.execute(
            "SELECT created_by FROM tasks WHERE task_id = ?", (task["task_id"],)
        ).fetchone()
        self.assertEqual("coordinator-001", row["created_by"])

    def test_create_task_rejects_caller_supplied_identity(self) -> None:
        with self.assertRaises(self.store_mod.IdentityError):
            self.store.create_task(
                issue=43,
                group_label="g-43",
                risk="standard",
                created_by="attacker",
            )

    def test_dispatch_records_caller_identity_not_supplied(self) -> None:
        task = self.store.create_task(issue=42, group_label="g-42", risk="standard")
        self.store.update_task(task_id=task["task_id"], status="ready")
        dispatch = self.store.create_dispatch(
            task_id=task["task_id"],
            agent_id="worker-001",
            worktree="/tmp/wt-42",
            branch="work/issue-42",
        )
        self.assertEqual("coordinator-001", dispatch["dispatched_by"])

    def test_dispatch_rejects_caller_supplied_identity(self) -> None:
        task = self.store.create_task(issue=42, group_label="g-42", risk="standard")
        self.store.update_task(task_id=task["task_id"], status="ready")
        with self.assertRaises(self.store_mod.IdentityError):
            self.store.create_dispatch(
                task_id=task["task_id"],
                agent_id="worker-001",
                worktree="/tmp/wt-42",
                branch="work/issue-42",
                dispatched_by="attacker",
            )

    def test_done_records_caller_identity_from_agent_id(self) -> None:
        os.environ["GWO_AGENT_ID"] = "worker-001"
        self.store_mod = load_store()
        store = self.store_mod.Store.connect(
            self.fixture.home, self.fixture.repo, caller_agent_id="worker-001"
        )
        try:
            task = store.create_task(issue=42, group_label="g-42", risk="standard")
            store.update_task(task_id=task["task_id"], status="ready")
            dispatch = store.create_dispatch(
                task_id=task["task_id"],
                agent_id="worker-001",
                worktree="/tmp/wt-42",
                branch="work/issue-42",
            )
            store.mark_done(
                task_id=task["task_id"],
                dispatch_id=dispatch["dispatch_id"],
                status="done",
            )
        finally:
            store.close()
        os.environ["GWO_AGENT_ID"] = "coordinator-001"

    def test_done_rejects_caller_supplied_identity(self) -> None:
        os.environ["GWO_AGENT_ID"] = "worker-001"
        self.store_mod = load_store()
        store = self.store_mod.Store.connect(
            self.fixture.home, self.fixture.repo, caller_agent_id="worker-001"
        )
        try:
            task = store.create_task(issue=42, group_label="g-42", risk="standard")
            store.update_task(task_id=task["task_id"], status="ready")
            dispatch = store.create_dispatch(
                task_id=task["task_id"],
                agent_id="worker-001",
                worktree="/tmp/wt-42",
                branch="work/issue-42",
            )
            with self.assertRaises(self.store_mod.IdentityError):
                store.mark_done(
                    task_id=task["task_id"],
                    dispatch_id=dispatch["dispatch_id"],
                    status="done",
                    actor="attacker",
                )
        finally:
            store.close()
        os.environ["GWO_AGENT_ID"] = "coordinator-001"


class CoordinatorLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = StoreFixture(self, repo="owner/repo")
        self.store = self.fixture.store
        self.store_mod = self.fixture.store_mod

    def tearDown(self) -> None:
        self.fixture.cleanup()

    def test_claim_coordinator_succeeds_when_empty(self) -> None:
        self.store.claim_coordinator()
        self.assertEqual("coordinator-001", self.store.coordinator_holder())

    def test_second_claim_is_rejected_and_names_holder(self) -> None:
        self.store.claim_coordinator()
        with self.assertRaises(self.store_mod.CoordinatorBusy) as ctx:
            self.store.claim_coordinator()
        self.assertIn("coordinator-001", str(ctx.exception))

    def test_release_coordinator_clears_holder(self) -> None:
        self.store.claim_coordinator()
        self.store.release_coordinator()
        self.assertIsNone(self.store.coordinator_holder())

    def test_release_without_claim_is_rejected(self) -> None:
        with self.assertRaises(self.store_mod.TransitionError):
            self.store.release_coordinator()

    def test_release_by_non_holder_is_rejected(self) -> None:
        self.store.claim_coordinator()
        self.store_mod = load_store()
        other = self.store_mod.Store.connect(
            self.fixture.home, self.fixture.repo, caller_agent_id="other-002"
        )
        try:
            with self.assertRaises(self.store_mod.CoordinatorBusy):
                other.claim_coordinator()
            with self.assertRaises(self.store_mod.TransitionError):
                other.release_coordinator()
        finally:
            other.close()

    def test_release_then_reclaim_succeeds(self) -> None:
        self.store.claim_coordinator()
        self.store.release_coordinator()
        self.store.claim_coordinator()
        self.assertEqual("coordinator-001", self.store.coordinator_holder())


class TaskLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = StoreFixture(self, repo="owner/repo")
        self.store = self.fixture.store
        self.store_mod = self.fixture.store_mod

    def tearDown(self) -> None:
        self.fixture.cleanup()

    def test_create_task_returns_pending_status(self) -> None:
        task = self.store.create_task(issue=42, group_label="g-42", risk="standard")
        self.assertEqual("pending", task["status"])
        self.assertEqual(42, task["issue"])
        self.assertEqual("owner/repo", task["repo"])
        self.assertEqual("g-42", task["group_label"])
        self.assertEqual("standard", task["risk"])

    def test_list_tasks_returns_all_rows(self) -> None:
        self.store.create_task(issue=42, group_label="g-42", risk="standard")
        self.store.create_task(issue=43, group_label="g-43", risk="fast")
        tasks = self.store.list_tasks()
        self.assertEqual(2, len(tasks))

    def test_update_task_status_transitions_atomically(self) -> None:
        task = self.store.create_task(issue=42, group_label="g-42", risk="standard")
        updated = self.store.update_task(task_id=task["task_id"], status="ready")
        self.assertEqual("ready", updated["status"])

    def test_update_task_rejects_invalid_transition(self) -> None:
        task = self.store.create_task(issue=42, group_label="g-42", risk="standard")
        with self.assertRaises(self.store_mod.TransitionError):
            self.store.update_task(task_id=task["task_id"], status="done")

    def test_update_task_hotset_and_deps(self) -> None:
        task = self.store.create_task(issue=42, group_label="g-42", risk="standard")
        updated = self.store.update_task(
            task_id=task["task_id"],
            hotset=["src/auth/"],
            deps=["t-41"],
        )
        self.assertEqual(["src/auth/"], updated["hotset_json"])
        self.assertEqual(["t-41"], updated["deps_json"])


class DispatchLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = StoreFixture(self, repo="owner/repo")
        self.store = self.fixture.store
        self.store_mod = self.fixture.store_mod

    def tearDown(self) -> None:
        self.fixture.cleanup()

    def test_create_dispatch_marks_task_dispatched(self) -> None:
        task = self.store.create_task(issue=42, group_label="g-42", risk="standard")
        self.store.update_task(task_id=task["task_id"], status="ready")
        dispatch = self.store.create_dispatch(
            task_id=task["task_id"],
            agent_id="worker-001",
            worktree="/tmp/wt-42",
            branch="work/issue-42",
        )
        self.assertEqual("active", dispatch["status"])
        self.assertEqual(1, dispatch["attempt"])
        tasks = self.store.list_tasks()
        self.assertEqual("dispatched", tasks[0]["status"])

    def test_create_dispatch_rejects_non_ready_task(self) -> None:
        task = self.store.create_task(issue=42, group_label="g-42", risk="standard")
        with self.assertRaises(self.store_mod.TransitionError):
            self.store.create_dispatch(
                task_id=task["task_id"],
                agent_id="worker-001",
                worktree="/tmp/wt-42",
                branch="work/issue-42",
            )

    def test_create_second_dispatch_increments_attempt(self) -> None:
        task = self.store.create_task(issue=42, group_label="g-42", risk="standard")
        self.store.update_task(task_id=task["task_id"], status="ready")
        first = self.store.create_dispatch(
            task_id=task["task_id"],
            agent_id="worker-001",
            worktree="/tmp/wt-42",
            branch="work/issue-42",
        )
        os.environ["GWO_AGENT_ID"] = "worker-001"
        self.store.mark_done(
            task_id=task["task_id"],
            dispatch_id=first["dispatch_id"],
            status="blocked",
        )
        os.environ["GWO_AGENT_ID"] = "coordinator-001"
        self.store.update_task(task_id=task["task_id"], status="ready")
        second = self.store.create_dispatch(
            task_id=task["task_id"],
            agent_id="worker-002",
            worktree="/tmp/wt-42b",
            branch="work/issue-42",
        )
        self.assertEqual(2, second["attempt"])

    def test_done_transitions_task_to_done(self) -> None:
        task = self.store.create_task(issue=42, group_label="g-42", risk="standard")
        self.store.update_task(task_id=task["task_id"], status="ready")
        dispatch = self.store.create_dispatch(
            task_id=task["task_id"],
            agent_id="worker-001",
            worktree="/tmp/wt-42",
            branch="work/issue-42",
        )
        os.environ["GWO_AGENT_ID"] = "worker-001"
        self.store.mark_done(
            task_id=task["task_id"],
            dispatch_id=dispatch["dispatch_id"],
            status="done",
        )
        os.environ["GWO_AGENT_ID"] = "coordinator-001"
        tasks = self.store.list_tasks()
        self.assertEqual("done", tasks[0]["status"])

    def test_done_rejects_invalid_status(self) -> None:
        task = self.store.create_task(issue=42, group_label="g-42", risk="standard")
        self.store.update_task(task_id=task["task_id"], status="ready")
        dispatch = self.store.create_dispatch(
            task_id=task["task_id"],
            agent_id="worker-001",
            worktree="/tmp/wt-42",
            branch="work/issue-42",
        )
        os.environ["GWO_AGENT_ID"] = "worker-001"
        with self.assertRaises(self.store_mod.TransitionError):
            self.store.mark_done(
                task_id=task["task_id"],
                dispatch_id=dispatch["dispatch_id"],
                status="running",
            )
        os.environ["GWO_AGENT_ID"] = "coordinator-001"

    def test_done_rejects_wrong_dispatch(self) -> None:
        task = self.store.create_task(issue=42, group_label="g-42", risk="standard")
        self.store.update_task(task_id=task["task_id"], status="ready")
        dispatch = self.store.create_dispatch(
            task_id=task["task_id"],
            agent_id="worker-001",
            worktree="/tmp/wt-42",
            branch="work/issue-42",
        )
        os.environ["GWO_AGENT_ID"] = "worker-002"
        with self.assertRaises(self.store_mod.IdentityError):
            self.store.mark_done(
                task_id=task["task_id"],
                dispatch_id=dispatch["dispatch_id"],
                status="done",
            )
        os.environ["GWO_AGENT_ID"] = "coordinator-001"


class TransactionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = StoreFixture(self, repo="owner/repo")
        self.store = self.fixture.store
        self.store_mod = self.fixture.store_mod

    def tearDown(self) -> None:
        self.fixture.cleanup()

    def test_failed_transition_rolls_back_task_status(self) -> None:
        task = self.store.create_task(issue=42, group_label="g-42", risk="standard")
        self.store.update_task(task_id=task["task_id"], status="ready")
        with self.assertRaises(self.store_mod.TransitionError):
            self.store.update_task(task_id=task["task_id"], status="done")
        tasks = self.store.list_tasks()
        self.assertEqual("ready", tasks[0]["status"])

    def test_failed_dispatch_leaves_task_unchanged(self) -> None:
        task = self.store.create_task(issue=42, group_label="g-42", risk="standard")
        with self.assertRaises(self.store_mod.TransitionError):
            self.store.create_dispatch(
                task_id=task["task_id"],
                agent_id="worker-001",
                worktree="/tmp/wt-42",
                branch="work/issue-42",
            )
        tasks = self.store.list_tasks()
        self.assertEqual("pending", tasks[0]["status"])

    def test_done_failure_leaves_task_dispatched(self) -> None:
        task = self.store.create_task(issue=42, group_label="g-42", risk="standard")
        self.store.update_task(task_id=task["task_id"], status="ready")
        dispatch = self.store.create_dispatch(
            task_id=task["task_id"],
            agent_id="worker-001",
            worktree="/tmp/wt-42",
            branch="work/issue-42",
        )
        os.environ["GWO_AGENT_ID"] = "worker-002"
        with self.assertRaises(self.store_mod.IdentityError):
            self.store.mark_done(
                task_id=task["task_id"],
                dispatch_id=dispatch["dispatch_id"],
                status="done",
            )
        os.environ["GWO_AGENT_ID"] = "coordinator-001"
        tasks = self.store.list_tasks()
        self.assertEqual("dispatched", tasks[0]["status"])

    def test_concurrent_reader_sees_committed_state_under_wal(self) -> None:
        task = self.store.create_task(issue=42, group_label="g-42", risk="standard")
        self.store.update_task(task_id=task["task_id"], status="ready")
        reader = self.store_mod.Store.connect(
            self.fixture.home, self.fixture.repo, caller_agent_id="reader-003"
        )
        try:
            reader_tasks = reader.list_tasks()
            self.assertEqual("ready", reader_tasks[0]["status"])
        finally:
            reader.close()

    def test_two_writers_serialize_without_partial_state(self) -> None:
        task = self.store.create_task(issue=42, group_label="g-42", risk="standard")
        self.store.update_task(task_id=task["task_id"], status="ready")
        self.store.claim_coordinator()
        other = self.store_mod.Store.connect(
            self.fixture.home, self.fixture.repo, caller_agent_id="other-002"
        )
        try:
            with self.assertRaises(self.store_mod.CoordinatorBusy):
                other.claim_coordinator()
            tasks = self.store.list_tasks()
            self.assertEqual("ready", tasks[0]["status"])
        finally:
            other.close()


if __name__ == "__main__":
    unittest.main()