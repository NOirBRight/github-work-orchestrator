from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
import sqlite3
from types import SimpleNamespace
from unittest.mock import Mock, call, patch

import pytest

pytest_plugins = ("v8_successor_test_support",)


SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "orchestrator" / "scripts"
import sys

sys.path.insert(0, str(SCRIPTS))

import gwo_v8  # noqa: E402
from gwo_v8.runtime_gateway import (  # noqa: E402
    ArtifactStore,
    CampaignPlanningSubject,
    ProfileMapping,
    RuntimeConfiguration,
    RuntimeGateway,
    RuntimeGatewayError,
    RuntimeRepositoryContext,
    WorkRunPurpose,
    WorkRunSubject,
    _InMemoryRuntimeProviderAdapter,
    _RuntimeEvent,
    _RuntimeEventPage,
)
from gwo_v8.execution_kernel import (  # noqa: E402
    StaleBindingObservation,
    StaleReadbackState,
)
from gwo_v8.plan_control_host import (  # noqa: E402
    RuntimeGatewayWatchdogEventSource,
)
from gwo_v8.runtime_profile import RuntimeProfile  # noqa: E402
from v8_watchdog_test_support import (  # noqa: E402
    NOW,
    RecordingWakeSource,
    page,
    runtime_wake,
)


def _gateway(tmp_path: Path):
    store = ArtifactStore(tmp_path / "artifacts", maximum_bytes=300_000)
    profile = RuntimeProfile(
        name="watchdog",
        provider="test",
        model="test-model",
        thinking="high",
        mode="safe",
        features={},
    )
    adapter = _InMemoryRuntimeProviderAdapter(store)
    gateway = RuntimeGateway(
        store_path=tmp_path / "gateway.journal",
        _adapter=adapter,
        configuration=RuntimeConfiguration(
            profiles={profile.digest: profile},
            host_mappings={
                selector: ProfileMapping(profile.digest)
                for selector in (
                    "coordinator",
                    "worker",
                    "recovery_worker",
                    "review_primary",
                    "review_strong",
                    "specialist:policy-1",
                )
            },
        ),
        _artifacts=store,
    )
    adapter.events = Mock(wraps=adapter.events)
    return gateway, store, adapter


def _subject(*, purpose: WorkRunPurpose | None = None) -> WorkRunSubject:
    return WorkRunSubject(
        repository="owner/repository",
        campaign_key="campaign:watchdog",
        campaign_handle="handle:watchdog",
        plan_revision_digest="0" * 64,
        work_run_key="work-run:watchdog",
        ticket_key="issue:113",
        purpose=purpose or WorkRunPurpose.implementation(),
        prompt_artifact_digest="0" * 64,
        authority_subtree_digest="1" * 64,
        stable_action_id="action:watchdog",
    )


def _put_subject_artifacts(store: ArtifactStore, subject: WorkRunSubject) -> WorkRunSubject:
    plan = store.put_canonical({"revision": 1})
    unsigned = replace(
        subject,
        plan_revision_digest=plan.digest,
        prompt_artifact_digest="0" * 64,
    )
    prompt = store.put_canonical(
        {
            "schema_version": "gwo.runtime.prompt.v1",
            "subject_digest": unsigned.prompt_binding_digest,
            "authority_digest": unsigned.authority_digest,
            "payload": {"complete_contract": "watchdog"},
        }
    )
    return replace(unsigned, prompt_artifact_digest=prompt.digest)


def _put_planning_subject(store: ArtifactStore, subject: WorkRunSubject) -> CampaignPlanningSubject:
    snapshot = store.put_canonical({"tickets": [{"key": subject.ticket_key}]})
    policy = store.put_canonical({"policy": "frozen"})
    unsigned = CampaignPlanningSubject(
        repository=subject.repository,
        campaign_key=subject.campaign_key,
        campaign_handle=subject.campaign_handle,
        expected_previous_plan_revision_digest=None,
        snapshot_artifact_digest=snapshot.digest,
        policy_witness_digest=policy.digest,
        planning_request_artifact_digest="0" * 64,
        stable_action_id=f"planning:{subject.stable_action_id}",
    )
    from gwo_v8.planning_protocol import planning_prompt

    prompt = store.put_canonical(
        planning_prompt(
            subject_digest=unsigned.prompt_binding_digest,
            authority_digest=policy.digest,
            snapshot_artifact_digest=snapshot.digest,
            policy_witness_artifact_digest=policy.digest,
        )
    )
    return replace(unsigned, planning_request_artifact_digest=prompt.digest)


