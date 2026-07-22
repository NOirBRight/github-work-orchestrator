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

    def __init__(
        self,
        test_case: unittest.TestCase,
        *,
        repo: str = "owner/repo",
        claim: bool = True,
    ):
        self.test_case = test_case
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self._saved_env = {
            "GWO_HOME": os.environ.get("GWO_HOME"),
            "GWO_AGENT_ID": os.environ.get("GWO_AGENT_ID"),
        }
        self._saved_path = list(sys.path)
        os.environ["GWO_HOME"] = str(self.home)
        os.environ["GWO_AGENT_ID"] = "coordinator-001"
        if str(SCRIPT_DIR) not in sys.path:
            sys.path.insert(0, str(SCRIPT_DIR))
        self.store_mod = load_store()
        self.repo = repo
        self.store = self.store_mod.Store.connect(
            self.home, repo, caller_agent_id="coordinator-001"
        )
        if claim:
            self.store.claim_coordinator()

    def cleanup(self) -> None:
        self.store.close()
        self.tmp.cleanup()
        sys.path[:] = self._saved_path
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
        task = self.store.create_task(issue=42, group_label="g-42", risk="standard")
        self.store.update_task(task_id=task["task_id"], status="ready")
        dispatch = self.store.create_dispatch(
            task_id=task["task_id"],
            agent_id="worker-001",
            worktree="/tmp/wt-42",
            branch="work/issue-42",
        )
        os.environ["GWO_AGENT_ID"] = "worker-001"
        self.store_mod = load_store()
        store = self.store_mod.Store.connect(
            self.fixture.home, self.fixture.repo, caller_agent_id="worker-001"
        )
        try:
            store.mark_done(
                task_id=task["task_id"],
                dispatch_id=dispatch["dispatch_id"],
                status="done",
            )
        finally:
            store.close()
        os.environ["GWO_AGENT_ID"] = "coordinator-001"

    def test_done_rejects_caller_supplied_identity(self) -> None:
        task = self.store.create_task(issue=42, group_label="g-42", risk="standard")
        self.store.update_task(task_id=task["task_id"], status="ready")
        dispatch = self.store.create_dispatch(
            task_id=task["task_id"],
            agent_id="worker-001",
            worktree="/tmp/wt-42",
            branch="work/issue-42",
        )
        os.environ["GWO_AGENT_ID"] = "worker-001"
        self.store_mod = load_store()
        store = self.store_mod.Store.connect(
            self.fixture.home, self.fixture.repo, caller_agent_id="worker-001"
        )
        try:
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
        self.fixture = StoreFixture(self, repo="owner/repo", claim=False)
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
        os.environ["GWO_AGENT_ID"] = "coordinator-001"
        self.store_mod = load_store()
        forged = self.store_mod.Store.connect(
            self.fixture.home, self.fixture.repo, caller_agent_id="attacker"
        )
        try:
            task = forged.create_task(issue=42, group_label="g-42", risk="standard")
            self.assertEqual("coordinator-001", task["created_by"])
        finally:
            forged.close()

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


