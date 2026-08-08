from __future__ import annotations

from pathlib import Path
import sys

import pytest

from tests.cutover_guard_test_support import valid_cutover_read_adapter_resolver


SCRIPTS = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "orchestrator"
    / "scripts"
)
sys.path.insert(0, str(SCRIPTS))

from gwo_v8._canonical import digest_value
from gwo_v8.execution_kernel import CampaignOutcome, CampaignStatus
from gwo_v8.plan_control import CampaignHandle
from gwo_v8.plan_control_host import ProductionPlanControlStartHost
from gwo_v8.production_effects import ProductionCompositionError
from gwo_v8.production_host import (
    PlanningContinuation,
    ProductionGwoHost,
    ProductionHostConfiguration,
)
from v8_production_test_support import (
    planning_host,
    reinstall_production_host,
)


def test_pending_planning_is_not_polled_by_advance_without_a_wake(
    tmp_path,
    planning_host,
):
    handle = planning_host.start("owner/repository", ("issue:108",))
    before = planning_host.planning_gateway_calls()
    outcome = planning_host.advance(handle)
    after = planning_host.planning_gateway_calls()
    assert outcome == CampaignOutcome(
        CampaignStatus.WAIT,
        "PlanningContinuationPending",
    )
    assert after == before


def test_wake_continues_the_same_persisted_planning_action_after_restart(
    tmp_path,
    planning_host,
):
    handle = planning_host.start("owner/repository", ("issue:108",))
    continuation = planning_host.start_host.read_planning_continuation(handle)
    assert continuation is not None
    restarted = reinstall_production_host(tmp_path, planning_host)
    assert restarted.start_host is not planning_host.start_host
    assert restarted.start_host.read_planning_continuation(handle) == continuation
    restarted.advance(handle, wake_ref="runtime:planning:41")
    assert restarted.planning_action_ids() == [continuation.stable_action_id]
    assert restarted.planning_pass_count() == 1


def test_revision_saved_before_activation_remains_a_planning_continuation(
    tmp_path,
):
    from test_v8_plancontrol_rebuild import (
        _Gateway,
        _GitHubPlanStateClient,
        _Source,
        _WriterGeneration,
    )

    from gwo_v8.plan_control import PlanControl
    from gwo_v8.plan_control_github import GitHubPlanRepository
    from gwo_v8.plan_control_host import ProductionPlanControlStartHost
    from gwo_v8.runtime_gateway import ArtifactStore, RuntimeConfiguration

    class CrashAfterRevisionRepository(GitHubPlanRepository):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.crash_once = True

        def save_attempt(self, attempt):
            result = super().save_attempt(attempt)
            if (
                self.crash_once
                and attempt.compilation_record_artifact_digest is not None
                and attempt.revision is not None
            ):
                self.crash_once = False
                raise RuntimeError("crash after revision, before activation")
            return result

    client = _GitHubPlanStateClient()
    writer = _WriterGeneration()

    def repository(repository_type=CrashAfterRevisionRepository):
        return repository_type(
            client,
            repository="owner/repository",
            branch="gwo-control",
            writer_generation="writer:one",
            writer_control=writer,
        )

    artifacts = ArtifactStore(tmp_path / "artifacts")
    first_repository = repository()
    with pytest.raises(RuntimeError):
        PlanControl(
            source=_Source(),
            artifacts=artifacts,
            gateway=_Gateway(artifacts),
            repository=first_repository,
        ).start("owner/repository", ["issue:109"])

    handle = CampaignHandle(
        "owner/repository",
        "campaign:"
        + digest_value(
            {"repository": "owner/repository", "ready_refs": ["issue:109"]}
        )[:24],
    )
    attempt = first_repository.read_attempt(handle, None)
    assert attempt is not None
    assert attempt.compilation_record_artifact_digest is not None
    assert attempt.revision is not None

    restarted_repository = repository(GitHubPlanRepository)
    host = ProductionPlanControlStartHost(
        source=_Source(),
        repository=restarted_repository,
        runtime_configuration=RuntimeConfiguration(
            profiles={},
            host_mappings={},
        ),
        repository_contexts={},
        gateway_store_path=tmp_path / "gateway.sqlite3",
        artifact_root=tmp_path / "artifacts",
        _gateway_builder=lambda **kwargs: _Gateway(kwargs["artifacts"]),
        cutover_read_adapter_resolver=valid_cutover_read_adapter_resolver(),
    )
    continuation = host.read_planning_continuation(handle)
    assert continuation is not None
    assert continuation.compilation_record_artifact_digest == (
        attempt.compilation_record_artifact_digest
    )
    host.continue_start(handle, continuation.ready_refs)
    assert host.read_active_or_none(handle) is not None


