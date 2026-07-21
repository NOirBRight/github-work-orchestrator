from __future__ import annotations

import importlib.util
import os
import sqlite3
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
        slug = self.store_mod._repo_slug("owner/repo")
        db_path = self.fixture.home / slug / "state.db"
        self.assertTrue(db_path.is_file(), f"expected database at {db_path}")
        self.assertTrue(
            db_path.resolve().is_relative_to(self.fixture.home.resolve()),
            "database must stay inside GWO_HOME",
        )

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
        saved = os.environ.pop("GWO_AGENT_ID")
        try:
            with self.assertRaises(self.store_mod.IdentityError):
                self.store_mod.Store.connect(
                    self.fixture.home, self.fixture.repo
                )
        finally:
            os.environ["GWO_AGENT_ID"] = saved

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
        os.environ["GWO_AGENT_ID"] = "other-002"
        other = self.store_mod.Store.connect(
            self.fixture.home, self.fixture.repo
        )
        try:
            with self.assertRaises(self.store_mod.CoordinatorBusy):
                other.claim_coordinator()
            with self.assertRaises(self.store_mod.TransitionError):
                other.release_coordinator()
        finally:
            other.close()
            os.environ["GWO_AGENT_ID"] = "coordinator-001"

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
        os.environ["GWO_AGENT_ID"] = "reader-003"
        reader = self.store_mod.Store.connect(
            self.fixture.home, self.fixture.repo
        )
        try:
            reader_tasks = reader.list_tasks()
            self.assertEqual("ready", reader_tasks[0]["status"])
        finally:
            reader.close()
            os.environ["GWO_AGENT_ID"] = "coordinator-001"

    def test_two_writers_serialize_without_partial_state(self) -> None:
        task = self.store.create_task(issue=42, group_label="g-42", risk="standard")
        self.store.update_task(task_id=task["task_id"], status="ready")
        self.store.claim_coordinator()
        os.environ["GWO_AGENT_ID"] = "other-002"
        other = self.store_mod.Store.connect(
            self.fixture.home, self.fixture.repo
        )
        try:
            with self.assertRaises(self.store_mod.CoordinatorBusy):
                other.claim_coordinator()
            tasks = self.store.list_tasks()
            self.assertEqual("ready", tasks[0]["status"])
        finally:
            other.close()
            os.environ["GWO_AGENT_ID"] = "coordinator-001"