def _prepare_and_start(gateway: RuntimeGateway, subject: WorkRunSubject) -> None:
    planning = _put_planning_subject(gateway._artifacts, subject)
    gateway.planning_preflight(planning)
    gateway.progress(subject)


def test_read_watchdog_events_passes_cursor_once_and_returns_page_cursor(tmp_path):
    gateway, _store, adapter = _gateway(tmp_path)
    adapter.events = Mock(
        return_value=_RuntimeEventPage(
            events=(),
            next_cursor="11",
        )
    )
    page = gateway._read_watchdog_events("11")
    adapter.events.assert_called_once_with("11")
    assert page.next_cursor == "11"
    assert page.events == ()


@pytest.mark.parametrize(
    ("purpose", "event_kind", "expected_source"),
    (
        (WorkRunPurpose.implementation(), "state:running", "runtime"),
        (WorkRunPurpose.implementation(), "candidate:reference", "candidate"),
        (WorkRunPurpose.formal_review(), "state:running", "review"),
        (WorkRunPurpose.specialist_review("policy-1"), "state:completed", "review"),
    ),
)
def test_read_watchdog_events_maps_source_from_subject_and_event(
    tmp_path,
    purpose,
    event_kind,
    expected_source,
):
    gateway, store, adapter = _gateway(tmp_path)
    subject = _put_subject_artifacts(
        store,
        replace(_subject(purpose=purpose), stable_action_id=f"action:{expected_source}"),
    )
    _prepare_and_start(gateway, subject)
    adapter.events = Mock(
        return_value=_RuntimeEventPage(
            events=(_RuntimeEvent("13", subject.stable_action_id, event_kind),),
            next_cursor="13",
        )
    )
    wake = gateway._read_watchdog_events("12").events[0]
    assert wake.source == expected_source
    assert (wake.repository, wake.campaign_key) == (
        subject.repository,
        subject.campaign_key,
    )
    assert wake.stable_action_id == subject.stable_action_id


@pytest.mark.parametrize("bad_cursor", ("0", "01", "-1", 1, True))
def test_read_watchdog_events_rejects_bad_cursor_without_publication(
    tmp_path,
    bad_cursor,
):
    gateway, _store, adapter = _gateway(tmp_path)
    with pytest.raises(RuntimeGatewayError) as raised:
        gateway._read_watchdog_events(bad_cursor)
    assert raised.value.code == "RUNTIME_EVENT_CURSOR_INVALID"
    adapter.events.assert_not_called()


@pytest.mark.parametrize(
    "bad_cursor",
    (
        pytest.param(str(2**63), id="overflow"),
        pytest.param("9" * 5000, id="overlong-decimal"),
        pytest.param("１２", id="non-ascii-decimal"),
    ),
)
def test_read_watchdog_events_rejects_cursor_contract_values_before_adapter_io(
    tmp_path,
    bad_cursor,
):
    gateway, _store, adapter = _gateway(tmp_path)
    refresh = Mock(wraps=gateway._refresh_before_adapter_io)
    gateway._refresh_before_adapter_io = refresh

    with pytest.raises(RuntimeGatewayError) as raised:
        gateway._read_watchdog_events(bad_cursor)

    assert raised.value.code == "RUNTIME_EVENT_CURSOR_INVALID"
    adapter.events.assert_not_called()
    refresh.assert_not_called()


def test_read_watchdog_events_rejects_tampered_subject(tmp_path):
    gateway, store, adapter = _gateway(tmp_path)
    subject = _put_subject_artifacts(store, _subject())
    _prepare_and_start(gateway, subject)
    gateway._data["actions"][subject.stable_action_id]["subject"][
        "campaign_key"
    ] = "campaign:tampered"
    adapter.events = Mock(
        return_value=_RuntimeEventPage(
            events=(_RuntimeEvent("14", subject.stable_action_id, "state:running"),),
            next_cursor="14",
        )
    )
    with pytest.raises(RuntimeGatewayError):
        gateway._read_watchdog_events("13")