class MigrationSafetyTests(unittest.TestCase):
    """Finding 4: Phase 2 review/lease DDL must live in its own migration so a
    pre-Phase-2 store (0001-initial, 0002-messages-in-reply-to, and
    0003-tasks-repo-issue-unique) receives the new tables/indexes cleanly,
    while fresh stores apply all migrations in order. A failed 0004 rolls back
    with no partial schema record and leaves existing data intact.
    """

    def setUp(self) -> None:
        self.fixture = StoreFixture(self, repo="owner/repo")
        self.store = self.fixture.store
        self.store_mod = self.fixture.store_mod

    def tearDown(self) -> None:
        self.fixture.cleanup()

    def _pre_phase2_tables(self) -> set[str]:
        return {
            "coordinator", "agents", "tasks", "dispatches", "messages", "schema_migrations"
        }

    def _phase2_tables(self) -> set[str]:
        return {"review_rounds", "review_results", "leases", "integration_chain"}

    def _build_pre_phase2_store(self) -> None:
        """Manually create a store that has only the first three migrations.

        Uses the *fixture-created* store's state.db, wipes any tables/indexes,
        then applies only the first three migrations cleanly.
        """
        store_mod = self.store_mod
        # Close the fixture-created store so we can mutate the raw DB.
        self.store.close()
        slug = store_mod._repo_slug("owner/repo")
        db_path = self.fixture.home / slug / "state.db"
        raw = sqlite3.connect(str(db_path))
        try:
            raw.execute("PRAGMA foreign_keys=OFF")
            # Drop every user table/index so we can apply the first three migrations only.
            master = raw.execute(
                "SELECT type, name FROM sqlite_master WHERE type IN ('table', 'index') "
                "AND name NOT LIKE 'sqlite_%'"
            ).fetchall()
            for kind, name in master:
                raw.execute(f"DROP {kind} IF EXISTS {name}")
            # Re-create schema_migrations first so we can record applied migrations.
            raw.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations (name TEXT PRIMARY KEY)"
            )
            raw.execute("BEGIN IMMEDIATE")
            for name, ddl in store_mod.MIGRATIONS:
                if name in {"0001-initial", "0002-messages-in-reply-to", "0003-tasks-repo-issue-unique"}:
                    for statement in store_mod._split_sql_statements(ddl):
                        raw.execute(statement)
                    raw.execute(
                        "INSERT OR REPLACE INTO schema_migrations (name) VALUES (?)", (name,)
                    )
            raw.execute("COMMIT")
        finally:
            raw.close()

    def test_pre_phase2_store_lacks_review_and_lease_tables(self) -> None:
        self._build_pre_phase2_store()
        raw = sqlite3.connect(str(self.fixture.home / self.store_mod._repo_slug("owner/repo") / "state.db"))
        try:
            tables = {
                str(row[1])
                for row in raw.execute(
                    "SELECT type, name FROM sqlite_master WHERE type IN ('table', 'index')"
                ).fetchall()
            }
            self.assertTrue(
                self._pre_phase2_tables().issubset(tables),
                "pre-Phase-2 store must have the original tables"
            )
            self.assertFalse(
                self._phase2_tables() & tables,
                "pre-Phase-2 store must not have review/lease tables yet"
            )
            self.assertNotIn("idx_leases_active_scope", tables)
            self.assertNotIn("idx_integration_chain_scope", tables)
        finally:
            raw.close()

    def test_pre_phase2_store_applies_phase2_migration_on_connect(self) -> None:
        self._build_pre_phase2_store()
        reopened = self.store_mod.Store.connect(
            self.fixture.home, "owner/repo", caller_agent_id="coordinator-001"
        )
        try:
            tables = set(reopened.table_names())
            self.assertTrue(
                self._phase2_tables().issubset(tables),
                "Phase 2 tables must appear after connecting a pre-Phase-2 store"
            )
            migrations = {
                str(row[0])
                for row in reopened.db.execute("SELECT name FROM schema_migrations").fetchall()
            }
            self.assertIn("0004-review-rounds-and-lease", migrations)
            # Existing data path: a task inserted before the migration survives.
            reopened.claim_coordinator()
            reopened.create_task(issue=99, group_label="g-99", risk="standard")
            tasks = reopened.list_tasks()
            self.assertEqual(1, len(tasks))
            self.assertEqual(99, tasks[0]["issue"])
        finally:
            reopened.close()

    def test_fresh_store_applies_migrations_in_order(self) -> None:
        migrations = [
            str(row[0])
            for row in self.store.db.execute(
                "SELECT name FROM schema_migrations ORDER BY rowid"
            ).fetchall()
        ]
        self.assertEqual(
            [
                "0001-initial",
                "0002-messages-in-reply-to",
                "0003-tasks-repo-issue-unique",
                "0004-review-rounds-and-lease",
                "0005-review-authority-and-chain-integrity",
            ],
            migrations,
        )
        tables = set(self.store.table_names())
        self.assertTrue(
            self._phase2_tables().issubset(tables),
            "fresh store must contain Phase 2 tables"
        )
        self.assertIn("review_assignments", tables)

    def test_failed_phase2_migration_rolls_back_no_partial_record(self) -> None:
        store_mod = self.store_mod
        original_migrations = store_mod.MIGRATIONS
        # Start from a fully migrated store, then strip 0004 so we exercise
        # the real migration path and a deterministic failure.
        self.store.close()
        slug = store_mod._repo_slug("owner/repo")
        db_path = str(self.fixture.home / slug / "state.db")
        raw = sqlite3.connect(db_path)
        raw.isolation_level = None
        try:
            raw.execute('DELETE FROM schema_migrations WHERE name = "0004-review-rounds-and-lease"')
            for row in raw.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' "
                "AND name IN ('review_rounds','review_results','leases','integration_chain')"
            ).fetchall():
                raw.execute(f"DROP TABLE {row[0]}")
            raw.execute("DROP INDEX IF EXISTS idx_leases_active_scope")
            raw.execute("DROP INDEX IF EXISTS idx_integration_chain_scope")
            raw.commit()
        finally:
            raw.close()
        broken_migrations = original_migrations[:3] + (
            (
                "0004-review-rounds-and-lease",
                "CREATE TABLE IF NOT EXISTS review_rounds (id INTEGER PRIMARY KEY); "
                "CREATE TABLE IF NOT EXISTS review_results (id INTEGER PRIMARY KEY); "
                "CREATE TABLE IF NOT EXISTS leases (id INTEGER PRIMARY PRIMARY KEY); "
                "CREATE TABLE IF NOT EXISTS integration_chain (id INTEGER PRIMARY KEY);"
            ),
        )
        store_mod.MIGRATIONS = broken_migrations
        reopened = None
        try:
            with self.assertRaises(sqlite3.OperationalError):
                reopened = store_mod.Store.connect(
                    self.fixture.home, "owner/repo", caller_agent_id="coordinator-001"
                )
            raw = sqlite3.connect(db_path)
            try:
                records = {
                    str(row[0])
                    for row in raw.execute("SELECT name FROM schema_migrations").fetchall()
                }
                self.assertNotIn("0004-review-rounds-and-lease", records)
                tables = {str(row[1]) for row in raw.execute(
                    "SELECT type, name FROM sqlite_master WHERE type = 'table'"
                ).fetchall()}
                self.assertNotIn("review_rounds", tables)
                self.assertNotIn("review_results", tables)
                self.assertNotIn("leases", tables)
                self.assertNotIn("integration_chain", tables)
            finally:
                raw.close()
        finally:
            store_mod.MIGRATIONS = original_migrations
            if reopened is not None:
                reopened.close()


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