class ConcurrentDispatchRaceTests(unittest.TestCase):
    """Finding 1: dispatch validation must be inside an explicit write
    transaction so two concurrent writers cannot both dispatch one ready task.
    """

    def setUp(self) -> None:
        self.fixture = StoreFixture(self, repo="owner/repo")
        self.store = self.fixture.store
        self.store_mod = self.fixture.store_mod

    def tearDown(self) -> None:
        self.fixture.cleanup()

    def test_two_concurrent_dispatches_for_one_ready_task_serializes(self) -> None:
        task = self.store.create_task(issue=42, group_label="g-42", risk="standard")
        self.store.update_task(task_id=task["task_id"], status="ready")
        other = self.store_mod.Store.connect(
            self.fixture.home, self.fixture.repo, caller_agent_id="coordinator-001"
        )
        try:
            first = self.store.create_dispatch(
                task_id=task["task_id"],
                agent_id="worker-001",
                worktree="/tmp/wt-42",
                branch="work/issue-42",
            )
            with self.assertRaises(self.store_mod.TransitionError):
                other.create_dispatch(
                    task_id=task["task_id"],
                    agent_id="worker-002",
                    worktree="/tmp/wt-42b",
                    branch="work/issue-42",
                )
            active = self.store.db.execute(
                "SELECT COUNT(*) FROM dispatches WHERE task_id = ? AND status = 'active'",
                (task["task_id"],),
            ).fetchone()[0]
            self.assertEqual(1, int(active), "only one active dispatch may exist")
            self.assertEqual("dispatched", self.store.list_tasks()[0]["status"])
            self.assertEqual("active", first["status"])
        finally:
            other.close()

    def test_interleaved_read_then_write_cannot_double_dispatch(self) -> None:
        """Reproduce the race: both writers read 'ready' before either writes.

        Writer A opens a write transaction (BEGIN IMMEDIATE) and reads the
        task as ready but has not yet committed. Writer B then reads the task
        status (autocommit SELECT in WAL sees the pre-A snapshot = ready) and
        calls create_dispatch. B's validation must not insert a second active
        dispatch once A commits, even though B observed 'ready'.
        """
        task = self.store.create_task(issue=42, group_label="g-42", risk="standard")
        self.store.update_task(task_id=task["task_id"], status="ready")
        slug = self.store_mod._repo_slug("owner/repo")
        a_raw = sqlite3.connect(
            str(self.fixture.home / slug / "state.db")
        )
        a_raw.row_factory = sqlite3.Row
        a_raw.isolation_level = None
        other = self.store_mod.Store.connect(
            self.fixture.home, self.fixture.repo, caller_agent_id="coordinator-001"
        )
        try:
            a_raw.execute("BEGIN IMMEDIATE")
            a_status = a_raw.execute(
                "SELECT status FROM tasks WHERE task_id = ?", (task["task_id"],)
            ).fetchone()
            self.assertEqual("ready", a_status["status"])
            b_snapshot = other.db.execute(
                "SELECT status FROM tasks WHERE task_id = ?", (task["task_id"],)
            ).fetchone()
            self.assertEqual(
                "ready", b_snapshot["status"], "B must observe ready before A commits"
            )
            a_raw.execute(
                "INSERT INTO dispatches (dispatch_id, task_id, agent_id, attempt, "
                "worktree, branch, status, dispatched_by, created_at) "
                "VALUES ('d-a', ?, 'w1', 1, '/x', 'b', 'active', 'coordinator-001', 0)",
                (task["task_id"],),
            )
            a_raw.execute(
                "UPDATE tasks SET status = 'dispatched' WHERE task_id = ?",
                (task["task_id"],),
            )
            a_raw.execute("COMMIT")
            with self.assertRaises(self.store_mod.TransitionError):
                other.create_dispatch(
                    task_id=task["task_id"],
                    agent_id="worker-002",
                    worktree="/tmp/wt-42b",
                    branch="work/issue-42",
                )
            active = int(self.store.db.execute(
                "SELECT COUNT(*) FROM dispatches WHERE task_id = ? AND status = 'active'",
                (task["task_id"],),
            ).fetchone()[0])
            self.assertEqual(1, active, "only one active dispatch may survive the race")
        finally:
            a_raw.close()
            other.close()

    def test_repeated_concurrent_dispatch_keeps_one_active(self) -> None:
        task = self.store.create_task(issue=42, group_label="g-42", risk="standard")
        self.store.update_task(task_id=task["task_id"], status="ready")
        other = self.store_mod.Store.connect(
            self.fixture.home, self.fixture.repo, caller_agent_id="coordinator-001"
        )
        try:
            self.store.create_dispatch(
                task_id=task["task_id"],
                agent_id="worker-001",
                worktree="/tmp/wt-42",
                branch="work/issue-42",
            )
            for _ in range(3):
                with self.assertRaises(self.store_mod.TransitionError):
                    other.create_dispatch(
                        task_id=task["task_id"],
                        agent_id="worker-002",
                        worktree="/tmp/wt-42b",
                        branch="work/issue-42",
                    )
            active = int(self.store.db.execute(
                "SELECT COUNT(*) FROM dispatches WHERE task_id = ? AND status = 'active'",
                (task["task_id"],),
            ).fetchone()[0])
            self.assertEqual(1, active)
        finally:
            other.close()


