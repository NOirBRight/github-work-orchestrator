from __future__ import annotations

import base64
from dataclasses import replace
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills" / "orchestrator" / "scripts"
TESTS = ROOT / "tests"
for path in (SCRIPTS, TESTS):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))


def _github_repository(client):
    from gwo_v8.plan_control_github import GitHubPlanRepository

    return GitHubPlanRepository(
        client,
        repository="owner/repository",
        branch="gwo-control",
        writer_generation="writer:one",
        maximum_state_bytes=4096,
    )


class _LegacyContentClient:
    def __init__(self, content):
        from gwo_v8.activation import GitHubContent

        self._content_type = GitHubContent
        self.content = GitHubContent(content, "blob:one")

    def read(self, repository, branch, path):
        return self.content

    def compare_and_swap(
        self,
        repository,
        branch,
        path,
        content,
        *,
        expected_blob_sha,
        message,
    ):
        if expected_blob_sha != self.content.blob_sha:
            raise RuntimeError("synthetic legacy CAS conflict")
        self.content = self._content_type(content, "blob:next")
        return self.content


class _LegacyWriter:
    def read_current(self, repository):
        return SimpleNamespace(
            repository=repository,
            writer_generation="writer:one",
            record_id="writer:one",
        )


def test_github_round_trip_preserves_successor_protocol_and_classification():
    from test_v8_plancontrol_production import _RefContentClient
    from test_v8_successor_plancontrol import _classified_control
    from gwo_v8._canonical import load_canonical_json

    _control, memory, _gateway, _artifacts, _source, handle, classification = (
        _classified_control()
    )
    attempt = memory.read_attempt(handle, classification.plan_revision_digest)
    assert attempt is not None

    client = _RefContentClient()
    repository = _github_repository(client)
    repository.save_attempt(attempt)
    repository.save_invalidation_classification(handle, classification)
    index = load_canonical_json(
        client._commits[client.head][".gwo-v8/plan-control-v3.json"].content
    )
    assert index["schema_version"] == "gwo.plan.github-index.v6"
    assert "invalidation_classifications" in index["categories"]

    recovered = _github_repository(client)
    assert recovered.read_attempt(
        handle,
        classification.plan_revision_digest,
    ) == attempt
    assert recovered.read_invalidation_classification(
        handle,
        classification.action_id,
    ) == classification


def test_github_duplicate_classification_is_exact_or_conflict():
    from test_v8_plancontrol_production import _RefContentClient
    from test_v8_successor_plancontrol import _classified_control
    from gwo_v8.plan_control import PlanControlError

    _control, memory, _gateway, _artifacts, _source, handle, classification = (
        _classified_control()
    )
    client = _RefContentClient()
    repository = _github_repository(client)
    repository.save_invalidation_classification(handle, classification)

    assert repository.save_invalidation_classification(
        handle,
        classification,
    ) == classification

    changed = replace(classification, reason="A different exact result.")
    with pytest.raises(PlanControlError) as rejected:
        repository.save_invalidation_classification(handle, changed)

    assert rejected.value.code == "PLAN_INVALIDATION_CLASSIFICATION_CONFLICT"
    assert repository.read_invalidation_classification(
        handle,
        classification.action_id,
    ) == classification


def test_github_restart_activates_from_hydrated_same_pass_artifacts(tmp_path):
    from test_v8_plancontrol_production import _RefContentClient
    from test_v8_successor_plancontrol import (
        _Gateway,
        _Source,
        _execution_snapshot,
        _invalidation,
        _source_snapshot,
        _successor_payload,
    )
    from gwo_v8.plan_control import PlanControl
    from gwo_v8.runtime_gateway import ArtifactStore

    client = _RefContentClient()
    first_artifacts = ArtifactStore(tmp_path / "first-artifacts")
    first_gateway = _Gateway(first_artifacts)
    first_repository = _github_repository(client)
    first = PlanControl(
        source=_Source(_source_snapshot()),
        artifacts=first_artifacts,
        gateway=first_gateway,
        repository=first_repository,
    )
    handle = first.start(
        "owner/repository",
        ["issue:108", "issue:109", "issue:110"],
        campaign_key="campaign:successor",
    )
    first_gateway.payload = _successor_payload()
    predecessor = first.read_active(handle)
    classification = first.classify_plan_invalidations(
        handle,
        (_invalidation(first, handle),),
        _execution_snapshot(predecessor),
    )
    assert classification is not None
    assert first_gateway.replan_progresses == 1

    fresh_artifacts = ArtifactStore(tmp_path / "fresh-artifacts")
    fresh_gateway = _Gateway(fresh_artifacts, _successor_payload())
    fresh_repository = _github_repository(client)
    observation = fresh_repository.observe_campaign(
        handle,
        classification.plan_revision_digest,
    )
    fresh_repository.hydrate_campaign_artifacts(
        fresh_artifacts,
        observation,
    )
    restarted = PlanControl(
        source=_Source(_source_snapshot()),
        artifacts=fresh_artifacts,
        gateway=fresh_gateway,
        repository=fresh_repository,
    )

    restarted.start(
        "owner/repository",
        ["issue:108", "issue:109", "issue:110"],
        campaign_key=handle.campaign_key,
        expected_previous_revision_digest=classification.plan_revision_digest,
    )

    assert fresh_gateway.replan_progresses == 0
    active = restarted.read_active(handle)
    assert active.current_revision_digest != classification.plan_revision_digest