class CoordinatorClaimAuthorizationTests(unittest.TestCase):
    """Finding 1: create_task, update_task, and create_dispatch are
    Coordinator-owned operations. They must require the caller to hold the
    active repository coordinator claim, checked inside the same write
    transaction. A foreign worker (or an unclaimed repo) must not be able to
    create/mutate tasks or dispatch to an attacker-selected agent.
    """

    def setUp(self) -> None:
        self.fixture = StoreFixture(self, repo="owner/repo", claim=False)
        self.store = self.fixture.store
        self.store_mod = self.fixture.store_mod

    def tearDown(self) -> None:
        self.fixture.cleanup()

    def test_create_task_rejected_when_no_active_claim(self) -> None:
        with self.assertRaises(self.store_mod.IdentityError) as ctx:
            self.store.create_task(issue=42, group_label="g-42", risk="standard")
        self.assertIn("coordinator", str(ctx.exception).lower())
        self.assertEqual(0, len(self.store.list_tasks()))

    def test_create_task_rejected_for_foreign_caller_without_claim(self) -> None:
        self.store.claim_coordinator()
        os.environ["GWO_AGENT_ID"] = "worker-foreign"
        foreign = self.store_mod.Store.connect(
            self.fixture.home, self.fixture.repo
        )
        try:
            with self.assertRaises(self.store_mod.IdentityError) as ctx:
                foreign.create_task(issue=42, group_label="g-42", risk="standard")
            self.assertIn("coordinator", str(ctx.exception).lower())
        finally:
            foreign.close()
        os.environ["GWO_AGENT_ID"] = "coordinator-001"
        self.assertEqual(0, len(self.store.list_tasks()))

    def test_create_task_succeeds_for_active_claim_holder(self) -> None:
        self.store.claim_coordinator()
        task = self.store.create_task(issue=42, group_label="g-42", risk="standard")
        self.assertEqual("coordinator-001", task["created_by"])

    def test_update_task_rejected_when_no_active_claim(self) -> None:
        self.store.claim_coordinator()
        task = self.store.create_task(issue=42, group_label="g-42", risk="standard")
        self.store.release_coordinator()
        with self.assertRaises(self.store_mod.IdentityError) as ctx:
            self.store.update_task(task_id=task["task_id"], status="ready")
        self.assertIn("coordinator", str(ctx.exception).lower())
        self.assertEqual("pending", self.store.list_tasks()[0]["status"])

    def test_update_task_rejected_for_foreign_caller(self) -> None:
        self.store.claim_coordinator()
        task = self.store.create_task(issue=42, group_label="g-42", risk="standard")
        os.environ["GWO_AGENT_ID"] = "worker-foreign"
        foreign = self.store_mod.Store.connect(
            self.fixture.home, self.fixture.repo
        )
        try:
            with self.assertRaises(self.store_mod.IdentityError) as ctx:
                foreign.update_task(task_id=task["task_id"], status="ready")
            self.assertIn("coordinator", str(ctx.exception).lower())
        finally:
            foreign.close()
        os.environ["GWO_AGENT_ID"] = "coordinator-001"
        self.assertEqual("pending", self.store.list_tasks()[0]["status"])

    def test_create_dispatch_rejected_when_no_active_claim(self) -> None:
        self.store.claim_coordinator()
        task = self.store.create_task(issue=42, group_label="g-42", risk="standard")
        self.store.update_task(task_id=task["task_id"], status="ready")
        self.store.release_coordinator()
        with self.assertRaises(self.store_mod.IdentityError) as ctx:
            self.store.create_dispatch(
                task_id=task["task_id"],
                agent_id="worker-001",
                worktree="/tmp/wt-42",
                branch="work/issue-42",
            )
        self.assertIn("coordinator", str(ctx.exception).lower())
        active = int(self.store.db.execute(
            "SELECT COUNT(*) FROM dispatches WHERE task_id = ? AND status = 'active'",
            (task["task_id"],),
        ).fetchone()[0])
        self.assertEqual(0, active)
        self.assertEqual("ready", self.store.list_tasks()[0]["status"])

    def test_create_dispatch_rejected_for_foreign_caller(self) -> None:
        self.store.claim_coordinator()
        task = self.store.create_task(issue=42, group_label="g-42", risk="standard")
        self.store.update_task(task_id=task["task_id"], status="ready")
        os.environ["GWO_AGENT_ID"] = "worker-foreign"
        foreign = self.store_mod.Store.connect(
            self.fixture.home, self.fixture.repo
        )
        try:
            with self.assertRaises(self.store_mod.IdentityError) as ctx:
                foreign.create_dispatch(
                    task_id=task["task_id"],
                    agent_id="attacker-agent",
                    worktree="/tmp/evil",
                    branch="evil",
                )
            self.assertIn("coordinator", str(ctx.exception).lower())
        finally:
            foreign.close()
        os.environ["GWO_AGENT_ID"] = "coordinator-001"
        active = int(self.store.db.execute(
            "SELECT COUNT(*) FROM dispatches WHERE task_id = ? AND status = 'active'",
            (task["task_id"],),
        ).fetchone()[0])
        self.assertEqual(0, active, "foreign caller must not dispatch")
        self.assertEqual("ready", self.store.list_tasks()[0]["status"])

    def test_create_dispatch_succeeds_for_active_claim_holder(self) -> None:
        self.store.claim_coordinator()
        task = self.store.create_task(issue=42, group_label="g-42", risk="standard")
        self.store.update_task(task_id=task["task_id"], status="ready")
        dispatch = self.store.create_dispatch(
            task_id=task["task_id"],
            agent_id="worker-001",
            worktree="/tmp/wt-42",
            branch="work/issue-42",
        )
        self.assertEqual("active", dispatch["status"])

    def test_release_then_reclaim_restores_authorization(self) -> None:
        self.store.claim_coordinator()
        task = self.store.create_task(issue=42, group_label="g-42", risk="standard")
        self.store.release_coordinator()
        with self.assertRaises(self.store_mod.IdentityError):
            self.store.update_task(task_id=task["task_id"], status="ready")
        self.store.claim_coordinator()
        updated = self.store.update_task(task_id=task["task_id"], status="ready")
        self.assertEqual("ready", updated["status"])