class DoneMismatchTests(unittest.TestCase):
    """Finding 2: mark_done must require the dispatch to belong to the supplied
    task, validate the task transition atomically, and reject statuses the task
    lifecycle cannot hold (stopped is absent from TASK_STATUSES).
    """

    def setUp(self) -> None:
        self.fixture = StoreFixture(self, repo="owner/repo")
        self.store = self.fixture.store
        self.store_mod = self.fixture.store_mod

    def tearDown(self) -> None:
        self.fixture.cleanup()

    def test_done_rejects_dispatch_owned_by_another_task(self) -> None:
        task_a = self.store.create_task(issue=42, group_label="g-42", risk="standard")
        self.store.update_task(task_id=task_a["task_id"], status="ready")
        task_b = self.store.create_task(issue=43, group_label="g-43", risk="standard")
        self.store.update_task(task_id=task_b["task_id"], status="ready")
        dispatch_a = self.store.create_dispatch(
            task_id=task_a["task_id"],
            agent_id="worker-001",
            worktree="/tmp/wt-42",
            branch="work/issue-42",
        )
        os.environ["GWO_AGENT_ID"] = "worker-001"
        with self.assertRaises(self.store_mod.TransitionError):
            self.store.mark_done(
                task_id=task_b["task_id"],
                dispatch_id=dispatch_a["dispatch_id"],
                status="done",
            )
        os.environ["GWO_AGENT_ID"] = "coordinator-001"
        tasks = {t["task_id"]: t for t in self.store.list_tasks()}
        self.assertEqual("dispatched", tasks[task_a["task_id"]]["status"])
        self.assertEqual("ready", tasks[task_b["task_id"]]["status"])

    def test_done_task_status_matches_lifecycle_enum(self) -> None:
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
        self.assertIn(tasks[0]["status"], set(self.store_mod.TASK_STATUSES))

    def test_done_blocked_transitions_task_to_blocked(self) -> None:
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
            status="blocked",
        )
        os.environ["GWO_AGENT_ID"] = "coordinator-001"
        self.assertEqual("blocked", self.store.list_tasks()[0]["status"])

    def test_done_stopped_does_not_store_invalid_task_status(self) -> None:
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
            status="stopped",
        )
        os.environ["GWO_AGENT_ID"] = "coordinator-001"
        tasks = self.store.list_tasks()
        self.assertIn(
            tasks[0]["status"], set(self.store_mod.TASK_STATUSES),
            "task status must remain a member of TASK_STATUSES after stopped",
        )

    def test_done_stopped_dispatch_status_allowed(self) -> None:
        task = self.store.create_task(issue=42, group_label="g-42", risk="standard")
        self.store.update_task(task_id=task["task_id"], status="ready")
        dispatch = self.store.create_dispatch(
            task_id=task["task_id"],
            agent_id="worker-001",
            worktree="/tmp/wt-42",
            branch="work/issue-42",
        )
        os.environ["GWO_AGENT_ID"] = "worker-001"
        result = self.store.mark_done(
            task_id=task["task_id"],
            dispatch_id=dispatch["dispatch_id"],
            status="stopped",
        )
        os.environ["GWO_AGENT_ID"] = "coordinator-001"
        self.assertEqual("stopped", result["status"])

    def test_done_rejects_task_not_dispatched(self) -> None:
        task_a = self.store.create_task(issue=42, group_label="g-42", risk="standard")
        self.store.update_task(task_id=task_a["task_id"], status="ready")
        task_b = self.store.create_task(issue=43, group_label="g-43", risk="standard")
        dispatch_a = self.store.create_dispatch(
            task_id=task_a["task_id"],
            agent_id="worker-001",
            worktree="/tmp/wt-42",
            branch="work/issue-42",
        )
        os.environ["GWO_AGENT_ID"] = "worker-001"
        with self.assertRaises(self.store_mod.TransitionError):
            self.store.mark_done(
                task_id=task_b["task_id"],
                dispatch_id=dispatch_a["dispatch_id"],
                status="done",
            )
        os.environ["GWO_AGENT_ID"] = "coordinator-001"


