from __future__ import annotations

import json
from pathlib import Path
import sys

import pytest


SCRIPTS = (
    Path(__file__).resolve().parents[1] / "skills" / "orchestrator" / "scripts"
)
sys.path.insert(0, str(SCRIPTS))

from gwo_v8 import (  # noqa: E402
    CampaignHandle,
    CampaignStartOptions,
    InMemoryCampaignSource,
    InMemoryPlanControlStore,
    InMemoryPlanningPass,
    PlanControl,
    PlanControlDecision,
    PlanControlError,
    PlanCompiler,
    start,
)


def _contract(key: str) -> dict:
    return {
        "title": f"Ticket {key}",
        "behavior": f"Deliver behavior for {key}",
        "acceptance": [f"{key} succeeds"],
    }


def _policy() -> dict:
    return {
        "ref": "policy://local/v1",
        "content": {"version": 1, "name": "local policy"},
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


def _ticket(key: str, *, blockers: tuple[dict, ...] = ()) -> dict:
    return {
        "key": key,
        "labels": ["ready-for-agent"],
        "source": {"ref": f"github://local/issues/{key}", "digest": f"{key}-digest"},
        "contract": _contract(key),
        "native_blockers": list(blockers),
    }


def _source(*tickets: dict) -> InMemoryCampaignSource:
    return InMemoryCampaignSource(
        repository="local/plancontrol",
        target_branch="main",
        campaign_source={"ref": "git://local/main", "digest": "main-sha"},
        policy=_policy(),
        tickets={ticket["key"]: ticket for ticket in tickets},
    )


def _intent(*keys: str) -> dict:
    return {
        "admitted_work": list(keys),
        "dependency_additions": [],
        "exclusive_resources": {},
        "capability_requirements": {key: ["git", "local_check"] for key in keys},
        "decision_requirements": [],
    }


def test_start_compiles_four_ready_tickets_into_one_handle_and_v3_revision():
    keys = tuple(f"issue:{number}" for number in range(1, 5))
    source = _source(*(_ticket(key) for key in keys))
    planner = InMemoryPlanningPass(_intent(*keys))
    store = InMemoryPlanControlStore()

    handle = start(
        "local/plancontrol",
        keys,
        control=PlanControl(source=source, planner=planner, store=store),
    )

    active = store.read_active(handle)
    assert handle.repository == "local/plancontrol"
    assert len(active.plan_spec["work"]) == 4
    assert active.plan_spec["schema_version"] == 3
    assert planner.calls == 1
    assert store.claimed_ticket_keys(handle) == frozenset(keys)
    assert active.digest == __import__("hashlib").sha256(active.canonical_bytes).hexdigest()
    assert json.loads(active.canonical_bytes) == active.plan_spec


@pytest.mark.parametrize(
    ("mutate", "code"),
    [
        (lambda tickets: tickets[0].update(labels=["needs-info"]), "TICKET_LABEL_INVALID"),
        (lambda tickets: tickets[0].update(contract={}), "TICKET_CONTRACT_MISSING"),
        (
            lambda tickets: tickets.__setitem__(
                0,
                _ticket("issue:1", blockers=({"key": "issue:2", "state": "open"},)),
            )
            or tickets.__setitem__(
                1,
                _ticket("issue:2", blockers=({"key": "issue:1", "state": "open"},)),
            ),
            "DEPENDENCY_CYCLE",
        ),
    ],
)
def test_start_fails_closed_before_planning_for_invalid_complete_snapshot(mutate, code):
    tickets = [_ticket("issue:1"), _ticket("issue:2")]
    mutate(tickets)
    source = _source(*tickets)
    planner = InMemoryPlanningPass(_intent("issue:1", "issue:2"))

    with pytest.raises(PlanControlError) as rejected:
        PlanControl(source=source, planner=planner, store=InMemoryPlanControlStore()).start(
            "local/plancontrol", ("issue:1", "issue:2")
        )

    assert rejected.value.code == code
    assert planner.calls == 0


def test_start_returns_typed_split_decision_without_truncating_or_planning():
    source = _source(_ticket("issue:1"))
    planner = InMemoryPlanningPass(_intent("issue:1"))

    with pytest.raises(PlanControlDecision) as deferred:
        PlanControl(
            source=source,
            planner=planner,
            store=InMemoryPlanControlStore(),
            max_snapshot_bytes=1,
        ).start("local/plancontrol", ("issue:1",))

    assert deferred.value.code == "SPLIT_CAMPAIGN_REQUIRED"
    assert deferred.value.decision.actual_bytes > deferred.value.decision.max_bytes
    assert planner.calls == 0


def test_snapshot_omission_and_planning_omission_both_fail_closed():
    source = _source(_ticket("issue:1"), _ticket("issue:2"))

    class OmittedSource:
        def snapshot(self, repository, ready_refs):
            value = source.snapshot(repository, ready_refs)
            value["tickets"] = value["tickets"][:1]
            return value

    omitted_planner = InMemoryPlanningPass(_intent("issue:1", "issue:2"))
    with pytest.raises(PlanControlError) as omitted_snapshot:
        PlanControl(
            source=OmittedSource(), planner=omitted_planner, store=InMemoryPlanControlStore()
        ).start("local/plancontrol", ("issue:1", "issue:2"))
    assert omitted_snapshot.value.code == "SNAPSHOT_OMISSION"
    assert omitted_planner.calls == 0

    omission_intent = _intent("issue:1")
    with pytest.raises(PlanControlError) as omitted_work:
        PlanControl(
            source=source,
            planner=InMemoryPlanningPass(omission_intent),
            store=InMemoryPlanControlStore(),
        ).start("local/plancontrol", ("issue:1", "issue:2"))
    assert omitted_work.value.code == "PLAN_INTENT_OMISSION"


def test_validated_planning_intent_is_reused_after_retry_and_crash():
    source = _source(_ticket("issue:1"))
    planner = InMemoryPlanningPass(_intent("issue:1"))
    store = InMemoryPlanControlStore()
    crashed = False

    def crash_once(boundary: str) -> None:
        nonlocal crashed
        if boundary == "activation_cas" and not crashed:
            crashed = True
            raise RuntimeError("simulated process loss")

    with pytest.raises(RuntimeError):
        PlanControl(
            source=source, planner=planner, store=store, checkpoint=crash_once
        ).start("local/plancontrol", ("issue:1",))

    handle = PlanControl(source=source, planner=planner, store=store).start(
        "local/plancontrol", ("issue:1",)
    )
    assert PlanControl(source=source, planner=planner, store=store).start(
        "local/plancontrol", ("issue:1",)
    ) == handle
    assert planner.calls == 1
    assert store.read_active(handle).receipt.revision_digest == store.read_active(handle).digest


def test_ambiguous_planning_pass_is_never_called_a_second_time_after_restart():
    source = _source(_ticket("issue:1"))
    store = InMemoryPlanControlStore()

    class PlannerThatLosesItsReply:
        def __init__(self):
            self.calls = 0

        def plan(self, snapshot, planning_id):
            del snapshot, planning_id
            self.calls += 1
            raise RuntimeError("planning runtime lost after possible completion")

    planner = PlannerThatLosesItsReply()
    control = PlanControl(source=source, planner=planner, store=store)
    with pytest.raises(RuntimeError):
        control.start("local/plancontrol", ("issue:1",))
    with pytest.raises(PlanControlError) as ambiguous:
        control.start("local/plancontrol", ("issue:1",))

    assert ambiguous.value.code == "PLANNING_READBACK_AMBIGUOUS"
    assert planner.calls == 1


def test_publish_and_receipt_readback_are_a_gate_before_start_returns():
    source = _source(_ticket("issue:1"))

    class PlanHiddenStore(InMemoryPlanControlStore):
        def read_revision(self, handle, digest):
            return None

    with pytest.raises(PlanControlError) as plan_missing:
        PlanControl(
            source=source,
            planner=InMemoryPlanningPass(_intent("issue:1")),
            store=PlanHiddenStore(),
        ).start("local/plancontrol", ("issue:1",))
    assert plan_missing.value.code == "PLAN_READBACK_MISMATCH"

    class ReceiptHiddenStore(InMemoryPlanControlStore):
        def read_receipt(self, handle, digest):
            return None

    receipt_store = ReceiptHiddenStore()
    with pytest.raises(PlanControlError) as receipt_missing:
        PlanControl(
            source=source,
            planner=InMemoryPlanningPass(_intent("issue:1")),
            store=receipt_store,
        ).start(
            "local/plancontrol",
            ("issue:1",),
            CampaignStartOptions(campaign_key="campaign:receipt-gate"),
        )
    assert receipt_missing.value.code == "ACTIVATION_RECEIPT_READBACK_MISMATCH"
    assert receipt_store.read_active(
        CampaignHandle("local/plancontrol", "campaign:receipt-gate")
    ) is None


def test_v3_freezes_ticket_contract_policy_and_provider_neutral_authority():
    source = _source(_ticket("issue:1"))
    planner = InMemoryPlanningPass(_intent("issue:1"))
    store = InMemoryPlanControlStore()
    handle = PlanControl(source=source, planner=planner, store=store).start(
        "local/plancontrol", ("issue:1",)
    )
    before = store.read_active(handle).canonical_bytes
    document = store.read_active(handle).plan_spec

    source.tickets["issue:1"]["contract"]["acceptance"].append("later edit")
    source.policy["content"]["version"] = 2

    assert store.read_active(handle).canonical_bytes == before
    assert document["work"][0]["contract"] == _contract("issue:1")
    assert document["campaign"]["authority"]["grants"] == _policy()["authority_grants"]["campaign"]
    assert document["work"][0]["authority"]["review"]["grants"] == _policy()["authority_grants"]["review"]
    forbidden = {
        "provider", "model", "cli", "runtime_binding", "capacity", "checks", "recovery", "integration", "nodes", "edges"
    }
    encoded = json.dumps(document, sort_keys=True)
    assert all(f'"{field}"' not in encoded for field in forbidden)


def test_v3_rejects_forbidden_runtime_or_lifecycle_fields_before_planning():
    ticket = _ticket("issue:1")
    ticket["contract"]["provider"] = "not-allowed"
    planner = InMemoryPlanningPass(_intent("issue:1"))

    with pytest.raises(PlanControlError) as rejected:
        PlanControl(
            source=_source(ticket), planner=planner, store=InMemoryPlanControlStore()
        ).start("local/plancontrol", ("issue:1",))

    assert rejected.value.code == "PLAN_FIELD_FORBIDDEN"
    assert planner.calls == 0


def test_successor_cas_keeps_campaign_handle_stable_and_rejects_wrong_parent():
    source = _source(_ticket("issue:1"))
    store = InMemoryPlanControlStore()
    first_planner = InMemoryPlanningPass(_intent("issue:1"))
    options = CampaignStartOptions(campaign_key="campaign:stable")
    first = PlanControl(source=source, planner=first_planner, store=store).start(
        "local/plancontrol", ("issue:1",), options
    )
    first_digest = store.read_active(first).digest
    source.tickets["issue:1"]["contract"]["acceptance"].append("new acceptance")

    successor = PlanControl(
        source=source, planner=InMemoryPlanningPass(_intent("issue:1")), store=store
    ).start(
        "local/plancontrol",
        ("issue:1",),
        CampaignStartOptions(
            campaign_key="campaign:stable",
            expected_previous_revision_digest=first_digest,
        ),
    )

    assert successor == first
    assert store.read_active(first).digest != first_digest
    assert (
        store.read_active(first).receipt.expected_previous_revision_digest == first_digest
    )
    with pytest.raises(PlanControlError) as stale:
        PlanControl(
            source=source, planner=InMemoryPlanningPass(_intent("issue:1")), store=store
        ).start(
            "local/plancontrol",
            ("issue:1",),
            CampaignStartOptions(
                campaign_key="campaign:stable",
                expected_previous_revision_digest="0" * 64,
            ),
        )
    assert stale.value.code == "ACTIVATION_CAS_CONFLICT"


def test_disjoint_claims_are_concurrent_and_any_overlap_fails_before_planning():
    first_keys = tuple(f"issue:{number}" for number in range(1, 5))
    second_keys = tuple(f"issue:{number}" for number in range(5, 9))
    all_tickets = {key: _ticket(key) for key in (*first_keys, *second_keys)}
    store = InMemoryPlanControlStore()
    first_planner = InMemoryPlanningPass(_intent(*first_keys))
    first_handle = PlanControl(
        source=InMemoryCampaignSource(
            repository="local/plancontrol",
            target_branch="main",
            campaign_source={"ref": "git://local/main", "digest": "main-sha"},
            policy=_policy(),
            tickets=all_tickets,
        ),
        planner=first_planner,
        store=store,
    ).start("local/plancontrol", first_keys)
    second_planner = InMemoryPlanningPass(_intent(*second_keys))
    second_handle = PlanControl(
        source=InMemoryCampaignSource(
            repository="local/plancontrol",
            target_branch="main",
            campaign_source={"ref": "git://local/main", "digest": "main-sha"},
            policy=_policy(),
            tickets=all_tickets,
        ),
        planner=second_planner,
        store=store,
    ).start("local/plancontrol", second_keys)

    assert first_handle != second_handle
    assert store.claimed_ticket_keys(first_handle) == frozenset(first_keys)
    assert store.claimed_ticket_keys(second_handle) == frozenset(second_keys)
    overlap_planner = InMemoryPlanningPass(_intent("issue:1", "issue:9"))
    all_tickets["issue:9"] = _ticket("issue:9")
    with pytest.raises(PlanControlError) as overlap:
        PlanControl(
            source=InMemoryCampaignSource(
                repository="local/plancontrol",
                target_branch="main",
                campaign_source={"ref": "git://local/main", "digest": "main-sha"},
                policy=_policy(),
                tickets=all_tickets,
            ),
            planner=overlap_planner,
            store=store,
        ).start("local/plancontrol", ("issue:1", "issue:9"))
    assert overlap.value.code == "TICKET_CLAIM_CONFLICT"
    assert overlap_planner.calls == 0


def test_start_never_calls_v2_compiler_or_writes_a_v2_projection(monkeypatch):
    def forbidden_v2(*_args, **_kwargs):
        raise AssertionError("new start must not invoke the V2 compiler")

    monkeypatch.setattr(PlanCompiler, "compile", forbidden_v2)
    source = _source(_ticket("issue:1"))
    store = InMemoryPlanControlStore()

    handle = PlanControl(
        source=source, planner=InMemoryPlanningPass(_intent("issue:1")), store=store
    ).start("local/plancontrol", ("issue:1",))

    document = store.read_active(handle).plan_spec
    assert document["schema_version"] == 3
    assert "nodes" not in document and "edges" not in document