def test_github_lost_ack_replays_one_successor(tmp_path):
    from test_v8_plancontrol_production import _RefContentClient
    from test_v8_successor_plancontrol import (
        _Gateway,
        _Source,
        _execution_snapshot,
        _invalidation,
        _source_snapshot,
        _successor_payload,
    )
    from gwo_v8.plan_control import PlanControl
    from gwo_v8.runtime_gateway import ArtifactStore

    client = _RefContentClient()
    artifacts = ArtifactStore(tmp_path / "artifacts")
    gateway = _Gateway(artifacts, _successor_payload())
    repository = _github_repository(client)
    control = PlanControl(
        source=_Source(_source_snapshot()),
        artifacts=artifacts,
        gateway=gateway,
        repository=repository,
    )
    handle = control.start(
        "owner/repository",
        ["issue:108", "issue:109", "issue:110"],
        campaign_key="campaign:successor",
    )
    predecessor = control.read_active(handle)
    classification = control.classify_plan_invalidations(
        handle,
        (_invalidation(control, handle),),
        _execution_snapshot(predecessor),
    )
    assert classification is not None

    original = client.compare_and_swap_ref
    lost = False

    def commit_then_lose_ack(*args, **kwargs):
        nonlocal lost
        committed = original(*args, **kwargs)
        if not lost and "activate Plan Revision" in kwargs["message"]:
            lost = True
            raise RuntimeError("synthetic lost activation acknowledgement")
        return committed

    client.compare_and_swap_ref = commit_then_lose_ack
    readback = control.activate_successor(handle, classification)
    replay = control.activate_successor(handle, classification)

    assert lost
    assert replay == readback
    assert gateway.replan_progresses == 1
    assert len(repository._read_repo().activation_receipts) == 2


def test_github_v3_state_migrates_with_empty_classifications():
    from test_v8_successor_plancontrol import (
        _classified_control as make_classified_control,
        _initial_control,
    )
    from gwo_v8._canonical import canonical_bytes, load_canonical_json
    from gwo_v8.plan_control_github import GitHubPlanRepository, _repo_value

    _control, memory, _gateway, _artifacts, _source, handle = _initial_control()
    legacy = _repo_value("owner/repository", "writer:one", memory)
    legacy["schema_version"] = "gwo.plan.github-state.v3"
    legacy.pop("invalidation_classifications")
    for attempt in legacy["attempts"]:
        attempt.pop("planning_protocol_id")
    client = _LegacyContentClient(canonical_bytes(legacy))
    repository = GitHubPlanRepository(
        client,
        repository="owner/repository",
        branch="gwo-control",
        writer_generation="writer:one",
        writer_control=_LegacyWriter(),
    )

    assert repository.read_invalidation_classification(
        handle,
        "replan:not-yet-present",
    ) is None

    classification = make_classified_control()[-1]
    repository.save_invalidation_classification(handle, classification)

    persisted = load_canonical_json(client.content.content)
    assert persisted["schema_version"] == "gwo.plan.github-state.v4"
    assert len(persisted["invalidation_classifications"]) == 1
    assert repository.read_invalidation_classification(
        handle,
        classification.action_id,
    ) == classification