class IdentityOverrideTests(unittest.TestCase):
    """Finding 3: caller_agent_id must not forge authority, and writes must fail
    when GWO_AGENT_ID is removed even if connect() was called with an override.
    """

    def setUp(self) -> None:
        self.fixture = StoreFixture(self, repo="owner/repo")
        self.store = self.fixture.store
        self.store_mod = self.fixture.store_mod

    def tearDown(self) -> None:
        self.fixture.cleanup()

    def test_connect_override_cannot_forge_task_author(self) -> None:
        os.environ["GWO_AGENT_ID"] = "worker-001"
        self.store_mod = load_store()
        forged = self.store_mod.Store.connect(
            self.fixture.home, self.fixture.repo, caller_agent_id="attacker"
        )
        try:
            task = forged.create_task(issue=42, group_label="g-42", risk="standard")
            self.assertEqual("worker-001", task["created_by"])
        finally:
            forged.close()
        os.environ["GWO_AGENT_ID"] = "coordinator-001"

    def test_connect_override_cannot_forge_dispatch_author(self) -> None:
        os.environ["GWO_AGENT_ID"] = "coordinator-001"
        self.store_mod = load_store()
        forged = self.store_mod.Store.connect(
            self.fixture.home, self.fixture.repo, caller_agent_id="attacker"
        )
        try:
            task = forged.create_task(issue=42, group_label="g-42", risk="standard")
            forged.update_task(task_id=task["task_id"], status="ready")
            dispatch = forged.create_dispatch(
                task_id=task["task_id"],
                agent_id="worker-001",
                worktree="/tmp/wt-42",
                branch="work/issue-42",
            )
            self.assertEqual("coordinator-001", dispatch["dispatched_by"])
        finally:
            forged.close()

    def test_write_fails_after_gwo_agent_id_removed(self) -> None:
        saved = os.environ.pop("GWO_AGENT_ID")
        try:
            with self.assertRaises(self.store_mod.IdentityError):
                self.store.create_task(issue=42, group_label="g-42", risk="standard")
        finally:
            os.environ["GWO_AGENT_ID"] = saved

    def test_claim_fails_after_gwo_agent_id_removed(self) -> None:
        saved = os.environ.pop("GWO_AGENT_ID")
        try:
            with self.assertRaises(self.store_mod.IdentityError):
                self.store.claim_coordinator()
        finally:
            os.environ["GWO_AGENT_ID"] = saved

    def test_release_fails_after_gwo_agent_id_removed(self) -> None:
        self.store.claim_coordinator()
        saved = os.environ.pop("GWO_AGENT_ID")
        try:
            with self.assertRaises(self.store_mod.IdentityError):
                self.store.release_coordinator()
        finally:
            os.environ["GWO_AGENT_ID"] = saved
        self.assertEqual("coordinator-001", self.store.coordinator_holder())

    def test_update_task_fails_after_gwo_agent_id_removed(self) -> None:
        task = self.store.create_task(issue=42, group_label="g-42", risk="standard")
        saved = os.environ.pop("GWO_AGENT_ID")
        try:
            with self.assertRaises(self.store_mod.IdentityError):
                self.store.update_task(task_id=task["task_id"], status="ready")
        finally:
            os.environ["GWO_AGENT_ID"] = saved
        self.assertEqual("pending", self.store.list_tasks()[0]["status"])

    def test_mark_done_fails_after_gwo_agent_id_removed(self) -> None:
        task = self.store.create_task(issue=42, group_label="g-42", risk="standard")
        self.store.update_task(task_id=task["task_id"], status="ready")
        dispatch = self.store.create_dispatch(
            task_id=task["task_id"],
            agent_id="worker-001",
            worktree="/tmp/wt-42",
            branch="work/issue-42",
        )
        os.environ["GWO_AGENT_ID"] = "worker-001"
        saved = os.environ.pop("GWO_AGENT_ID")
        try:
            with self.assertRaises(self.store_mod.IdentityError):
                self.store.mark_done(
                    task_id=task["task_id"],
                    dispatch_id=dispatch["dispatch_id"],
                    status="done",
                )
        finally:
            os.environ["GWO_AGENT_ID"] = saved
        os.environ["GWO_AGENT_ID"] = "coordinator-001"
        self.assertEqual("dispatched", self.store.list_tasks()[0]["status"])


class WindowsPathTests(unittest.TestCase):
    """Finding 4: reject Windows separators and enforce resolved-path
    containment so a repository string cannot traverse outside GWO_HOME.
    """

    def setUp(self) -> None:
        self.fixture = StoreFixture(self, repo="owner/repo")
        self.store = self.fixture.store
        self.store_mod = self.fixture.store_mod

    def tearDown(self) -> None:
        self.fixture.cleanup()

    def test_backslash_repository_is_rejected(self) -> None:
        with self.assertRaises(self.store_mod.IdentityError):
            self.store_mod.Store.connect(
                self.fixture.home, "owner\\..\\evil", caller_agent_id="coordinator-001"
            )

    def test_traversal_repository_is_rejected(self) -> None:
        with self.assertRaises(self.store_mod.IdentityError):
            self.store_mod.Store.connect(
                self.fixture.home, "owner/..\\evil", caller_agent_id="coordinator-001"
            )

    def test_database_stays_inside_gwo_home(self) -> None:
        slug = self.store_mod._repo_slug("owner/repo")
        db_path = self.fixture.home / slug / "state.db"
        self.assertTrue(db_path.is_file())
        resolved = db_path.resolve()
        self.assertTrue(
            resolved.is_relative_to(self.fixture.home.resolve()),
            f"{resolved} must stay inside {self.fixture.home}",
        )


