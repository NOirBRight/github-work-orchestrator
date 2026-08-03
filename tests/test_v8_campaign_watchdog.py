from pathlib import Path
import sqlite3
import sys

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
