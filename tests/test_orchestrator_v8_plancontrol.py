from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import inspect
import json
from pathlib import Path
import sqlite3
from types import MappingProxyType
import sys
from threading import Barrier, RLock

import pytest


SCRIPTS = (
    Path(__file__).resolve().parents[1] / "skills" / "orchestrator" / "scripts"
)
sys.path.insert(0, str(SCRIPTS))

import gwo_v8  # noqa: E402
from gwo_v8 import (  # noqa: E402
    CampaignHandle,
    CampaignStartOptions,
    PlanControlDecision,
    PlanControlError,
    start,
)
from gwo_v8 import plan_control as pc  # noqa: E402
from gwo_v8 import _v3_github_control as gh3  # noqa: E402


REPOSITORY = "local/plancontrol"


def _contract(key: str) -> dict:
    return {
        "title": f"Ticket {key}",
        "body": f"## Contract\n\nDeliver and accept {key}.",
    }


def _policy() -> dict:
    core = {
        "schema_version": 1,
        "ref": "policy://local/v1",
        "authority_grants": {
            "campaign": [
                {
                    "operation_id": "repository.read.v1",
                    "resource_id": "campaign.snapshot.v1",
                }
            ],
            "worker": [
                {
                    "operation_id": "workspace.write.v1",
                    "resource_id": "work-run.workspace.v1",
                }
            ],
            "recovery_worker": [
                {
                    "operation_id": "workspace.write.v1",
                    "resource_id": "work-run.workspace.v1",
                }
            ],
            "review": [
                {
                    "operation_id": "repository.read.v1",
                    "resource_id": "review.subject.v1",
                }
            ],
        },
        "allowed_capabilities": ["git", "local_check"],
        "exclusive_resources": ["repository.release.v1"],
    }
    return {**core, "digest": pc._digest(pc._strict_json_bytes(core))}


def _ticket(key: str, *, blockers: tuple[dict, ...] = ()) -> dict:
    return {
        "key": key,
        "labels": ["ready-for-agent"],
        "source": {
            "ref": f"github://local/issues/{key}",
            "digest": f"digest:{key}",
        },
        "contract": _contract(key),
        "native_blockers": list(blockers),
    }


class _Source:
    def __init__(self, *tickets: dict, policy: dict | None = None):
        self.tickets = {ticket["key"]: ticket for ticket in tickets}
        self.policy = policy or _policy()
        self.calls = 0

    def snapshot(self, repository: str, ready_refs: tuple[str, ...]) -> dict:
        self.calls += 1
        return {
            "repository": repository,
            "target_branch": "main",
            "campaign_source": {
                "ref": "git://local/main",
                "digest": "target:main",
            },
            "policy": self.policy,
            "tickets": [self.tickets[key] for key in ready_refs],
        }


def _intent(*keys: str, findings: tuple[dict, ...] = ()) -> dict:
    return {
        "admitted_work": list(keys),
        "dependency_additions": [],
        "exclusive_resources": {},
        "capability_requirements": {
            key: ["git", "local_check"] for key in keys
        },
        "decision_requirements": list(findings),
    }


class _Planner:
    def __init__(self, intent: dict, *, mutate_after_return: bool = False):
        self.intent = intent
        self.calls = 0
        self.views: list[object] = []
        self.mutate_after_return = mutate_after_return

    def plan(self, snapshot, planning_action_id: str):
        self.calls += 1
        self.views.append(snapshot)
        assert planning_action_id.startswith("planning:")
        result = self.intent
        if self.mutate_after_return:
            result["capability_requirements"].clear()
        return result


class _FailingPlanner:
    def __init__(self, detail: str = "planner transport failed"):
        self.detail = detail
        self.calls = 0

    def plan(self, snapshot, planning_action_id: str):
        del snapshot, planning_action_id
        self.calls += 1
        raise RuntimeError(self.detail)


@dataclass
class _Writer:
    generation: str = "writer:v8"
    allowed: bool = True
    revision: int = 1

    def read(self, repository: str):
        value = {
            "repository": repository,
            "writer_generation": self.generation,
            "v8_start_allowed": self.allowed,
            "revision": self.revision,
        }
        return pc._WriterWitness(
            repository=repository,
            writer_generation=self.generation,
            v8_start_allowed=self.allowed,
            digest=pc._digest(pc._strict_json_bytes(value)),
        )


class _MemoryContent:
    def __init__(self):
        self._lock = RLock()
        self.values = {}
        self.cas_conflicts = 0
        self.repository_cas_conflicts = 0

    def read(self, repository, branch, path):
        with self._lock:
            value = self.values.get((repository, branch, path))
            if value is None:
                return None
            return pc._Content(value[0], value[1])

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
        del message
        with self._lock:
            key = (repository, branch, path)
            current = self.values.get(key)
            actual = None if current is None else current[1]
            if self.cas_conflicts:
                self.cas_conflicts -= 1
                raise RuntimeError("simulated CAS conflict")
            if "/repositories/" in path and self.repository_cas_conflicts:
                self.repository_cas_conflicts -= 1
                raise RuntimeError("simulated repository control CAS conflict")
            if actual != expected_blob_sha:
                raise RuntimeError("CAS conflict")
            sha = "blob:" + pc._digest(content)
            self.values[key] = (bytes(content), sha)
            return pc._Content(bytes(content), sha)


