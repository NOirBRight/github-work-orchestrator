from pathlib import Path
import sqlite3
import subprocess
import sys
import threading
import time

import pytest


SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "orchestrator" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


from gwo_v8.campaign_watchdog import (
    CampaignWatchdog,
    CampaignWatchdogError,
    WatchdogWake,
)
from gwo_v8.execution_kernel import CampaignStatus
from gwo_v8.plan_control import CampaignHandle
from v8_watchdog_test_support import (
    NOW,
    RecordingAdvancer,
    RecordingCampaignSource,
    RecordingWakeSource,
    handle,
    make_snapshot,
    make_watchdog,
    page,
    runtime_wake,
    wake,
)


def test_wake_ref_binds_source_cursor_and_identity():
    wake = WatchdogWake(
        cursor="41",
        campaign=CampaignHandle("owner/repo", "campaign:alpha"),
        source="runtime",
        source_identity="stable-action:worker-a",
    )
    assert wake.wake_ref == "watchdog:runtime:41:stable-action:worker-a"


def test_changed_event_reusing_cursor_fails_without_advancing_cursor(tmp_path):
    source = RecordingWakeSource(pages=[page(runtime_wake("7", "action:a"))])
    watchdog = make_watchdog(tmp_path, source=source)
    watchdog.run_once("2026-08-03T10:00:00+00:00")
    source.pages = [page(runtime_wake("7", "action:b"))]
    with pytest.raises(CampaignWatchdogError) as raised:
        watchdog.run_once("2026-08-03T10:01:00+00:00")
    assert raised.value.code == "WATCHDOG_CURSOR_CONFLICT"
    assert watchdog.read_cursor("runtime_gateway") == "7"


def test_empty_same_cursor_poll_is_a_noop_without_duplicate_dispatch(tmp_path):
    source = RecordingWakeSource(
        pages=[
            page(runtime_wake("7", "action:a")),
            page(next_cursor="7"),
        ]
    )
    advancer = RecordingAdvancer()
    watchdog = make_watchdog(tmp_path, source=source, advancer=advancer)

    watchdog.run_once("2026-08-03T10:00:00+00:00")
    watchdog.run_once("2026-08-03T10:01:00+00:00")

    assert source.calls == [None, "7"]
    assert advancer.calls == [(handle(), "watchdog:runtime:7:action:a")]
    assert watchdog.read_cursor("runtime_gateway") == "7"