def test_read_watchdog_events_rejects_missing_persisted_action(tmp_path):
    gateway, _store, adapter = _gateway(tmp_path)
    adapter.events = Mock(
        return_value=_RuntimeEventPage(
            events=(_RuntimeEvent("15", "action:missing", "state:running"),),
            next_cursor="15",
        )
    )
    with pytest.raises(RuntimeGatewayError) as raised:
        gateway._read_watchdog_events("14")
    assert raised.value.code == "RUNTIME_PROVIDER_PROTOCOL_INVALID"


def test_host_runtime_event_wakes_the_same_installed_advance(
    tmp_path,
    public_successor,
):
    kernel = public_successor._kernel
    source = RecordingWakeSource(
        [
            page(
                runtime_wake(
                    "1",
                    "implementation:issue:113",
                    campaign=public_successor.handle,
                )
            )
        ]
    )
    wake_ref = "watchdog:runtime:1:implementation:issue:113"
    watchdog = public_successor.host.install_campaign_watchdog(
        store_path=tmp_path / "watchdog.db",
        execution_kernel=kernel,
        _runtime_event_source=source,
    )
    with patch.object(kernel, "advance", wraps=kernel.advance) as advance_spy:
        watchdog.run_once(NOW)
    advance_spy.assert_called_once_with(public_successor.handle, wake_ref)
    assert any(
        run.last_wake_ref == wake_ref
        for run in kernel.inspect(public_successor.handle).work_runs
    )


def test_host_default_runtime_gateway_source_wakes_the_same_installed_advance(
    tmp_path,
    public_successor,
):
    kernel = public_successor._kernel
    identity = "implementation:issue:113"

    class StrictRuntimeGateway:
        def __init__(self):
            self.reads = []

        def _read_watchdog_events(self, after_cursor):
            self.reads.append(after_cursor)
            return SimpleNamespace(
                events=(
                    SimpleNamespace(
                        cursor="1",
                        repository=public_successor.handle.repository,
                        campaign_key=public_successor.handle.campaign_key,
                        source="runtime",
                        stable_action_id=identity,
                    ),
                ),
                next_cursor="1",
            )

    gateway = StrictRuntimeGateway()
    builder_calls = []

    def gateway_builder(**kwargs):
        builder_calls.append(kwargs)
        return gateway

    public_successor.host._gateway_builder = gateway_builder
    read_calls = []
    original_read = RuntimeGatewayWatchdogEventSource.read

    def read_spy(source, after_cursor):
        read_calls.append(after_cursor)
        return original_read(source, after_cursor)

    with patch.object(RuntimeGatewayWatchdogEventSource, "read", read_spy):
        watchdog = public_successor.host.install_campaign_watchdog(
            store_path=tmp_path / "default-watchdog.db",
            execution_kernel=kernel,
        )
        with patch.object(kernel, "advance", wraps=kernel.advance) as advance_spy:
            watchdog.run_once(NOW)

    wake_ref = f"watchdog:runtime:1:{identity}"
    assert builder_calls
    assert (
        builder_calls[0]["gateway_store_path"]
        == public_successor.host._gateway_store_path
    )
    assert gateway.reads == [None]
    assert read_calls == [None]
    assert watchdog._campaign_source is kernel
    assert watchdog._advancer is kernel
    advance_spy.assert_called_once_with(public_successor.handle, wake_ref)


def _watchdog_due_row(store_path, handle):
    with sqlite3.connect(store_path) as connection:
        return connection.execute(
            "SELECT next_check_at, progress_digest FROM v8_watchdog_due "
            "WHERE repository=? AND campaign_key=?",
            (handle.repository, handle.campaign_key),
        ).fetchone()


def _make_public_due_advance_readable(effects):
    original_execute = effects.execute

    def execute(action):
        if action.kind == "stale_readback":
            return StaleBindingObservation(
                stable_action_id=action.stable_action_id,
                runtime_binding_id=action.runtime_binding_id,
                state=StaleReadbackState.IDLE,
                runtime_readback_digest="1" * 64,
                process_readback_digest="2" * 64,
                workspace_readback_digest="3" * 64,
                campaign_readback_digest="4" * 64,
                receipt_digest="5" * 64,
            )
        return original_execute(action)

    effects.execute = execute