class _CheckpointCrash(RuntimeError):
    pass


class _CrashAt:
    def __init__(self, boundary: str):
        self.boundary = boundary
        self.seen: list[str] = []

    def __call__(self, boundary: str):
        self.seen.append(boundary)
        if boundary == self.boundary:
            raise _CheckpointCrash(boundary)


class _BarrierSource(_Source):
    def __init__(self, barrier: Barrier, *tickets: dict):
        super().__init__(*tickets)
        self.barrier = barrier

    def snapshot(self, repository: str, ready_refs: tuple[str, ...]) -> dict:
        value = super().snapshot(repository, ready_refs)
        self.barrier.wait(timeout=10)
        return value


def _control(
    tmp_path: Path,
    *,
    source: _Source,
    planner,
    content: _MemoryContent | None = None,
    writer: _Writer | None = None,
    journal_name: str = "v3.sqlite3",
    checkpoint=None,
    max_snapshot_bytes: int = 1_000_000,
):
    content = content or _MemoryContent()
    writer = writer or _Writer()
    return pc._PlanControl(
        source=source,
        planner=planner,
        journal=pc._SQLiteV3Journal(tmp_path / journal_name),
        durable=pc._GitHubV3Control(content),
        writer=writer,
        checkpoint=checkpoint,
        max_snapshot_bytes=max_snapshot_bytes,
    )


@pytest.fixture(autouse=True)
def _clear_production_factory():
    pc._install_production_factory(None)
    yield
    pc._install_production_factory(None)


def test_public_start_is_exact_and_v3_internals_stay_private(tmp_path):
    keys = tuple(f"issue:{number}" for number in range(1, 5))
    planner = _Planner(_intent(*keys))
    control = _control(
        tmp_path,
        source=_Source(*(_ticket(key) for key in keys)),
        planner=planner,
    )
    pc._install_production_factory(lambda: control)

    handle = start(
        REPOSITORY,
        keys,
        CampaignStartOptions(
            runtime_profile_overrides=(
                ("issue:1", "worker", "profile:fast"),
                ("issue:2", "review_primary", "profile:review"),
            )
        ),
    )

    assert inspect.signature(start).parameters.keys() == {
        "repository",
        "ready_refs",
        "options",
    }
    assert isinstance(handle, CampaignHandle)
    assert len(control._read_active(handle).plan_spec["work"]) == 4
    assert planner.calls == 1
    for internal in (
        "PlanControl",
        "CampaignSnapshot",
        "PlanRevision",
        "ActivationReceiptV3",
        "InMemoryCampaignSource",
        "InMemoryPlanningPass",
        "InMemoryPlanControlStore",
        "DecisionFinding",
    ):
        assert internal not in gwo_v8.__all__
        assert not hasattr(gwo_v8, internal)


def test_planner_gets_deep_immutable_bytes_and_cannot_mutate_compilation(tmp_path):
    key = "issue:1"
    source = _Source(_ticket(key))
    returned = _intent(key)
    planner = _Planner(returned)
    control = _control(tmp_path, source=source, planner=planner)

    handle = control.start(REPOSITORY, (key,))
    active_before = control._read_active(handle)

    assert isinstance(planner.views[0], MappingProxyType)
    assert isinstance(planner.views[0]["tickets"], tuple)
    assert isinstance(planner.views[0]["tickets"][0]["contract"], MappingProxyType)
    with pytest.raises(TypeError):
        planner.views[0]["tickets"][0]["contract"]["body"] = "evil"
    source.tickets[key]["contract"]["body"] = "evil source mutation"
    returned["admitted_work"].clear()
    returned["capability_requirements"][key].append("evil")

    active_after = control._read_active(handle)
    assert active_after.canonical_bytes == active_before.canonical_bytes
    assert active_after.plan_spec["work"][0]["contract"] == _contract(key)


@pytest.mark.parametrize("invalid", [float("nan"), float("inf"), object()])
def test_strict_canonical_json_rejects_non_json_and_non_finite_values(
    tmp_path,
    invalid,
):
    ticket = _ticket("issue:1")
    ticket["contract"]["body"] = invalid
    planner = _Planner(_intent("issue:1"))
    control = _control(tmp_path, source=_Source(ticket), planner=planner)

    with pytest.raises(PlanControlError) as rejected:
        control.start(REPOSITORY, ("issue:1",))

    assert rejected.value.code == "CANONICAL_JSON_INVALID"
    assert planner.calls == 0


