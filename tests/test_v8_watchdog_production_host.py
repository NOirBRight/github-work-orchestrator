from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock

import pytest

pytest_plugins = ("v8_successor_test_support",)


SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "orchestrator" / "scripts"
import sys

sys.path.insert(0, str(SCRIPTS))

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
    watchdog = public_successor.host.install_campaign_watchdog(
        store_path=tmp_path / "watchdog.db",
        execution_kernel=kernel,
        _runtime_event_source=source,
    )
    watchdog.run_once(NOW)
    assert any(
        run.last_wake_ref == "watchdog:runtime:1:implementation:issue:113"
        for run in kernel.inspect(public_successor.handle).work_runs
    )