class MigrationConcurrencyTests(unittest.TestCase):
    """Finding 2: migration discovery occurs before the per-migration
    transaction. Two synchronized Store.connect calls can both observe the same
    pending migration; one succeeds and the other fails on
    schema_migrations.name uniqueness. The migration row must be claimed or
    re-checked under the same BEGIN IMMEDIATE that applies DDL and records
    completion.
    """

    def setUp(self) -> None:
        self.fixture = StoreFixture(self, repo="owner/repo")
        self.store = self.fixture.store
        self.store_mod = self.fixture.store_mod

    def tearDown(self) -> None:
        self.fixture.cleanup()

    def test_concurrent_connect_does_not_raise_uniqueness_error(self) -> None:
        store_mod = self.store_mod
        original_migrations = store_mod.MIGRATIONS
        probe_ddl = (
            "CREATE TABLE IF NOT EXISTS migration_concurrency_probe "
            "(id INTEGER PRIMARY KEY)"
        )
        store_mod.MIGRATIONS = original_migrations + (
            ("9995-concurrent-probe", probe_ddl),
        )
        self.store.close()
        try:
            os.environ["GWO_AGENT_ID"] = "coordinator-001"
            first = store_mod.Store.connect(
                self.fixture.home, self.fixture.repo, caller_agent_id="coordinator-001"
            )
            first.close()
            second = store_mod.Store.connect(
                self.fixture.home, self.fixture.repo, caller_agent_id="coordinator-001"
            )
            try:
                records = {
                    str(row[0])
                    for row in second.db.execute(
                        "SELECT name FROM schema_migrations"
                    ).fetchall()
                }
                self.assertIn("9995-concurrent-probe", records)
                self.assertEqual(
                    1,
                    len(second.db.execute(
                        "SELECT name FROM schema_migrations WHERE name = '9995-concurrent-probe'"
                    ).fetchall()),
                    "migration must be recorded exactly once across connects",
                )
                self.assertIn(
                    "migration_concurrency_probe", set(second.table_names())
                )
            finally:
                second.close()
        finally:
            store_mod.MIGRATIONS = original_migrations

    def test_concurrent_discovery_then_apply_serializes_cleanly(self) -> None:
        """Reproduce the race: both writers discover the pending migration
        before either applies it. Writer A commits the migration. Writer B,
        holding a stale 'pending' snapshot, must not fail with a uniqueness
        error; it must re-check under its own BEGIN IMMEDIATE and skip the
        already-applied migration."""
        store_mod = self.store_mod
        original_migrations = store_mod.MIGRATIONS
        probe_ddl = (
            "CREATE TABLE IF NOT EXISTS migration_concurrency_probe3 "
            "(id INTEGER PRIMARY KEY)"
        )
        migration_name = "9993b-concurrent-probe3"
        store_mod.MIGRATIONS = original_migrations + (
            (migration_name, probe_ddl),
        )
        slug = store_mod._repo_slug("owner/repo")
        db_path = str(self.fixture.home / slug / "state.db")
        try:
            b_raw = sqlite3.connect(db_path)
            b_raw.row_factory = sqlite3.Row
            b_raw.isolation_level = None
            b_raw.execute("BEGIN IMMEDIATE")
            b_raw.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations (name TEXT PRIMARY KEY)"
            )
            b_discovered = {
                str(row[0])
                for row in b_raw.execute(
                    "SELECT name FROM schema_migrations"
                ).fetchall()
            }
            self.assertNotIn(
                migration_name, b_discovered,
                "B must observe the migration as pending before A applies it",
            )
            b_raw.execute("COMMIT")
            os.environ["GWO_AGENT_ID"] = "coordinator-001"
            a_store = store_mod.Store.connect(
                self.fixture.home, self.fixture.repo, caller_agent_id="coordinator-001"
            )
            a_store.close()
            b_store = store_mod.Store.connect(
                self.fixture.home, self.fixture.repo, caller_agent_id="coordinator-001"
            )
            try:
                b_store.run_migrations()
                records = {
                    str(row[0])
                    for row in b_store.db.execute(
                        "SELECT name FROM schema_migrations"
                    ).fetchall()
                }
                self.assertIn(migration_name, records)
                self.assertEqual(
                    1,
                    len(b_store.db.execute(
                        "SELECT name FROM schema_migrations WHERE name = ?",
                        (migration_name,),
                    ).fetchall()),
                    "migration must be recorded exactly once after the race",
                )
            finally:
                b_store.close()
                b_raw.close()
        finally:
            store_mod.MIGRATIONS = original_migrations

    def test_apply_migration_rechecks_row_under_same_transaction(self) -> None:
        """Directly reproduce the uniqueness race: A applies the migration,
        then B calls _apply_migration for the same name. B must re-check the
        schema_migrations row under its own BEGIN IMMEDIATE and skip cleanly
        rather than raising a uniqueness error."""
        store_mod = self.store_mod
        original_migrations = store_mod.MIGRATIONS
        probe_ddl = (
            "CREATE TABLE IF NOT EXISTS migration_concurrency_probe4 "
            "(id INTEGER PRIMARY KEY)"
        )
        migration_name = "9993c-concurrent-probe4"
        store_mod.MIGRATIONS = original_migrations + (
            (migration_name, probe_ddl),
        )
        try:
            os.environ["GWO_AGENT_ID"] = "coordinator-001"
            a_store = store_mod.Store.connect(
                self.fixture.home, self.fixture.repo, caller_agent_id="coordinator-001"
            )
            a_store.close()
            b_store = store_mod.Store.connect(
                self.fixture.home, self.fixture.repo, caller_agent_id="coordinator-001"
            )
            try:
                b_store._apply_migration(migration_name, probe_ddl, "coordinator-001")
                records = {
                    str(row[0])
                    for row in b_store.db.execute(
                        "SELECT name FROM schema_migrations"
                    ).fetchall()
                }
                self.assertIn(migration_name, records)
                self.assertEqual(
                    1,
                    len(b_store.db.execute(
                        "SELECT name FROM schema_migrations WHERE name = ?",
                        (migration_name,),
                    ).fetchall()),
                    "migration must be recorded exactly once after duplicate apply",
                )
            finally:
                b_store.close()
        finally:
            store_mod.MIGRATIONS = original_migrations

    def test_concurrent_migration_claim_serializes_cleanly(self) -> None:
        store_mod = self.store_mod
        original_migrations = store_mod.MIGRATIONS
        probe_ddl = (
            "CREATE TABLE IF NOT EXISTS migration_concurrency_probe2 "
            "(id INTEGER PRIMARY KEY)"
        )
        store_mod.MIGRATIONS = original_migrations + (
            ("9994-concurrent-probe2", probe_ddl),
        )
        try:
            slug = store_mod._repo_slug("owner/repo")
            db_path = str(self.fixture.home / slug / "state.db")
            a_raw = sqlite3.connect(db_path)
            a_raw.row_factory = sqlite3.Row
            a_raw.isolation_level = None
            try:
                a_raw.execute("BEGIN IMMEDIATE")
                a_raw.execute(
                    "CREATE TABLE IF NOT EXISTS schema_migrations (name TEXT PRIMARY KEY)"
                )
                a_raw.execute(
                    "INSERT INTO schema_migrations (name) VALUES (?)",
                    ("9994-concurrent-probe2",),
                )
                a_raw.execute(probe_ddl)
                a_raw.execute("COMMIT")
                os.environ["GWO_AGENT_ID"] = "coordinator-001"
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
                    self.assertIn("9994-concurrent-probe2", records)
                    self.assertEqual(
                        1,
                        len(reopened.db.execute(
                            "SELECT name FROM schema_migrations WHERE name = '9994-concurrent-probe2'"
                        ).fetchall()),
                        "migration must be recorded exactly once",
                    )
                finally:
                    reopened.close()
            finally:
                a_raw.close()
        finally:
            store_mod.MIGRATIONS = original_migrations