@pytest.mark.parametrize(
    ("mutate", "detail"),
    [
        (
            lambda policy: policy.__setitem__("provider", "vendor"),
            "unsupported provider field",
        ),
        (
            lambda policy: policy.__setitem__("model", "model-name"),
            "unsupported model field",
        ),
        (
            lambda policy: policy.__setitem__("reasoning", "high"),
            "unsupported reasoning field",
        ),
        (
            lambda policy: policy.__setitem__("runtime_profile", "fast"),
            "unsupported Runtime field",
        ),
        (
            lambda policy: policy.__setitem__("configuration_source", "cli"),
            "unsupported configuration source",
        ),
        (
            lambda policy: policy.__setitem__("permission_flags", ["--all"]),
            "unsupported permission flags",
        ),
        (
            lambda policy: policy.__setitem__("digest", "0" * 64),
            "incorrect Policy Witness digest",
        ),
        (
            lambda policy: policy["authority_grants"]["worker"][0].__setitem__(
                "operation_id", "--all"
            ),
            "CLI-style authority flag",
        ),
        (
            lambda policy: policy["authority_grants"]["worker"][0].__setitem__(
                "operation_id", ""
            ),
            "blank authority identifier",
        ),
        (
            lambda policy: policy["authority_grants"]["worker"][0].__setitem__(
                "operation_id", "unknown.write.v1"
            ),
            "unknown authority root",
        ),
        (
            lambda policy: policy["authority_grants"]["worker"][0].__setitem__(
                "operation_id", "workspace.write"
            ),
            "unversioned authority identifier",
        ),
        (
            lambda policy: policy["allowed_capabilities"].__setitem__(
                0, "--danger"
            ),
            "CLI-style allowed fact",
        ),
        (
            lambda policy: policy["exclusive_resources"].__setitem__(
                0, "unknown.release.v1"
            ),
            "unknown exclusive-resource root",
        ),
    ],
)
def test_policy_witness_is_a_strict_digest_bound_whitelist(
    tmp_path,
    mutate,
    detail,
):
    del detail
    policy = _policy()
    mutate(policy)
    planner = _Planner(_intent("issue:1"))
    control = _control(
        tmp_path,
        source=_Source(_ticket("issue:1"), policy=policy),
        planner=planner,
    )

    with pytest.raises(PlanControlError) as rejected:
        control.start(REPOSITORY, ("issue:1",))

    assert rejected.value.code == "POLICY_WITNESS_INVALID"
    assert planner.calls == 0


@pytest.mark.parametrize(
    "options",
    [
        {"campaign_key": "caller-controlled"},
        {"expected_previous_revision_digest": "0" * 64},
        {"provider": "vendor"},
        {"model": "model-name"},
        {"reasoning": "high"},
        {"permission_flags": ["--all"]},
        {"runtime_profile_overrides": [("issue:1", "worker", "--flag")]},
        {"runtime_profile_overrides": [("issue:1", "campaign", "profile:one")]},
        {
            "runtime_profile_overrides": [
                ("issue:1", "specialist:unversioned", "profile:one")
            ]
        },
    ],
)
def test_public_options_reject_control_and_provider_specific_knobs(
    tmp_path,
    options,
):
    planner = _Planner(_intent("issue:1"))
    control = _control(
        tmp_path,
        source=_Source(_ticket("issue:1")),
        planner=planner,
    )

    with pytest.raises(PlanControlError) as rejected:
        control.start(REPOSITORY, ("issue:1",), options)

    assert rejected.value.code == "START_OPTIONS_INVALID"
    assert planner.calls == 0


def test_runtime_profile_overrides_are_durable_facts_not_planspec_fields(
    tmp_path,
):
    key = "issue:1"
    content = _MemoryContent()
    control = _control(
        tmp_path,
        source=_Source(_ticket(key)),
        planner=_Planner(_intent(key)),
        content=content,
    )
    options = CampaignStartOptions(
        runtime_profile_overrides=(
            (key, "worker", "profile:fast"),
            (key, "specialist:artifact.render.v1", "profile:specialist"),
        )
    )

    handle = control.start(REPOSITORY, (key,), options)

    campaign = control.durable.campaign(handle)
    runtime_digest = campaign["runtime_facts_digest"]
    runtime_bytes = control.durable.read_artifact(
        REPOSITORY, "runtime-facts", runtime_digest
    )
    assert json.loads(runtime_bytes) == options.as_value()
    plan = control._read_active(handle).plan_spec
    rendered = json.dumps(plan, sort_keys=True)
    assert "profile:fast" not in rendered
    assert "profile:specialist" not in rendered
    assert "runtime" not in rendered.lower()
    assert set(plan) == {
        "schema_version",
        "repository",
        "target_branch",
        "campaign",
        "policy",
        "work",
    }


