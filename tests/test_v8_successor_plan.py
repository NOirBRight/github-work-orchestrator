from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import sys

import pytest


SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "orchestrator" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
import gwo_v8  # noqa: F401  # bind the package to this isolated worktree


# Task 1's support module is shared by the successor task worktrees.  The
# fallback keeps this isolated worktree runnable while that shared placeholder
# remains outside this task's file scope.
try:
    from v8_successor_test_support import (
        successor_classification_value,
        three_ticket_replanning_snapshot,
    )
except ModuleNotFoundError:
    shared_tests = Path(__file__).resolve().parents[2] / "issue-135" / "tests"
    if str(shared_tests) not in sys.path:
        sys.path.insert(0, str(shared_tests))
    from v8_successor_test_support import (
        successor_classification_value,
        three_ticket_replanning_snapshot,
    )


def _bound_successor_classification(snapshot, **kwargs):
    from gwo_v8._canonical import digest_value

    classification = successor_classification_value(**kwargs).canonical()
    classification["snapshot_digest"] = snapshot.get(
        "snapshot_digest", digest_value(snapshot)
    )
    classification["plan_revision_digest"] = snapshot.get(
        "plan_revision_digest", snapshot["active_plan_revision"]["digest"]
    )
    return classification


def test_existing_owner_keeps_all_campaign_tickets():
    from gwo_v8.successor_plan import derive_successor_plan_intent

    snapshot = three_ticket_replanning_snapshot()
    intent = derive_successor_plan_intent(
        snapshot,
        _bound_successor_classification(
            snapshot,
            owners=("issue:110",),
            resources=(
                (
                    "issue:110",
                    "repository.target.v1",
                    "The existing owner serializes target writes.",
                ),
            ),
        ),
    )

    assert intent["admitted_work"] == ["issue:108", "issue:109", "issue:110"]


def test_owner_without_plan_delta_is_not_a_successor():
    from gwo_v8.successor_plan import (
        SuccessorPlanError,
        derive_successor_plan_intent,
    )

    snapshot = three_ticket_replanning_snapshot()
    with pytest.raises(SuccessorPlanError) as raised:
        derive_successor_plan_intent(
            snapshot,
            _bound_successor_classification(snapshot, owners=("issue:110",)),
        )

    assert raised.value.code == "SUCCESSOR_PLAN_UNCHANGED"


def test_justified_new_dependency_is_added_in_from_depends_on_to_direction():
    from gwo_v8.successor_plan import (
        compile_successor_plan_spec,
        derive_successor_plan_intent,
    )

    snapshot = three_ticket_replanning_snapshot()
    intent = derive_successor_plan_intent(
        snapshot,
        _bound_successor_classification(
            snapshot,
            dependencies=(("issue:109", "issue:110", "110 owns persistence"),),
        ),
    )
    plan = compile_successor_plan_spec(snapshot, intent)
    work = {item["key"]: item for item in plan["work"]}

    assert work["issue:109"]["depends_on"] == ["issue:110"]
    assert work["issue:110"]["depends_on"] == []


def test_policy_allowed_resource_is_added_without_authority_change():
    from gwo_v8.successor_plan import (
        compile_successor_plan_spec,
        derive_successor_plan_intent,
    )

    snapshot = three_ticket_replanning_snapshot()
    before = deepcopy(snapshot["active_plan_revision"]["plan_spec"])
    intent = derive_successor_plan_intent(
        snapshot,
        _bound_successor_classification(
            snapshot,
            resources=(
                (
                    "issue:110",
                    "repository.target.v1",
                    "The existing owner serializes target writes.",
                ),
            ),
        ),
    )
    plan = compile_successor_plan_spec(snapshot, intent)
    work = {item["key"]: item for item in plan["work"]}
    previous_work = {item["key"]: item for item in before["work"]}

    assert work["issue:110"]["exclusive_resources"] == ["repository.target.v1"]
    assert work["issue:110"]["authority"] == previous_work["issue:110"]["authority"]
    assert work["issue:110"]["capabilities"] == previous_work["issue:110"]["capabilities"]