class MigrationStatementParserTests(unittest.TestCase):
    """Finding 3: ddl.split(';') is not a SQLite statement parser and breaks
    valid literals/comments/triggers containing semicolons. Use
    sqlite3.complete_statement or explicit statement sequences while
    preserving atomic rollback.
    """

    def setUp(self) -> None:
        self.fixture = StoreFixture(self, repo="owner/repo")
        self.store = self.fixture.store
        self.store_mod = self.fixture.store_mod

    def tearDown(self) -> None:
        self.fixture.cleanup()

    def test_semicolon_in_string_literal_is_not_split(self) -> None:
        store_mod = self.store_mod
        original_migrations = store_mod.MIGRATIONS
        ddl = (
            "CREATE TABLE IF NOT EXISTS migration_literal_probe "
            "(id INTEGER PRIMARY KEY, note TEXT); "
            "INSERT INTO migration_literal_probe (id, note) VALUES "
            "(1, 'semi;colon;in;literal')"
        )
        store_mod.MIGRATIONS = original_migrations + (
            ("9993-literal", ddl),
        )
        try:
            self.store.run_migrations()
            records = self.store.db.execute(
                "SELECT name FROM schema_migrations WHERE name = '9993-literal'"
            ).fetchall()
            self.assertEqual(1, len(records))
            row = self.store.db.execute(
                "SELECT note FROM migration_literal_probe WHERE id = 1"
            ).fetchone()
            self.assertEqual("semi;colon;in;literal", row["note"])
        finally:
            store_mod.MIGRATIONS = original_migrations

    def test_semicolon_in_sql_comment_is_not_split(self) -> None:
        store_mod = self.store_mod
        original_migrations = store_mod.MIGRATIONS
        ddl = (
            "CREATE TABLE IF NOT EXISTS migration_comment_probe "
            "(id INTEGER PRIMARY KEY); "
            "-- this comment has a ; semicolon in it\n"
            "CREATE TABLE IF NOT EXISTS migration_comment_probe2 "
            "(id INTEGER PRIMARY KEY)"
        )
        store_mod.MIGRATIONS = original_migrations + (
            ("9992-comment", ddl),
        )
        try:
            self.store.run_migrations()
            records = self.store.db.execute(
                "SELECT name FROM schema_migrations WHERE name = '9992-comment'"
            ).fetchall()
            self.assertEqual(1, len(records))
            tables = set(self.store.table_names())
            self.assertIn("migration_comment_probe", tables)
            self.assertIn("migration_comment_probe2", tables)
        finally:
            store_mod.MIGRATIONS = original_migrations

    def test_rollback_preserved_with_literal_containing_semicolon(self) -> None:
        store_mod = self.store_mod
        original_migrations = store_mod.MIGRATIONS
        ddl = (
            "CREATE TABLE IF NOT EXISTS migration_rollback_probe "
            "(id INTEGER PRIMARY KEY, note TEXT); "
            "INSERT INTO migration_rollback_probe (id, note) VALUES "
            "(1, 'a;b;c'); "
            "CREATE TABLE migration_rollback_dup (id INTEGER PRIMARY KEY); "
            "CREATE TABLE migration_rollback_dup (id INTEGER PRIMARY KEY)"
        )
        store_mod.MIGRATIONS = original_migrations + (
            ("9991-rollback-literal", ddl),
        )
        try:
            with self.assertRaises(sqlite3.OperationalError):
                self.store.run_migrations()
            records = self.store.db.execute(
                "SELECT name FROM schema_migrations WHERE name = '9991-rollback-literal'"
            ).fetchall()
            self.assertEqual(0, len(records), "failed migration must not be recorded")
            tables = set(self.store.table_names())
            self.assertNotIn(
                "migration_rollback_probe",
                tables,
                "first DDL must roll back with the failing migration",
            )
        finally:
            store_mod.MIGRATIONS = original_migrations