def _legacy_v5_attempt_fixture():
    from test_v8_successor_plancontrol import _classified_control
    from gwo_v8._canonical import canonical_bytes, load_canonical_json
    from gwo_v8.plan_control_github import _object_changes

    _control, memory, _gateway, _artifacts, _source, handle, classification = (
        _classified_control()
    )
    client = __import__(
        "test_v8_plancontrol_production",
        fromlist=["_RefContentClient"],
    )._RefContentClient()
    repository = _github_repository(client)
    for attempt in memory.attempts.values():
        repository.save_attempt(attempt)

    root = load_canonical_json(
        client._commits[client.head][".gwo-v8/plan-control-v3.json"].content
    )
    attempts_digest = root["categories"]["attempts"]
    attempts_object = load_canonical_json(
        repository._read_object_at_ref(client.head, attempts_digest)
    )
    attempts_object["items"] = [
        {
            key: value
            for key, value in item.items()
            if key != "planning_protocol_id"
        }
        for item in attempts_object["items"]
    ]
    replacement_digest, changes = _object_changes(
        repository.object_prefix,
        canonical_bytes(attempts_object),
    )
    root["categories"]["attempts"] = replacement_digest
    changes[repository.path] = canonical_bytes(root)
    client.compare_and_swap_ref(
        "owner/repository",
        "gwo-control",
        expected_ref_digest=client.head,
        changes=changes,
        message="test legacy v5 attempt fixture",
    )
    return client, handle, classification


def test_github_v5_hydration_infers_only_bound_legacy_attempt_protocols():
    from gwo_v8.planning_protocol import (
        PLANNING_OUTPUT_PROTOCOL_ID,
        REPLANNING_OUTPUT_PROTOCOL_ID,
    )

    client, handle, classification = _legacy_v5_attempt_fixture()
    repository = _github_repository(client)

    initial = repository.read_attempt(handle, None)
    successor = repository.read_attempt(handle, classification.plan_revision_digest)

    assert initial is not None
    assert initial.planning_protocol_id == PLANNING_OUTPUT_PROTOCOL_ID
    assert successor is not None
    assert successor.planning_protocol_id == REPLANNING_OUTPUT_PROTOCOL_ID


@pytest.mark.parametrize(
    "mutation",
    (
        "missing_digest",
        "missing_bytes",
        "digest_mismatch",
        "noncanonical_bytes",
        "wrong_schema",
        "wrong_fields",
    ),
)
def test_attempt_decoder_rejects_unbound_compilation_record(mutation):
    from test_v8_successor_plancontrol import _classified_control
    from gwo_v8._canonical import canonical_bytes, digest_bytes, load_canonical_json
    from gwo_v8.plan_control import PlanControlError
    from gwo_v8.plan_control_github import _attempt_from, _attempt_value

    _control, memory, _gateway, _artifacts, _source, handle, classification = (
        _classified_control()
    )
    attempt = memory.read_attempt(handle, classification.plan_revision_digest)
    assert attempt is not None
    value = _attempt_value(attempt)
    original_bytes = base64.b64decode(
        value["compilation_record_bytes_base64"],
        validate=True,
    )
    record = load_canonical_json(original_bytes)

    if mutation == "missing_digest":
        value["compilation_record_artifact_digest"] = None
    elif mutation == "missing_bytes":
        value["compilation_record_bytes_base64"] = None
    elif mutation == "digest_mismatch":
        value["compilation_record_artifact_digest"] = "0" * 64
    elif mutation == "noncanonical_bytes":
        noncanonical = original_bytes + b"\n"
        value["compilation_record_bytes_base64"] = base64.b64encode(
            noncanonical
        ).decode("ascii")
        value["compilation_record_artifact_digest"] = digest_bytes(noncanonical)
    elif mutation == "wrong_schema":
        record["schema_version"] = "gwo.plan.compilation.v1"
        rewritten = canonical_bytes(record)
        value["compilation_record_bytes_base64"] = base64.b64encode(
            rewritten
        ).decode("ascii")
        value["compilation_record_artifact_digest"] = digest_bytes(rewritten)
    else:
        del record["classification"]
        rewritten = canonical_bytes(record)
        value["compilation_record_bytes_base64"] = base64.b64encode(
            rewritten
        ).decode("ascii")
        value["compilation_record_artifact_digest"] = digest_bytes(rewritten)

    with pytest.raises(PlanControlError) as rejected:
        _attempt_from(value)

    assert rejected.value.code in {
        "DURABLE_STATE_INVALID",
        "COMPILATION_RECORD_INVALID",
    }


def test_save_invalidation_classification_rejects_malformed_caller_by_type():
    from test_v8_plancontrol_production import _RefContentClient
    from gwo_v8.plan_control import CampaignHandle, PlanControlError

    repository = _github_repository(_RefContentClient())
    handle = CampaignHandle("owner/repository", "campaign:malformed")

    with pytest.raises(PlanControlError) as rejected:
        repository.save_invalidation_classification(handle, object())

    assert rejected.value.code == "PLAN_INVALIDATION_CLASSIFICATION_INVALID"