@pytest.mark.parametrize(
    "case",
    (
        "unapproved_ticket",
        "self_dependency",
        "duplicate_dependency",
        "cyclic_dependency",
        "empty_dependency_reason",
        "policy_unknown_resource",
        "unapproved_resource_ticket",
        "duplicate_resource",
        "empty_resource_reason",
    ),
)
def test_illegal_successor_fact_matrix_fails_closed(case):
    from gwo_v8.successor_plan import SuccessorPlanError, derive_successor_plan_intent

    snapshot = three_ticket_replanning_snapshot()
    classification = _bound_successor_classification(snapshot)
    successor = classification["successor"]
    if case == "unapproved_ticket":
        successor["approved_ticket_keys"] = ["issue:999"]
    elif case == "self_dependency":
        successor["dependency_additions"] = [
            {"from": "issue:109", "to": "issue:109", "reason": "self"}
        ]
    elif case == "duplicate_dependency":
        edge = {"from": "issue:109", "to": "issue:110", "reason": "edge"}
        successor["dependency_additions"] = [edge, dict(edge)]
    elif case == "cyclic_dependency":
        successor["dependency_additions"] = [
            {"from": "issue:109", "to": "issue:110", "reason": "forward"},
            {"from": "issue:110", "to": "issue:109", "reason": "reverse"},
        ]
    elif case == "empty_dependency_reason":
        successor["dependency_additions"] = [
            {"from": "issue:109", "to": "issue:110", "reason": ""}
        ]
    elif case == "policy_unknown_resource":
        successor["exclusive_resource_additions"] = [
            {
                "ticket_key": "issue:110",
                "resource_id": "repository.admin.v1",
                "reason": "unknown",
            }
        ]
    elif case == "unapproved_resource_ticket":
        successor["exclusive_resource_additions"] = [
            {
                "ticket_key": "issue:999",
                "resource_id": "repository.target.v1",
                "reason": "foreign",
            }
        ]
    elif case == "duplicate_resource":
        resource = {
            "ticket_key": "issue:110",
            "resource_id": "repository.target.v1",
            "reason": "one",
        }
        successor["exclusive_resource_additions"] = [resource, dict(resource)]
    elif case == "empty_resource_reason":
        successor["exclusive_resource_additions"] = [
            {
                "ticket_key": "issue:110",
                "resource_id": "repository.target.v1",
                "reason": "",
            }
        ]
    else:  # pragma: no cover - pytest parametrization is closed
        raise AssertionError(case)

    with pytest.raises(SuccessorPlanError):
        derive_successor_plan_intent(
            snapshot,
            classification,
        )


@pytest.mark.parametrize("binding_field", ("snapshot_digest", "plan_revision_digest"))
def test_classification_must_bind_active_snapshot_and_revision(binding_field):
    from gwo_v8.successor_plan import (
        SuccessorPlanError,
        derive_successor_plan_intent,
    )

    snapshot = three_ticket_replanning_snapshot()
    classification = _bound_successor_classification(
        snapshot,
        dependencies=(("issue:109", "issue:110", "110 owns persistence"),),
    )
    classification[binding_field] = "f" * 64

    with pytest.raises(SuccessorPlanError) as raised:
        derive_successor_plan_intent(snapshot, classification)

    assert raised.value.code == "PLAN_INVALIDATION_CLASSIFICATION_READBACK_INVALID"


@pytest.mark.parametrize(
    ("field", "code"),
    (
        ("target_branch", "REPLAN_SOURCE_CHANGED"),
        ("campaign_source", "REPLAN_SOURCE_CHANGED"),
        ("tickets", "REPLAN_SOURCE_CHANGED"),
        ("external_dependencies", "REPLAN_SOURCE_CHANGED"),
        ("policy", "REPLAN_POLICY_CHANGED"),
    ),
)
def test_fresh_source_must_equal_the_frozen_projection(field, code):
    from gwo_v8.successor_plan import (
        SuccessorPlanError,
        validate_fresh_successor_source,
    )

    snapshot = three_ticket_replanning_snapshot()
    fresh = deepcopy(snapshot)
    if field == "target_branch":
        fresh[field] = "release"
    elif field == "campaign_source":
        fresh[field]["resolved_commit_oid"] = "c" * 40
    elif field == "tickets":
        fresh[field][0]["contract"]["body"] = "changed"
    elif field == "external_dependencies":
        fresh[field].append(
            {
                "key": "issue:901",
                "state": "closed",
                "repository": "owner/repository",
            }
        )
    else:
        fresh["policy"]["ref"] = "policy:changed"

    with pytest.raises(SuccessorPlanError) as raised:
        validate_fresh_successor_source(snapshot, fresh)

    assert raised.value.code == code


def test_fresh_source_requires_observed_policy_projection():
    from gwo_v8.successor_plan import (
        SuccessorPlanError,
        validate_fresh_successor_source,
    )

    snapshot = three_ticket_replanning_snapshot()
    fresh = deepcopy(snapshot)
    del fresh["policy"]

    with pytest.raises(SuccessorPlanError) as raised:
        validate_fresh_successor_source(snapshot, fresh)

    assert raised.value.code == "REPLAN_POLICY_CHANGED"