@pytest.mark.parametrize(
    ("boundary", "decision_expected", "calls_before_retry"),
    [
        ("SNAPSHOTTED", False, 0),
        ("CLAIMS_RESERVED", False, 0),
        ("PLANNING_STARTED", True, 0),
        ("PLANNING_REPLY_RECEIVED", True, 1),
        ("INTENT_ACCEPTED", False, 1),
        ("PLAN_ARTIFACT_PUBLISHED", False, 1),
        ("PLAN_READ_BACK", False, 1),
        ("PLAN_PUBLISHED", False, 1),
        ("ACTIVATION_COMMITTED", False, 1),
        ("ACTIVATION_RECEIPT_READ_BACK", False, 1),
    ],
)
def test_restart_boundaries_are_idempotent_and_never_repeat_planning(
    tmp_path,
    boundary,
    decision_expected,
    calls_before_retry,
):
    key = "issue:1"
    source = _Source(_ticket(key))
    planner = _Planner(_intent(key))
    content = _MemoryContent()
    crash = _CrashAt(boundary)
    first = _control(
        tmp_path,
        source=source,
        planner=planner,
        content=content,
        checkpoint=crash,
    )

    with pytest.raises(_CheckpointCrash):
        first.start(REPOSITORY, (key,))

    assert planner.calls == calls_before_retry
    resumed = _control(
        tmp_path,
        source=source,
        planner=planner,
        content=content,
    )
    if decision_expected:
        with pytest.raises(PlanControlDecision) as first_decision:
            resumed.start(REPOSITORY, (key,))
        with pytest.raises(PlanControlDecision) as repeated_decision:
            resumed.start(REPOSITORY, (key,))
        assert first_decision.value.decision_digest == (
            repeated_decision.value.decision_digest
        )
        assert first_decision.value.findings == repeated_decision.value.findings
        assert [item.code for item in first_decision.value.findings] == [
            "PLANNING_AMBIGUOUS"
        ]
        assert planner.calls == calls_before_retry
        assert not any("/plans/" in path for _, _, path in content.values)
    else:
        handle = resumed.start(REPOSITORY, (key,))
        assert resumed._read_active(handle).receipt.planning_action_id.startswith(
            "planning:"
        )
        assert planner.calls == 1
        with sqlite3.connect(tmp_path / "v3.sqlite3") as connection:
            state = connection.execute(
                "SELECT state FROM v3_campaign_journal"
            ).fetchone()[0]
        assert state == "ACTIVE_LOCAL"


def test_repository_global_claim_cas_blocks_overlap_before_loser_planning(
    tmp_path,
):
    content = _MemoryContent()
    barrier = Barrier(2)
    left_planner = _Planner(_intent("issue:1", "issue:2"))
    right_planner = _Planner(_intent("issue:2", "issue:3"))
    left = _control(
        tmp_path,
        source=_BarrierSource(
            barrier, _ticket("issue:1"), _ticket("issue:2")
        ),
        planner=left_planner,
        content=content,
        journal_name="left.sqlite3",
    )
    right = _control(
        tmp_path,
        source=_BarrierSource(
            barrier, _ticket("issue:2"), _ticket("issue:3")
        ),
        planner=right_planner,
        content=content,
        journal_name="right.sqlite3",
    )

    def run(control, refs, campaign_key):
        try:
            return control.start(
                REPOSITORY, refs, _campaign_key=campaign_key
            )
        except PlanControlDecision as decision:
            return decision

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = tuple(
            future.result(timeout=20)
            for future in (
                pool.submit(
                    run,
                    left,
                    ("issue:1", "issue:2"),
                    "campaign:left",
                ),
                pool.submit(
                    run,
                    right,
                    ("issue:2", "issue:3"),
                    "campaign:right",
                ),
            )
        )

    handles = [item for item in outcomes if isinstance(item, CampaignHandle)]
    decisions = [
        item for item in outcomes if isinstance(item, PlanControlDecision)
    ]
    assert len(handles) == 1
    assert len(decisions) == 1
    assert decisions[0].findings == (
        pc.DecisionFinding(
            code="TICKET_CLAIM_CONFLICT",
            detail="Ticket is claimed by another Campaign",
            ticket_key="issue:2",
        ),
    )
    assert left_planner.calls + right_planner.calls == 1
    plan_paths = [
        path for _, _, path in content.values if "/plans/" in path
    ]
    assert len(plan_paths) == 1


def test_disjoint_claims_retry_repository_cas_and_both_plan_once(tmp_path):
    content = _MemoryContent()
    content.repository_cas_conflicts = 1
    barrier = Barrier(2)
    left_planner = _Planner(_intent("issue:1"))
    right_planner = _Planner(_intent("issue:2"))
    left = _control(
        tmp_path,
        source=_BarrierSource(barrier, _ticket("issue:1")),
        planner=left_planner,
        content=content,
        journal_name="left.sqlite3",
    )
    right = _control(
        tmp_path,
        source=_BarrierSource(barrier, _ticket("issue:2")),
        planner=right_planner,
        content=content,
        journal_name="right.sqlite3",
    )

    with ThreadPoolExecutor(max_workers=2) as pool:
        handles = tuple(
            future.result(timeout=20)
            for future in (
                pool.submit(
                    left.start,
                    REPOSITORY,
                    ("issue:1",),
                    None,
                    _campaign_key="campaign:left",
                ),
                pool.submit(
                    right.start,
                    REPOSITORY,
                    ("issue:2",),
                    None,
                    _campaign_key="campaign:right",
                ),
            )
        )

    assert {handle.campaign_key for handle in handles} == {
        "campaign:left",
        "campaign:right",
    }
    assert left_planner.calls == right_planner.calls == 1
    control_value, _ = left.durable._read_control(REPOSITORY)
    assert set(control_value["claims"]) == {"issue:1", "issue:2"}
    assert {claim["state"] for claim in control_value["claims"].values()} == {
        "active"
    }