def test_exact_page_replay_dispatches_once_and_stores_one_wake(tmp_path):
    replay = page(runtime_wake("9", "action:a"))
    source = RecordingWakeSource(pages=[replay, replay])
    advancer = RecordingAdvancer()
    watchdog = make_watchdog(tmp_path, source=source, advancer=advancer)

    first = watchdog.run_once(NOW)
    second = watchdog.run_once(NOW)

    assert first == (advancer.outcome,)
    assert second == ()
    assert advancer.calls == [(handle(), "watchdog:runtime:9:action:a")]
    with sqlite3.connect(watchdog._store_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM v8_watchdog_wakes").fetchone() == (1,)


def test_conflicting_page_rolls_back_wake_rows_and_preserves_dispatch(tmp_path):
    source = RecordingWakeSource(pages=[page(runtime_wake("7", "action:a"))])
    advancer = RecordingAdvancer()
    watchdog = make_watchdog(tmp_path, source=source, advancer=advancer)
    watchdog.run_once(NOW)
    with sqlite3.connect(watchdog._store_path) as connection:
        before = connection.execute(
            "SELECT wake_ref, cursor, source_identity FROM v8_watchdog_wakes"
        ).fetchall()

    source.pages = [page(runtime_wake("7", "action:b"))]
    with pytest.raises(CampaignWatchdogError) as raised:
        watchdog.run_once(NOW)

    assert raised.value.code == "WATCHDOG_CURSOR_CONFLICT"
    assert advancer.calls == [(handle(), "watchdog:runtime:7:action:a")]
    assert watchdog.read_cursor("runtime_gateway") == "7"
    with sqlite3.connect(watchdog._store_path) as connection:
        after = connection.execute(
            "SELECT wake_ref, cursor, source_identity FROM v8_watchdog_wakes"
        ).fetchall()
    assert after == before


def test_post_insert_cas_failure_rolls_back_wake_and_cursor(tmp_path):
    source = RecordingWakeSource(pages=[page(runtime_wake("7", "action:a"))])
    advancer = RecordingAdvancer()
    watchdog = make_watchdog(tmp_path, source=source, advancer=advancer)
    watchdog.run_once(NOW)
    with sqlite3.connect(watchdog._store_path) as connection:
        before_source = connection.execute(
            "SELECT cursor, page_digest FROM v8_watchdog_sources "
            "WHERE stream='runtime_gateway'"
        ).fetchone()
        before_wakes = connection.execute(
            "SELECT wake_ref, cursor, source_identity FROM v8_watchdog_wakes"
        ).fetchall()
        connection.execute(
            """
            CREATE TRIGGER force_watchdog_cas_failure
            AFTER INSERT ON v8_watchdog_wakes
            BEGIN
                DELETE FROM v8_watchdog_sources
                WHERE stream = 'runtime_gateway';
            END
            """
        )

    source.pages = [page(runtime_wake("8", "action:b"))]
    with pytest.raises(CampaignWatchdogError) as raised:
        watchdog.run_once(NOW)

    assert raised.value.code == "WATCHDOG_CURSOR_CONFLICT"
    assert advancer.calls == [(handle(), "watchdog:runtime:7:action:a")]
    assert watchdog.read_cursor("runtime_gateway") == "7"
    with sqlite3.connect(watchdog._store_path) as connection:
        after_source = connection.execute(
            "SELECT cursor, page_digest FROM v8_watchdog_sources "
            "WHERE stream='runtime_gateway'"
        ).fetchone()
        after_wakes = connection.execute(
            "SELECT wake_ref, cursor, source_identity FROM v8_watchdog_wakes"
        ).fetchall()
    assert after_source == before_source
    assert after_wakes == before_wakes


@pytest.mark.parametrize(
    "invalid_now",
    ["2026-08-03T10:00:00Z", "2026-08-03T10:00:00+08:00"],
)
def test_invalid_now_is_rejected_before_any_store_write(tmp_path, invalid_now):
    source = RecordingWakeSource(pages=[page(runtime_wake("1", "action:a"))])
    watchdog = make_watchdog(tmp_path, source=source)

    with pytest.raises(CampaignWatchdogError) as raised:
        watchdog.run_once(invalid_now)

    assert raised.value.code == "WATCHDOG_INPUT_INVALID"
    assert source.calls == []
    with sqlite3.connect(watchdog._store_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM v8_watchdog_sources").fetchone() == (0,)
        assert connection.execute("SELECT COUNT(*) FROM v8_watchdog_wakes").fetchone() == (0,)


def test_invalid_source_page_is_rejected_before_any_wake_write(tmp_path):
    source = RecordingWakeSource(pages=[object()])
    watchdog = make_watchdog(tmp_path, source=source)

    with pytest.raises(CampaignWatchdogError) as raised:
        watchdog.run_once(NOW)

    assert raised.value.code == "WATCHDOG_INPUT_INVALID"
    with sqlite3.connect(watchdog._store_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM v8_watchdog_sources").fetchone() == (0,)
        assert connection.execute("SELECT COUNT(*) FROM v8_watchdog_wakes").fetchone() == (0,)


def test_multiple_streams_and_events_dispatch_in_deterministic_order(tmp_path):
    runtime_source = RecordingWakeSource(
        pages=[page(wake("3", source_identity="runtime:b"), wake("4", source_identity="runtime:c"))]
    )
    hosted_source = RecordingWakeSource(
        pages=[page(wake("2", source="hosted_check", source_identity="check:a"))]
    )
    advancer = RecordingAdvancer()
    watchdog = CampaignWatchdog(
        store_path=tmp_path / "multi-stream.db",
        event_sources={
            "runtime_gateway": runtime_source,
            "hosted_check": hosted_source,
        },
        campaign_source=RecordingCampaignSource({handle(): make_snapshot()}),
        advancer=advancer,
    )

    watchdog.run_once(NOW)

    assert advancer.calls == [
        (handle(), "watchdog:hosted_check:2:check:a"),
        (handle(), "watchdog:runtime:3:runtime:b"),
        (handle(), "watchdog:runtime:4:runtime:c"),
    ]
    assert hosted_source.calls == [None]
    assert runtime_source.calls == [None]


def test_due_projection_rebuilds_scheduled_rows_and_removes_stale_rows(tmp_path):
    scheduled = CampaignHandle("owner/repo", "campaign:scheduled")
    removed = CampaignHandle("owner/repo", "campaign:removed")
    replacement = CampaignHandle("owner/repo", "campaign:replacement")
    campaigns = RecordingCampaignSource(
        {
            scheduled: make_snapshot(
                campaign=scheduled,
                next_check_at="2026-08-03T09:59:00+00:00",
            ),
            removed: make_snapshot(
                campaign=removed,
                next_check_at="2026-08-03T09:58:00+00:00",
            ),
        }
    )
    watchdog = make_watchdog(
        tmp_path,
        source=RecordingWakeSource([]),
        campaign_source=campaigns,
    )
    watchdog.rebuild_due_queue()
    campaigns.snapshots = {
        scheduled: make_snapshot(campaign=scheduled, status=CampaignStatus.COMPLETE),
        replacement: make_snapshot(
            campaign=replacement,
            next_check_at="2026-08-03T10:01:00+00:00",
        ),
    }

    watchdog.rebuild_due_queue()

    with sqlite3.connect(watchdog._store_path) as connection:
        rows = connection.execute(
            "SELECT repository, campaign_key, next_check_at FROM v8_watchdog_due "
            "ORDER BY repository, campaign_key"
        ).fetchall()
    assert rows == [
        ("owner/repo", "campaign:replacement", "2026-08-03T10:01:00+00:00")
    ]


def test_read_cursor_does_not_modify_the_store_bytes(tmp_path):
    source = RecordingWakeSource(pages=[page(runtime_wake("11", "action:a"))])
    watchdog = make_watchdog(tmp_path, source=source)
    watchdog.run_once(NOW)
    before = watchdog._store_path.read_bytes()

    assert watchdog.read_cursor("runtime_gateway") == "11"

    assert watchdog._store_path.read_bytes() == before


def test_restart_rebuilds_overdue_campaign_and_calls_advance_once(tmp_path):
    campaigns = RecordingCampaignSource(
        {handle(): make_snapshot(next_check_at="2026-08-03T09:59:00+00:00")}
    )
    source = RecordingWakeSource([])
    advancer = RecordingAdvancer()
    first = make_watchdog(
        tmp_path,
        source=source,
        campaign_source=campaigns,
        advancer=advancer,
    )
    first.rebuild_due_queue()
    restarted = make_watchdog(
        tmp_path,
        source=source,
        campaign_source=campaigns,
        advancer=advancer,
    )

    outcomes = restarted.run_once("2026-08-03T10:00:00+00:00")

    assert len(outcomes) == 1
    expected_source_identity = (
        f"{handle().repository}:{handle().campaign_key}:"
        "2026-08-03T09:59:00+00:00"
    )
    assert advancer.calls == [
        (handle(), f"watchdog:due:1:{expected_source_identity}")
    ]


def test_due_work_is_persisted_as_a_stable_watchdog_wake(tmp_path):
    due_at = "2026-08-03T09:59:00+00:00"
    campaigns = RecordingCampaignSource(
        {handle(): make_snapshot(next_check_at=due_at)}
    )
    advancer = RecordingAdvancer()
    watchdog = make_watchdog(
        tmp_path,
        source=RecordingWakeSource([]),
        campaign_source=campaigns,
        advancer=advancer,
    )
    watchdog.rebuild_due_queue()

    watchdog.run_once(NOW)

    expected_source_identity = f"{handle().repository}:{handle().campaign_key}:{due_at}"
    expected_wake_ref = f"watchdog:due:1:{expected_source_identity}"
    assert advancer.calls == [(handle(), expected_wake_ref)]
    with sqlite3.connect(tmp_path / "watchdog.db") as connection:
        assert connection.execute(
            "SELECT wake_ref, cursor, repository, campaign_key, source, "
            "source_identity FROM v8_watchdog_wakes"
        ).fetchall() == [
            (
                expected_wake_ref,
                "1",
                "owner/repo",
                handle().campaign_key,
                "due",
                expected_source_identity,
            )
        ]


def test_due_wake_pending_row_replays_immediately_after_dispatch_crash(tmp_path):
    due_at = "2026-08-03T09:59:00+00:00"
    campaigns = RecordingCampaignSource(
        {handle(): make_snapshot(next_check_at=due_at)}
    )

    class CrashOnceAdvancer(RecordingAdvancer):
        def __init__(self):
            super().__init__()
            self.fail_once = True

        def advance(self, campaign, wake_ref=None):
            if self.fail_once:
                self.fail_once = False
                raise RuntimeError("crash before due wake dispatch")
            return super().advance(campaign, wake_ref)

    advancer = CrashOnceAdvancer()
    first = make_watchdog(
        tmp_path,
        source=RecordingWakeSource([]),
        campaign_source=campaigns,
        advancer=advancer,
    )
    first.rebuild_due_queue()

    with pytest.raises(RuntimeError, match="crash before due wake dispatch"):
        first.run_once(NOW)

    expected_source_identity = f"{handle().repository}:{handle().campaign_key}:{due_at}"
    expected_wake_ref = f"watchdog:due:1:{expected_source_identity}"
    with sqlite3.connect(tmp_path / "watchdog.db") as connection:
        assert connection.execute(
            "SELECT wake_ref FROM v8_watchdog_pending_wakes"
        ).fetchall() == [(expected_wake_ref,)]

    restarted = make_watchdog(
        tmp_path,
        source=RecordingWakeSource([]),
        campaign_source=campaigns,
        advancer=advancer,
    )
    assert len(restarted.run_once(NOW)) == 1
    assert advancer.calls == [(handle(), expected_wake_ref)]
    with sqlite3.connect(tmp_path / "watchdog.db") as connection:
        assert connection.execute(
            "SELECT wake_ref FROM v8_watchdog_pending_wakes"
        ).fetchall() == []


def test_due_work_orders_by_timestamp_then_campaign_and_rebuilds_after_advance(tmp_path):
    first = CampaignHandle("owner/repo", "campaign:a")
    second = CampaignHandle("owner/repo", "campaign:b")
    campaigns = RecordingCampaignSource(
        {
            first: make_snapshot(
                campaign=first,
                next_check_at="2026-08-03T09:58:00+00:00",
            ),
            second: make_snapshot(
                campaign=second,
                next_check_at="2026-08-03T09:59:00+00:00",
            ),
        }
    )

    class SnapshotUpdatingAdvancer(RecordingAdvancer):
        def advance(self, campaign, wake_ref=None):
            outcome = super().advance(campaign, wake_ref)
            if campaign == first:
                campaigns.snapshots[campaign] = make_snapshot(
                    campaign=campaign,
                    next_check_at="2026-08-03T10:05:00+00:00",
                )
            else:
                campaigns.snapshots[campaign] = make_snapshot(
                    campaign=campaign,
                    status=CampaignStatus.COMPLETE,
                )
            return outcome

    advancer = SnapshotUpdatingAdvancer()

    make_watchdog(
        tmp_path,
        source=RecordingWakeSource([]),
        campaign_source=campaigns,
        advancer=advancer,
    ).run_once(NOW)

    assert [call[0].campaign_key for call in advancer.calls] == [
        "campaign:a",
        "campaign:b",
    ]
    with sqlite3.connect(tmp_path / "watchdog.db") as connection:
        assert connection.execute(
            "SELECT repository, campaign_key, next_check_at "
            "FROM v8_watchdog_due ORDER BY repository, campaign_key"
        ).fetchall() == [
            ("owner/repo", "campaign:a", "2026-08-03T10:05:00+00:00")
        ]


def test_exact_event_replay_reuses_wake_ref_and_advances_cursor_once(tmp_path):
    replay = page(wake("9"))
    source = RecordingWakeSource([replay, replay])
    advancer = RecordingAdvancer()
    watchdog = make_watchdog(tmp_path, source=source, advancer=advancer)

    watchdog.run_once(NOW)
    with sqlite3.connect(tmp_path / "watchdog.db") as connection:
        connection.execute(
            "DELETE FROM v8_watchdog_sources WHERE stream='runtime_gateway'"
        )
    watchdog.run_once(NOW)

    expected = "watchdog:runtime:9:semantic:issue:113"
    assert advancer.calls == [(handle(), expected)]
    assert watchdog.read_cursor("runtime_gateway") == "9"


def test_failed_event_dispatch_remains_pending_for_restart_retry(tmp_path):
    replay = page(wake("9"))
    source = RecordingWakeSource([replay])

    class FailingAdvancer(RecordingAdvancer):
        def advance(self, campaign, wake_ref=None):
            super().advance(campaign, wake_ref)
            raise RuntimeError("dispatch failed")

    failing = FailingAdvancer()
    first = make_watchdog(tmp_path, source=source, advancer=failing)

    with pytest.raises(RuntimeError, match="dispatch failed"):
        first.run_once(NOW)

    retry_advancer = RecordingAdvancer()
    restarted = make_watchdog(
        tmp_path,
        source=source,
        advancer=retry_advancer,
    )

    restarted.run_once(NOW)

    assert retry_advancer.calls == [
        (handle(), "watchdog:runtime:9:semantic:issue:113")
    ]


def test_concurrent_watchdogs_claim_overdue_campaign_once(tmp_path):
    campaigns = RecordingCampaignSource(
        {
            handle(): make_snapshot(
                next_check_at="2026-08-03T09:59:00+00:00"
            )
        }
    )

    class BlockingAdvancer(RecordingAdvancer):
        def __init__(self):
            super().__init__()
            self.started = threading.Event()
            self.release = threading.Event()

        def advance(self, campaign, wake_ref=None):
            outcome = super().advance(campaign, wake_ref)
            self.started.set()
            if not self.release.wait(timeout=5):
                raise AssertionError("timed out waiting for the competing watchdog")
            return outcome

    first_advancer = BlockingAdvancer()
    second_advancer = RecordingAdvancer()
    first = make_watchdog(
        tmp_path,
        source=RecordingWakeSource([]),
        campaign_source=campaigns,
        advancer=first_advancer,
    )
    second = make_watchdog(
        tmp_path,
        source=RecordingWakeSource([]),
        campaign_source=campaigns,
        advancer=second_advancer,
    )
    first._claim_owner = "owner:first"
    second._claim_owner = "owner:second"
    first.rebuild_due_queue()

    first_outcomes = []
    first_errors = []

    def run_first():
        try:
            first_outcomes.append(first.run_once(NOW))
        except BaseException as error:
            first_errors.append(error)

    thread = threading.Thread(target=run_first)
    thread.start()
    assert first_advancer.started.wait(timeout=5)
    try:
        second_outcomes = second.run_once(NOW)
    finally:
        first_advancer.release.set()
        thread.join(timeout=5)

    assert not thread.is_alive()
    assert first_errors == []
    assert len(first_outcomes) == 1
    assert second_outcomes == ()
    expected_source_identity = (
        f"{handle().repository}:{handle().campaign_key}:"
        "2026-08-03T09:59:00+00:00"
    )
    assert first_advancer.calls == [
        (handle(), f"watchdog:due:1:{expected_source_identity}")
    ]
    assert second_advancer.calls == []


def test_release_due_claim_failure_releases_the_process_lock(tmp_path):
    first = make_watchdog(tmp_path, source=RecordingWakeSource([]))
    second = make_watchdog(tmp_path, source=RecordingWakeSource([]))
    assert first._try_acquire_due_lock(handle())

    with sqlite3.connect(first._store_path) as connection:
        connection.execute(
            "INSERT INTO v8_watchdog_due_claims "
            "(repository, campaign_key, claim_token, claimed_until) "
            "VALUES (?, ?, ?, ?)",
            (handle().repository, handle().campaign_key, "claim:release", NOW),
        )
        connection.execute(
            "CREATE TRIGGER fail_due_claim_release "
            "BEFORE DELETE ON v8_watchdog_due_claims "
            "BEGIN SELECT RAISE(ABORT, 'forced release failure'); END"
        )

    try:
        with pytest.raises(CampaignWatchdogError) as raised:
            first._release_due_claim(handle(), "claim:release")

        assert raised.value.code == "WATCHDOG_STORE_INVALID"
        assert second._try_acquire_due_lock(handle())
    finally:
        first._release_due_lock(handle())
        second._release_due_lock(handle())


def test_ack_due_claim_failure_releases_the_process_lock(tmp_path):
    campaigns = RecordingCampaignSource(
        {handle(): make_snapshot(next_check_at="2026-08-03T09:59:00+00:00")}
    )
    first = make_watchdog(
        tmp_path,
        source=RecordingWakeSource([]),
        campaign_source=campaigns,
    )
    second = make_watchdog(
        tmp_path,
        source=RecordingWakeSource([]),
        campaign_source=campaigns,
    )
    first.rebuild_due_queue()
    claims = first._claim_due_work(NOW)
    assert claims
    claimed_handle, claim_token = claims[0]

    with sqlite3.connect(first._store_path) as connection:
        connection.execute(
            "CREATE TRIGGER fail_due_ack "
            "BEFORE DELETE ON v8_watchdog_due "
            "BEGIN SELECT RAISE(ABORT, 'forced acknowledgement failure'); END"
        )

    try:
        with pytest.raises(CampaignWatchdogError) as raised:
            first._ack_due_claim(claimed_handle, claim_token)

        assert raised.value.code == "WATCHDOG_STORE_INVALID"
        assert second._try_acquire_due_lock(handle())
    finally:
        first._release_due_lock(handle())
        second._release_due_lock(handle())


def test_due_lock_is_exclusive_across_processes_and_releases_on_exit(tmp_path):
    store_path = tmp_path / "watchdog.db"
    make_watchdog(tmp_path, source=RecordingWakeSource([]))
    competitor = make_watchdog(tmp_path, source=RecordingWakeSource([]))
    ready_path = tmp_path / "child-ready"
    child_script = """
import sys
from pathlib import Path

sys.path.insert(0, sys.argv[2])

from gwo_v8.campaign_watchdog import CampaignWatchdog
from gwo_v8.plan_control import CampaignHandle


class EventSource:
    def read(self, after_cursor):
        raise AssertionError("event source should not be read")


class CampaignSource:
    def active_campaigns(self):
        return ()

    def watchdog_snapshot(self, handle):
        raise AssertionError("campaign source should not be read")


class Advancer:
    def advance(self, handle, wake_ref=None):
        raise AssertionError("advancer should not be called")


watchdog = CampaignWatchdog(
    store_path=Path(sys.argv[1]),
    event_sources={"runtime_gateway": EventSource()},
    campaign_source=CampaignSource(),
    advancer=Advancer(),
)
campaign = CampaignHandle("owner/repo", "campaign:watchdog-test")
if not watchdog._try_acquire_due_lock(campaign):
    raise SystemExit("child could not acquire due lock")
Path(sys.argv[3]).write_text("acquired", encoding="ascii")
sys.stdin.read()
"""
    process = subprocess.Popen(
        [
            sys.executable,
            "-c",
            child_script,
            str(store_path),
            str(SCRIPTS),
            str(ready_path),
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    try:
        deadline = time.monotonic() + 5
        while not ready_path.exists() and process.poll() is None:
            if time.monotonic() >= deadline:
                break
            time.sleep(0.01)
        if not ready_path.exists():
            stdout, stderr = process.communicate(timeout=5)
            pytest.fail(
                f"child did not acquire due lock: stdout={stdout!r}, stderr={stderr!r}"
            )

        assert not competitor._try_acquire_due_lock(handle())
    finally:
        if process.poll() is None:
            process.terminate()
        try:
            process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate(timeout=5)
        competitor._release_due_lock(handle())

    assert competitor._try_acquire_due_lock(handle())
    competitor._release_due_lock(handle())