class UpdateTaskDispatchBypassTests(unittest.TestCase):
    """Finding 1: update_task must not permit dispatched -> done|failed|blocked
    transitions while an active dispatch exists, because that bypasses the
    dispatched-agent check in done and can leave a terminal task with an
    active dispatch. Only the done path (or an explicitly authorized
    coordinator override that updates the linked dispatch in the same
    transaction) may move a dispatched task to a terminal status.
    """

    def setUp(self) -> None:
        self.fixture = StoreFixture(self, repo="owner/repo")
        self.store = self.fixture.store
        self.store_mod = self.fixture.store_mod

    def tearDown(self) -> None:
        self.fixture.cleanup()

    def test_update_task_rejects_dispatched_to_done_with_active_dispatch(self) -> None:
        task = self.store.create_task(issue=42, group_label="g-42", risk="standard")
        self.store.update_task(task_id=task["task_id"], status="ready")
        self.store.create_dispatch(
            task_id=task["task_id"],
            agent_id="worker-001",
            worktree="/tmp/wt-42",
            branch="work/issue-42",
        )
        with self.assertRaises(self.store_mod.TransitionError):
            self.store.update_task(task_id=task["task_id"], status="done")
        tasks = self.store.list_tasks()
        self.assertEqual("dispatched", tasks[0]["status"])
        active = int(self.store.db.execute(
            "SELECT COUNT(*) FROM dispatches WHERE task_id = ? AND status = 'active'",
            (task["task_id"],),
        ).fetchone()[0])
        self.assertEqual(1, active, "active dispatch must remain after rejection")

    def test_update_task_rejects_dispatched_to_failed_with_active_dispatch(self) -> None:
        task = self.store.create_task(issue=42, group_label="g-42", risk="standard")
        self.store.update_task(task_id=task["task_id"], status="ready")
        self.store.create_dispatch(
            task_id=task["task_id"],
            agent_id="worker-001",
            worktree="/tmp/wt-42",
            branch="work/issue-42",
        )
        with self.assertRaises(self.store_mod.TransitionError):
            self.store.update_task(task_id=task["task_id"], status="failed")
        self.assertEqual("dispatched", self.store.list_tasks()[0]["status"])

    def test_update_task_rejects_dispatched_to_blocked_with_active_dispatch(self) -> None:
        task = self.store.create_task(issue=42, group_label="g-42", risk="standard")
        self.store.update_task(task_id=task["task_id"], status="ready")
        self.store.create_dispatch(
            task_id=task["task_id"],
            agent_id="worker-001",
            worktree="/tmp/wt-42",
            branch="work/issue-42",
        )
        with self.assertRaises(self.store_mod.TransitionError):
            self.store.update_task(task_id=task["task_id"], status="blocked")
        self.assertEqual("dispatched", self.store.list_tasks()[0]["status"])

    def test_update_task_rejects_dispatched_to_ready_with_active_dispatch(self) -> None:
        task = self.store.create_task(issue=42, group_label="g-42", risk="standard")
        self.store.update_task(task_id=task["task_id"], status="ready")
        self.store.create_dispatch(
            task_id=task["task_id"],
            agent_id="worker-001",
            worktree="/tmp/wt-42",
            branch="work/issue-42",
        )
        with self.assertRaises(self.store_mod.TransitionError):
            self.store.update_task(task_id=task["task_id"], status="ready")
        self.assertEqual("dispatched", self.store.list_tasks()[0]["status"])

    def test_update_task_allows_dispatched_to_ready_after_dispatch_closed(self) -> None:
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
            status="blocked",
        )
        os.environ["GWO_AGENT_ID"] = "coordinator-001"
        updated = self.store.update_task(task_id=task["task_id"], status="ready")
        self.assertEqual("ready", updated["status"])