def test_replanning_attempt_is_not_exposed_as_initial_planning_continuation():
    from dataclasses import replace

    from test_v8_successor_plancontrol import (
        _initial_control,
        _seed_completed_successor,
    )

    control, repository, gateway, artifacts, source, handle = _initial_control()
    _seed_completed_successor(control, repository, gateway, artifacts, handle)
    from gwo_v8._canonical import canonical_bytes

    artifacts.read_bytes = lambda digest: canonical_bytes(artifacts.values[digest])
    attempt = repository.read_attempt(
        handle,
        control.read_active(handle).current_revision_digest,
    )
    assert attempt is not None
    repository.attempts[
        (handle.repository, handle.campaign_key, attempt.expected_previous_revision_digest)
    ] = replace(
        attempt,
        compilation_record_artifact_digest=None,
        compilation_record_bytes=None,
    )

    host = ProductionPlanControlStartHost.__new__(ProductionPlanControlStartHost)
    host._repository = repository
    host._artifacts = artifacts
    host._source = source
    assert host.read_planning_continuation(handle) is None


def test_continue_start_canonicalizes_ready_refs_before_matching_continuation():
    handle = CampaignHandle("owner/repository", "campaign:one")
    continuation = PlanningContinuation(
        campaign=handle,
        ready_refs=("issue:109",),
        expected_previous_revision_digest=None,
        snapshot_artifact_digest="1" * 64,
        planning_request_artifact_digest="2" * 64,
        stable_action_id="planning:one",
        compilation_record_artifact_digest=None,
    )

    class Source:
        def canonical_ready_refs(self, repository, refs):
            assert repository == "owner/repository"
            assert refs == ("#109",)
            return ("issue:109",)

    class Control:
        def __init__(self):
            self.calls = []

        def start(self, repository, refs, *, campaign_key):
            self.calls.append((repository, refs, campaign_key))
            return handle

    control = Control()
    host = ProductionPlanControlStartHost.__new__(ProductionPlanControlStartHost)
    host._source = Source()
    host.read_planning_continuation = lambda _handle: continuation
    host._existing_control = lambda _handle: control

    assert host.continue_start(handle, ("#109",)) == handle
    assert control.calls == [("owner/repository", ("issue:109",), "campaign:one")]


def test_pending_planning_inspect_is_read_only(tmp_path, planning_host):
    handle = planning_host.start("owner/repository", ("issue:108",))
    before = planning_host.store_bytes()
    diagnostics = planning_host.inspect(handle)
    assert diagnostics.status is CampaignStatus.WAIT
    assert diagnostics.reason == "PlanningContinuationPending"
    assert diagnostics.work_runs == ()
    assert planning_host.store_bytes() == before


def test_normal_real_repository_stays_on_v61_authority(tmp_path, planning_host):
    arguments = planning_host.install_arguments()
    arguments["host_configuration"] = ProductionHostConfiguration(
        target_isolation_root=tmp_path,
        writer_activation_enabled=False,
    )
    arguments["target_path"] = Path("D:/Workstation/github-work-orchestrator")
    with pytest.raises(ProductionCompositionError) as raised:
        ProductionGwoHost.install(**arguments)
    assert raised.value.code == "V8_ISOLATED_PREVIEW_REQUIRED"