class Migration0005Tests(unittest.TestCase):
    """Issue #37: migration 0005 adds review authority, round tail, and
    chain-integrity schema. Pre-existing malformed 0004/0005 schema must roll
    back atomically without a schema_migrations success record.
    """

    def setUp(self) -> None:
        self.fixture = StoreFixture(self, repo="owner/repo")
        self.store = self.fixture.store
        self.store_mod = self.fixture.store_mod

    def tearDown(self) -> None:
        self.fixture.cleanup()

    def _fresh_applied(self) -> set[str]:
        return {
            str(row[0])
            for row in self.store.db.execute("SELECT name FROM schema_migrations").fetchall()
        }

    def test_fresh_store_applies_migrations_in_order_including_0005(self) -> None:
        migrations = [
            str(row[0])
            for row in self.store.db.execute(
                "SELECT name FROM schema_migrations ORDER BY rowid"
            ).fetchall()
        ]
        self.assertEqual(
            [
                "0001-initial",
                "0002-messages-in-reply-to",
                "0003-tasks-repo-issue-unique",
                "0004-review-rounds-and-lease",
                "0005-review-authority-and-chain-integrity",
            ],
            migrations,
        )

    def test_0005_adds_review_assignments_table(self) -> None:
        tables = set(self.store.table_names())
        self.assertIn("review_assignments", tables)

    def test_0005_adds_integration_chain_position_columns(self) -> None:
        columns = {
            str(row[1])
            for row in self.store.db.execute(
                "PRAGMA table_info(integration_chain)"
            ).fetchall()
        }
        self.assertIn("position", columns)
        self.assertIn("head", columns)

    def test_0005_adds_review_rounds_tail_and_axis_columns(self) -> None:
        columns = {
            str(row[1])
            for row in self.store.db.execute(
                "PRAGMA table_info(review_rounds)"
            ).fetchall()
        }
        self.assertIn("is_current", columns)
        self.assertIn("assigned_axis", columns)

    def _build_pre_0005_store(self) -> None:
        """Wipe the fixture database and apply only migrations 0001-0004."""
        store_mod = self.store_mod
        self.store.close()
        slug = store_mod._repo_slug("owner/repo")
        db_path = str(self.fixture.home / slug / "state.db")
        raw = sqlite3.connect(db_path)
        try:
            raw.execute("PRAGMA foreign_keys=OFF")
            master = raw.execute(
                "SELECT type, name FROM sqlite_master WHERE type IN ('table', 'index', 'trigger') "
                "AND name NOT LIKE 'sqlite_%'"
            ).fetchall()
            for kind, name in master:
                raw.execute(f"DROP {kind} IF EXISTS {name}")
            raw.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations (name TEXT PRIMARY KEY)"
            )
            raw.execute("BEGIN IMMEDIATE")
            for name, ddl in store_mod.MIGRATIONS:
                if name in {
                    "0001-initial",
                    "0002-messages-in-reply-to",
                    "0003-tasks-repo-issue-unique",
                    "0004-review-rounds-and-lease",
                }:
                    for statement in store_mod._split_sql_statements(ddl):
                        raw.execute(statement)
                    raw.execute(
                        "INSERT OR REPLACE INTO schema_migrations (name) VALUES (?)", (name,)
                    )
            raw.execute("COMMIT")
        finally:
            raw.close()

    def test_0005_malformed_schema_rolls_back_without_record(self) -> None:
        """A legacy store whose 0004/0005 shape is wrong must roll back and not
        record 0005 as applied.
        """
        store_mod = self.store_mod
        self._build_pre_0005_store()
        # Add a wrong-shape column that conflicts with 0005's expected DDL.
        slug = store_mod._repo_slug("owner/repo")
        db_path = str(self.fixture.home / slug / "state.db")
        raw = sqlite3.connect(db_path)
        raw.isolation_level = None
        try:
            raw.execute(
                "ALTER TABLE review_rounds ADD COLUMN is_current INTEGER NOT NULL DEFAULT 0"
            )
            raw.commit()
        finally:
            raw.close()
        with self.assertRaises((sqlite3.OperationalError, store_mod.StoreError)):
            store_mod.Store.connect(
                self.fixture.home, "owner/repo", caller_agent_id="coordinator-001"
            )
        raw = sqlite3.connect(db_path)
        raw.isolation_level = None
        try:
            migrations = {
                str(row[0])
                for row in raw.execute("SELECT name FROM schema_migrations").fetchall()
            }
            self.assertNotIn("0005-review-authority-and-chain-integrity", migrations)
            tables = {
                str(row[1])
                for row in raw.execute(
                    "SELECT type, name FROM sqlite_master WHERE type = 'table'"
                ).fetchall()
            }
            self.assertNotIn("review_assignments", tables)
        finally:
            raw.close()


if __name__ == "__main__":
    unittest.main()