def test_decision_aggregates_all_sorted_findings_and_retries_exact_bytes(
    tmp_path,
):
    key = "issue:1"
    planner = _Planner(
        _intent(
            key,
            findings=(
                {"code": "Z_LAST", "detail": "last", "ticket_key": key},
                {"code": "A_FIRST", "detail": "first"},
                {"code": "M_MIDDLE", "detail": "middle", "ticket_key": key},
            ),
        )
    )
    control = _control(
        tmp_path,
        source=_Source(_ticket(key)),
        planner=planner,
    )

    with pytest.raises(PlanControlDecision) as first:
        control.start(REPOSITORY, (key,))
    with pytest.raises(PlanControlDecision) as repeated:
        control.start(REPOSITORY, (key,))

    decision = first.value
    assert decision.repository == REPOSITORY
    assert decision.campaign_key.startswith("campaign:")
    assert len(decision.snapshot_digest) == 64
    assert decision.planning_action_id.startswith("planning:")
    assert [finding.code for finding in decision.findings] == [
        "A_FIRST",
        "M_MIDDLE",
        "Z_LAST",
    ]
    assert decision.findings == repeated.value.findings
    assert decision.decision_digest == repeated.value.decision_digest
    assert planner.calls == 1


def test_semantically_invalid_planning_reply_uses_typed_decision_boundary(
    tmp_path,
):
    key = "issue:1"
    invalid = _intent(key)
    invalid["admitted_work"] = []
    planner = _Planner(invalid)
    control = _control(
        tmp_path,
        source=_Source(_ticket(key)),
        planner=planner,
    )

    with pytest.raises(PlanControlDecision) as rejected:
        control.start(REPOSITORY, (key,))

    assert [finding.code for finding in rejected.value.findings] == [
        "PLAN_INTENT_OMISSION"
    ]
    assert planner.calls == 1


def test_planning_exception_is_durable_ambiguity_and_never_reinvoked(tmp_path):
    key = "issue:1"
    planner = _FailingPlanner()
    content = _MemoryContent()
    control = _control(
        tmp_path,
        source=_Source(_ticket(key)),
        planner=planner,
        content=content,
    )

    with pytest.raises(PlanControlDecision) as first:
        control.start(REPOSITORY, (key,))
    with pytest.raises(PlanControlDecision) as repeated:
        control.start(REPOSITORY, (key,))

    assert [finding.code for finding in first.value.findings] == [
        "PLANNING_AMBIGUOUS"
    ]
    assert first.value.decision_digest == repeated.value.decision_digest
    assert planner.calls == 1
    durable, _ = control.durable._read_control(REPOSITORY)
    assert durable["claims"][key]["state"] == "pending"
    assert durable["campaigns"][first.value.campaign_key]["state"] == (
        "PLANNING_AMBIGUOUS"
    )
    assert not hasattr(control.durable, "abandon")
    assert hasattr(gh3.PendingClaimAbandonmentAuthority, "authorize")


def test_oversize_snapshot_is_durable_typed_split_decision(tmp_path):
    key = "issue:1"
    planner = _Planner(_intent(key))
    content = _MemoryContent()
    control = _control(
        tmp_path,
        source=_Source(_ticket(key)),
        planner=planner,
        content=content,
        max_snapshot_bytes=1,
    )

    with pytest.raises(PlanControlDecision) as first:
        control.start(REPOSITORY, (key,))
    with pytest.raises(PlanControlDecision) as repeated:
        control.start(REPOSITORY, (key,))

    assert [finding.code for finding in first.value.findings] == [
        "SPLIT_CAMPAIGN_REQUIRED"
    ]
    assert first.value.decision_digest == repeated.value.decision_digest
    assert planner.calls == 0
    assert not any("/plans/" in path for _, _, path in content.values)


def test_writer_authority_disallow_fails_closed_before_claims_or_planning(
    tmp_path,
):
    key = "issue:1"
    planner = _Planner(_intent(key))
    content = _MemoryContent()
    control = _control(
        tmp_path,
        source=_Source(_ticket(key)),
        planner=planner,
        content=content,
        writer=_Writer(allowed=False),
    )

    with pytest.raises(PlanControlError) as rejected:
        control.start(REPOSITORY, (key,))

    assert rejected.value.code == "WRITER_AUTHORITY_NOT_READY"
    assert planner.calls == 0
    durable, _ = control.durable._read_control(REPOSITORY)
    assert durable["campaigns"] == {}
    assert durable["claims"] == {}


def test_writer_witness_change_before_activation_is_a_durable_decision(
    tmp_path,
):
    key = "issue:1"
    writer = _Writer()
    planner = _Planner(_intent(key))

    def change_writer(boundary: str):
        if boundary == "PLAN_READ_BACK":
            writer.revision += 1

    control = _control(
        tmp_path,
        source=_Source(_ticket(key)),
        planner=planner,
        writer=writer,
        checkpoint=change_writer,
    )

    with pytest.raises(PlanControlDecision) as first:
        control.start(REPOSITORY, (key,))
    with pytest.raises(PlanControlDecision) as repeated:
        control.start(REPOSITORY, (key,))

    assert [finding.code for finding in first.value.findings] == [
        "WRITER_WITNESS_CHANGED"
    ]
    assert first.value.decision_digest == repeated.value.decision_digest
    assert planner.calls == 1