def test_reinstall_rebuilds_lost_due_work_without_native_callback(
    tmp_path,
    public_successor,
):
    watchdog_store = tmp_path / "watchdog.db"
    kernel_store = public_successor._kernel._store_path
    runtime_store = public_successor.host._gateway_store_path
    first = public_successor.host.install_campaign_watchdog(
        store_path=watchdog_store,
        execution_kernel=public_successor._kernel,
        _runtime_event_source=RecordingWakeSource([]),
    )
    first.rebuild_due_queue()
    expected_due = _watchdog_due_row(watchdog_store, public_successor.handle)
    assert expected_due is not None

    with sqlite3.connect(watchdog_store) as connection:
        connection.execute(
            "DELETE FROM v8_watchdog_due WHERE repository=? AND campaign_key=?",
            (
                public_successor.handle.repository,
                public_successor.handle.campaign_key,
            ),
        )

    public_successor.reinstall()
    assert public_successor._kernel._store_path == kernel_store
    assert public_successor.host._gateway_store_path == runtime_store
    restarted = public_successor.host.install_campaign_watchdog(
        store_path=watchdog_store,
        execution_kernel=public_successor._kernel,
        _runtime_event_source=RecordingWakeSource([]),
    )
    assert restarted._store_path == watchdog_store
    assert _watchdog_due_row(watchdog_store, public_successor.handle) == expected_due

    due_now = datetime.fromisoformat(expected_due[0]) + timedelta(seconds=1)
    public_successor._kernel._clock = lambda: due_now
    _make_public_due_advance_readable(public_successor.effects)
    with patch.object(
        public_successor._kernel,
        "advance",
        wraps=public_successor._kernel.advance,
    ) as advance_spy:
        outcomes = restarted.run_once(due_now.isoformat())
    assert len(outcomes) == 1
    expected_wake_ref = (
        f"watchdog:due:1:{public_successor.handle.repository}:"
        f"{public_successor.handle.campaign_key}:{expected_due[0]}"
    )
    advance_spy.assert_called_once_with(public_successor.handle, expected_wake_ref)


def test_older_runtime_callback_after_newer_is_a_noop(
    tmp_path,
    public_successor,
):
    identity = "implementation:issue:113"
    source = RecordingWakeSource(
        [
            page(
                runtime_wake(
                    "22",
                    identity,
                    campaign=public_successor.handle,
                )
            ),
            page(
                runtime_wake(
                    "21",
                    identity,
                    campaign=public_successor.handle,
                )
            ),
        ]
    )
    watchdog = public_successor.host.install_campaign_watchdog(
        store_path=tmp_path / "reordered-watchdog.db",
        execution_kernel=public_successor._kernel,
        _runtime_event_source=source,
    )
    snapshot = public_successor._kernel.watchdog_snapshot(public_successor.handle)
    assert snapshot.next_check_at is not None
    before_due = (
        datetime.fromisoformat(snapshot.next_check_at) - timedelta(seconds=1)
    ).isoformat()

    with patch.object(
        public_successor._kernel,
        "advance",
        wraps=public_successor._kernel.advance,
    ) as advance_spy:
        watchdog.run_once(before_due)
        actions_after_newer = tuple(
            action.stable_action_id for action in public_successor.effects.executed
        )
        watchdog.run_once(before_due)

    assert advance_spy.call_args_list == [
        call(
            public_successor.handle,
            f"watchdog:runtime:22:{identity}",
        )
    ]
    assert watchdog.read_cursor("runtime_gateway") == "22"
    assert tuple(
        action.stable_action_id for action in public_successor.effects.executed
    ) == actions_after_newer


def test_watchdog_composition_adds_no_public_workflow_operation():
    possible_operations = {
        "start",
        "advance",
        "inspect",
        "run_once",
        "rebuild_due_queue",
        "install_campaign_watchdog",
    }
    assert set(gwo_v8.__all__) & possible_operations == {
        "start",
        "advance",
        "inspect",
    }
    for private_name in (
        "CampaignWatchdog",
        "RuntimeGatewayWatchdogEventSource",
        "install_campaign_watchdog",
    ):
        assert private_name not in gwo_v8.__all__
        assert not hasattr(gwo_v8, private_name)
