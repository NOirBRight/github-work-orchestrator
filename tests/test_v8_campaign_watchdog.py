from pathlib import Path
import sys

import pytest


SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "orchestrator" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


from gwo_v8.campaign_watchdog import WatchdogWake
from gwo_v8.campaign_watchdog import CampaignWatchdogError
from gwo_v8.plan_control import CampaignHandle
from v8_watchdog_test_support import (
    RecordingWakeSource,
    make_watchdog,
    page,
    runtime_wake,
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