def test_writer_witness_change_across_restart_preserves_reserved_witness(
    tmp_path,
):
    key = "issue:1"
    writer = _Writer()
    planner = _Planner(_intent(key))
    content = _MemoryContent()
    first = _control(
        tmp_path,
        source=_Source(_ticket(key)),
        planner=planner,
        writer=writer,
        content=content,
        checkpoint=_CrashAt("PLAN_PUBLISHED"),
    )
    with pytest.raises(_CheckpointCrash):
        first.start(REPOSITORY, (key,))
    writer.revision += 1
    resumed = _control(
        tmp_path,
        source=_Source(_ticket(key)),
        planner=planner,
        writer=writer,
        content=content,
    )

    with pytest.raises(PlanControlDecision) as rejected:
        resumed.start(REPOSITORY, (key,))

    assert [finding.code for finding in rejected.value.findings] == [
        "WRITER_WITNESS_CHANGED"
    ]
    assert planner.calls == 1


def _tamper_artifact(content: _MemoryContent, kind: str) -> None:
    key = next(
        key for key in content.values if f"/{kind}/" in key[2]
    )
    tampered = pc._strict_json_bytes({"tampered": kind})
    content.values[key] = (tampered, "blob:" + pc._digest(tampered))


@pytest.mark.parametrize("location", ["journal", "durable"])
def test_snapshot_tamper_is_detected_before_planner(
    tmp_path,
    location,
):
    key = "issue:1"
    planner = _Planner(_intent(key))
    content = _MemoryContent()
    first = _control(
        tmp_path,
        source=_Source(_ticket(key)),
        planner=planner,
        content=content,
        checkpoint=_CrashAt("CLAIMS_RESERVED"),
    )
    with pytest.raises(_CheckpointCrash):
        first.start(REPOSITORY, (key,))

    if location == "journal":
        tampered = pc._strict_json_bytes({"tampered": "snapshot"})
        with sqlite3.connect(tmp_path / "v3.sqlite3") as connection:
            connection.execute(
                "UPDATE v3_campaign_journal SET snapshot_bytes = ?",
                (tampered,),
            )
    else:
        _tamper_artifact(content, "snapshots")
    resumed = _control(
        tmp_path,
        source=_Source(_ticket(key)),
        planner=planner,
        content=content,
    )

    with pytest.raises(PlanControlError) as rejected:
        resumed.start(REPOSITORY, (key,))

    assert rejected.value.code in {
        "SNAPSHOT_DIGEST_MISMATCH",
        "DURABLE_ARTIFACT_READBACK_MISMATCH",
    }
    assert planner.calls == 0


@pytest.mark.parametrize("location", ["journal", "durable"])
def test_intent_tamper_is_detected_without_reinvoking_planner(
    tmp_path,
    location,
):
    key = "issue:1"
    planner = _Planner(_intent(key))
    content = _MemoryContent()
    first = _control(
        tmp_path,
        source=_Source(_ticket(key)),
        planner=planner,
        content=content,
        checkpoint=_CrashAt("INTENT_ACCEPTED"),
    )
    with pytest.raises(_CheckpointCrash):
        first.start(REPOSITORY, (key,))

    if location == "journal":
        tampered = pc._strict_json_bytes({"tampered": "intent"})
        with sqlite3.connect(tmp_path / "v3.sqlite3") as connection:
            connection.execute(
                "UPDATE v3_campaign_journal SET intent_bytes = ?",
                (tampered,),
            )
    else:
        _tamper_artifact(content, "intents")
    resumed = _control(
        tmp_path,
        source=_Source(_ticket(key)),
        planner=planner,
        content=content,
    )

    with pytest.raises(PlanControlError) as rejected:
        resumed.start(REPOSITORY, (key,))

    assert rejected.value.code in {
        "PLAN_INTENT_READBACK_MISMATCH",
        "DURABLE_ARTIFACT_READBACK_MISMATCH",
    }
    assert planner.calls == 1


def test_published_plan_tamper_blocks_activation_without_replanning(tmp_path):
    key = "issue:1"
    planner = _Planner(_intent(key))
    content = _MemoryContent()
    first = _control(
        tmp_path,
        source=_Source(_ticket(key)),
        planner=planner,
        content=content,
        checkpoint=_CrashAt("PLAN_PUBLISHED"),
    )
    with pytest.raises(_CheckpointCrash):
        first.start(REPOSITORY, (key,))
    _tamper_artifact(content, "plans")
    resumed = _control(
        tmp_path,
        source=_Source(_ticket(key)),
        planner=planner,
        content=content,
    )

    with pytest.raises(PlanControlError) as rejected:
        resumed.start(REPOSITORY, (key,))

    assert rejected.value.code == "DURABLE_ARTIFACT_READBACK_MISMATCH"
    assert planner.calls == 1


def test_receipt_artifact_tamper_blocks_rollforward(tmp_path):
    key = "issue:1"
    planner = _Planner(_intent(key))
    content = _MemoryContent()
    first = _control(
        tmp_path,
        source=_Source(_ticket(key)),
        planner=planner,
        content=content,
        checkpoint=_CrashAt("ACTIVATION_COMMITTED"),
    )
    with pytest.raises(_CheckpointCrash):
        first.start(REPOSITORY, (key,))
    _tamper_artifact(content, "receipts")
    resumed = _control(
        tmp_path,
        source=_Source(_ticket(key)),
        planner=planner,
        content=content,
    )

    with pytest.raises(PlanControlError) as rejected:
        resumed.start(REPOSITORY, (key,))

    assert rejected.value.code == "DURABLE_ARTIFACT_READBACK_MISMATCH"
    assert planner.calls == 1