class MigrationAtomicityTests(unittest.TestCase):
    """Finding 2: each migration and its schema_migrations record must commit in
    one transaction. A multi-statement migration whose later statement fails must
    roll back the whole migration so no DDL is left without a record.
    """

    def setUp(self) -> None:
        self.fixture = StoreFixture(self, repo="owner/repo")
        self.store = self.fixture.store
        self.store_mod = self.fixture.store_mod

    def tearDown(self) -> None:
        self.fixture.cleanup()

    def test_failing_second_statement_rolls_back_whole_migration(self) -> None:
        store_mod = self.store_mod
        original_migrations = store_mod.MIGRATIONS
        broken_ddl = (
            "CREATE TABLE IF NOT EXISTS migration_probe (id INTEGER PRIMARY KEY); "
            "CREATE TABLE migration_probe_dup (id INTEGER PRIMARY KEY); "
            "CREATE TABLE migration_probe_dup (id INTEGER PRIMARY KEY);"
        )
        store_mod.MIGRATIONS = original_migrations + (
            ("9999-broken", broken_ddl),
        )
        try:
            with self.assertRaises(sqlite3.OperationalError):
                self.store.run_migrations()
            records = self.store.db.execute(
                "SELECT name FROM schema_migrations WHERE name = '9999-broken'"
            ).fetchall()
            self.assertEqual(0, len(records), "broken migration must not be recorded")
            tables = set(self.store.table_names())
            self.assertNotIn(
                "migration_probe",
                tables,
                "first DDL statement must roll back with the failing migration",
            )
        finally:
            store_mod.MIGRATIONS = original_migrations

    def test_reopen_after_failed_migration_applies_cleanly(self) -> None:
        store_mod = self.store_mod
        original_migrations = store_mod.MIGRATIONS
        broken_ddl = (
            "CREATE TABLE IF NOT EXISTS migration_probe2 (id INTEGER PRIMARY KEY); "
            "CREATE TABLE migration_probe2_dup (id INTEGER PRIMARY KEY); "
            "CREATE TABLE migration_probe2_dup (id INTEGER PRIMARY KEY);"
        )
        good_ddl = "CREATE TABLE IF NOT EXISTS migration_probe3 (id INTEGER PRIMARY KEY);"
        store_mod.MIGRATIONS = original_migrations + (
            ("9998-broken", broken_ddl),
            ("9997-good", good_ddl),
        )
        try:
            with self.assertRaises(sqlite3.OperationalError):
                self.store.run_migrations()
            self.store.close()
            store_mod.MIGRATIONS = original_migrations + (
                ("9997-good", good_ddl),
            )
            reopened = store_mod.Store.connect(
                self.fixture.home, self.fixture.repo, caller_agent_id="coordinator-001"
            )
            try:
                records = {
                    str(row[0])
                    for row in reopened.db.execute(
                        "SELECT name FROM schema_migrations"
                    ).fetchall()
                }
                self.assertNotIn("9998-broken", records)
                self.assertIn("9997-good", records)
                self.assertIn("migration_probe3", set(reopened.table_names()))
                self.assertNotIn(
                    "migration_probe2", set(reopened.table_names()),
                    "rolled-back DDL must not survive reopen",
                )
            finally:
                reopened.close()
        finally:
            store_mod.MIGRATIONS = original_migrations
            try:
                self.store_mod = load_store()
            except Exception:
                pass


class MigrationIdentityTests(unittest.TestCase):
    """Finding 3: run_migrations is a public store write and must re-resolve
    GWO_AGENT_ID at migration write time. It must fail when GWO_AGENT_ID is
    removed after connect, and it is part of the every-write identity matrix.
    """

    def setUp(self) -> None:
        self.fixture = StoreFixture(self, repo="owner/repo")
        self.store = self.fixture.store
        self.store_mod = self.fixture.store_mod

    def tearDown(self) -> None:
        self.fixture.cleanup()

    def test_run_migrations_fails_after_gwo_agent_id_removed(self) -> None:
        saved = os.environ.pop("GWO_AGENT_ID")
        try:
            with self.assertRaises(self.store_mod.IdentityError):
                self.store.run_migrations()
        finally:
            os.environ["GWO_AGENT_ID"] = saved

    def test_connect_run_migrations_fails_without_gwo_agent_id(self) -> None:
        store_mod = self.store_mod
        original_migrations = store_mod.MIGRATIONS
        store_mod.MIGRATIONS = original_migrations + (
            ("9996-probe", "CREATE TABLE IF NOT EXISTS migration_probe4 (id INTEGER PRIMARY KEY);"),
        )
        try:
            saved = os.environ.pop("GWO_AGENT_ID")
            try:
                with self.assertRaises(store_mod.IdentityError):
                    store_mod.Store.connect(self.fixture.home, self.fixture.repo)
            finally:
                os.environ["GWO_AGENT_ID"] = saved
        finally:
            store_mod.MIGRATIONS = original_migrations


if __name__ == "__main__":
    unittest.main()