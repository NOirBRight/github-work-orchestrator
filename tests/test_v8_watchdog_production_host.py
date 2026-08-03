from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock

import pytest


SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "orchestrator" / "scripts"
import sys

sys.path.insert(0, str(SCRIPTS))

from gwo_v8.runtime_gateway import (  # noqa: E402
    ArtifactStore,
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
            host_mappings={"coordinator": ProfileMapping(profile.digest)},
        ),
        _artifacts=store,
    )
    return gateway, store, adapter


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