def test_local_receipt_identity_tamper_is_rejected_by_active_readback(
    tmp_path,
):
    key = "issue:1"
    control = _control(
        tmp_path,
        source=_Source(_ticket(key)),
        planner=_Planner(_intent(key)),
    )
    handle = control.start(REPOSITORY, (key,))
    receipt = control._read_active(handle).receipt.as_value()
    receipt["campaign_key"] = "campaign:tampered"
    receipt_bytes = pc._strict_json_bytes(receipt)
    with sqlite3.connect(tmp_path / "v3.sqlite3") as connection:
        connection.execute(
            """
            UPDATE v3_active_campaigns
            SET receipt_bytes = ?, receipt_digest = ?
            """,
            (receipt_bytes, pc._digest(receipt_bytes)),
        )

    with pytest.raises(PlanControlError) as rejected:
        control._read_active(handle)

    assert rejected.value.code == "ACTIVATION_RECEIPT_INVALID"


def test_pending_claim_readback_tamper_blocks_planner(tmp_path):
    key = "issue:1"
    planner = _Planner(_intent(key))
    content = _MemoryContent()
    first = _control(
        tmp_path,
        source=_Source(_ticket(key)),
        planner=planner,
        content=content,
        checkpoint=_CrashAt("CLAIMS_RESERVED"),
    )
    with pytest.raises(_CheckpointCrash):
        first.start(REPOSITORY, (key,))
    durable, sha = first.durable._read_control(REPOSITORY)
    durable["claims"][key]["state"] = "active"
    path = first.durable._repository_path(REPOSITORY)
    tampered = pc._strict_json_bytes(durable)
    content.values[(REPOSITORY, first.durable.branch, path)] = (
        tampered,
        sha,
    )
    resumed = _control(
        tmp_path,
        source=_Source(_ticket(key)),
        planner=planner,
        content=content,
    )

    with pytest.raises(PlanControlError) as rejected:
        resumed.start(REPOSITORY, (key,))

    assert rejected.value.code == "CLAIM_RESERVATION_READBACK_MISMATCH"
    assert planner.calls == 0


def test_committed_receipt_rolls_forward_into_an_empty_local_journal(
    tmp_path,
):
    key = "issue:1"
    planner = _Planner(_intent(key))
    content = _MemoryContent()
    first = _control(
        tmp_path,
        source=_Source(_ticket(key)),
        planner=planner,
        content=content,
        journal_name="first.sqlite3",
    )
    handle = first.start(REPOSITORY, (key,))
    receipt = first._read_active(handle).receipt
    resumed = _control(
        tmp_path,
        source=_Source(_ticket(key)),
        planner=planner,
        content=content,
        journal_name="rebuilt.sqlite3",
    )

    rebuilt_handle = resumed.start(REPOSITORY, (key,))

    assert rebuilt_handle == handle
    assert resumed._read_active(handle).receipt == receipt
    assert planner.calls == 1


def test_successor_revision_uses_private_cas_seam_and_stable_handle(tmp_path):
    key = "issue:1"
    source = _Source(_ticket(key))
    planner = _Planner(_intent(key))
    control = _control(tmp_path, source=source, planner=planner)
    first_handle = control.start(
        REPOSITORY, (key,), _campaign_key="campaign:stable"
    )
    first = control._read_active(first_handle)
    source.tickets[key]["contract"]["body"] = "Successor frozen contract."

    second_handle = control.start(
        REPOSITORY, (key,), _campaign_key="campaign:stable"
    )

    second = control._read_active(second_handle)
    assert second_handle == first_handle
    assert second.revision.digest != first.revision.digest
    assert second.receipt.expected_previous_revision_digest == (
        first.revision.digest
    )
    assert planner.calls == 2
    with pytest.raises(PlanControlError) as conflict:
        source.tickets[key]["contract"]["body"] = "Third contract."
        control.start(
            REPOSITORY,
            (key,),
            _campaign_key="campaign:stable",
            _expected_previous_revision_digest="0" * 64,
        )
    assert conflict.value.code == "ACTIVATION_CAS_CONFLICT"
    assert planner.calls == 2


def test_pending_campaign_identity_cannot_be_replaced_by_new_snapshot(
    tmp_path,
):
    key = "issue:1"
    source = _Source(_ticket(key))
    planner = _Planner(_intent(key))
    content = _MemoryContent()
    first = _control(
        tmp_path,
        source=source,
        planner=planner,
        content=content,
        checkpoint=_CrashAt("CLAIMS_RESERVED"),
    )
    with pytest.raises(_CheckpointCrash):
        first.start(
            REPOSITORY, (key,), _campaign_key="campaign:stable"
        )
    source.tickets[key]["contract"]["body"] = "Unreserved replacement."
    resumed = _control(
        tmp_path,
        source=source,
        planner=planner,
        content=content,
    )

    with pytest.raises(PlanControlError) as rejected:
        resumed.start(
            REPOSITORY, (key,), _campaign_key="campaign:stable"
        )

    assert rejected.value.code == "CAMPAIGN_IN_PROGRESS_CONFLICT"
    assert planner.calls == 0


def test_source_and_planner_return_mutation_after_copy_cannot_change_plan(
    tmp_path,
):
    key = "issue:1"
    source = _Source(_ticket(key))
    returned = _intent(key)
    planner = _Planner(returned)

    def mutate_after_boundaries(boundary: str):
        if boundary == "SNAPSHOTTED":
            source.tickets[key]["contract"]["body"] = "late source mutation"
        if boundary == "PLANNING_REPLY_RECEIVED":
            returned["admitted_work"].clear()
            returned["capability_requirements"][key].clear()

    control = _control(
        tmp_path,
        source=source,
        planner=planner,
        checkpoint=mutate_after_boundaries,
    )

    handle = control.start(REPOSITORY, (key,))
    work = control._read_active(handle).plan_spec["work"][0]

    assert work["contract"] == _contract(key)
    assert work["capabilities"] == [
        "git",
        "local_check",
    ]


def test_snapshot_is_re_read_after_untrusted_planner_returns(tmp_path):
    key = "issue:1"

    class JournalTamperingPlanner(_Planner):
        def plan(self, snapshot, planning_action_id: str):
            result = super().plan(snapshot, planning_action_id)
            tampered = pc._strict_json_bytes({"tampered": "during planning"})
            with sqlite3.connect(tmp_path / "v3.sqlite3") as connection:
                connection.execute(
                    "UPDATE v3_campaign_journal SET snapshot_bytes = ?",
                    (tampered,),
                )
            return result

    planner = JournalTamperingPlanner(_intent(key))
    content = _MemoryContent()
    control = _control(
        tmp_path,
        source=_Source(_ticket(key)),
        planner=planner,
        content=content,
    )

    with pytest.raises(PlanControlError) as rejected:
        control.start(REPOSITORY, (key,))

    assert rejected.value.code == "SNAPSHOT_DIGEST_MISMATCH"
    assert planner.calls == 1
    assert not any("/intents/" in path for _, _, path in content.values)
    assert not any("/plans/" in path for _, _, path in content.values)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda plan: plan.__setitem__("provider", "vendor"),
        lambda plan: plan["campaign"].__setitem__("model", "model-name"),
        lambda plan: plan["policy"].__setitem__("reasoning", "high"),
        lambda plan: plan["work"][0].__setitem__(
            "runtime_profile", "profile:fast"
        ),
        lambda plan: plan["work"][0].__setitem__(
            "configuration_source", "cli"
        ),
        lambda plan: plan["work"][0].__setitem__(
            "permission_flags", ["--all"]
        ),
        lambda plan: plan["work"][0]["authority"]["worker"].__setitem__(
            "subtree_digest", "0" * 64
        ),
        lambda plan: plan["work"][0]["authority"]["worker"]["grants"][
            0
        ].__setitem__("operation_id", "unknown.write.v1"),
    ],
)
def test_planspec_v3_validator_is_a_complete_whitelist(tmp_path, mutate):
    key = "issue:1"
    control = _control(
        tmp_path,
        source=_Source(_ticket(key)),
        planner=_Planner(_intent(key)),
    )
    handle = control.start(REPOSITORY, (key,))
    plan = control._read_active(handle).plan_spec
    mutate(plan)

    with pytest.raises(PlanControlError):
        pc._validate_plan_spec(pc._strict_json_bytes(plan))


def test_v3_plancontrol_uses_only_v3_tables_and_never_calls_v2_compiler(
    tmp_path,
    monkeypatch,
):
    def forbidden_v2_compile(*args, **kwargs):
        del args, kwargs
        raise AssertionError("V2 compiler was called")

    monkeypatch.setattr(gwo_v8.PlanCompiler, "compile", forbidden_v2_compile)
    key = "issue:1"
    control = _control(
        tmp_path,
        source=_Source(_ticket(key)),
        planner=_Planner(_intent(key)),
    )

    control.start(REPOSITORY, (key,))

    with sqlite3.connect(tmp_path / "v3.sqlite3") as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
    assert tables == {"v3_campaign_journal", "v3_active_campaigns"}
    module_root = (
        SCRIPTS / "gwo_v8"
    )
    sources = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (
            module_root / "plan_control.py",
            module_root / "_v3_canonical.py",
            module_root / "_v3_journal.py",
            module_root / "_v3_github_control.py",
            module_root / "_v3_composition.py",
            module_root / "_v3_plan_spec.py",
            module_root / "_v3_types.py",
        )
    )
    assert "from .compiler" not in sources
    assert "from .activation" not in sources
    assert "v2_" not in sources.lower()


def test_root_start_requires_private_real_plancontrol_composition():
    with pytest.raises(PlanControlError) as missing:
        start(REPOSITORY, ("issue:1",))
    assert missing.value.code == "PLAN_CONTROL_NOT_CONFIGURED"

    pc._install_production_factory(lambda: object())
    with pytest.raises(PlanControlError) as invalid:
        start(REPOSITORY, ("issue:1",))
    assert invalid.value.code == "PLAN_CONTROL_NOT_CONFIGURED"
