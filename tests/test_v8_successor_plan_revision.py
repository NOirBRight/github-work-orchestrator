"""Public successor Plan Revision vertical slices for Issue #135."""

from __future__ import annotations

import pytest


pytest_plugins = ("v8_successor_test_support",)


def test_public_existing_owner_successor_has_exact_readback_and_preserves_result(
    public_successor,
):
    """An approved existing owner activates one successor through public seams."""

    import gwo_v8
    from v8_successor_test_support import successor_payload

    # The resource is policy-approved and gives the existing owner a real,
    # deterministic PlanSpec delta without inventing a new Ticket.
    public_successor.set_successor_payload(
        successor_payload(
            owners=("issue:110",),
            resources=(
                (
                    "issue:110",
                    "repository.target.v1",
                    "The existing owner needs the shared target resource.",
                ),
            ),
        )
    )

    before = gwo_v8.inspect(public_successor.handle)
    before_runs = {run.ticket_key: run for run in before.work_runs}
    invalidation = public_successor.invalidation_for("issue:109")

    outcome = gwo_v8.advance(
        public_successor.handle,
        plan_invalidation=invalidation,
    )
    after = gwo_v8.inspect(public_successor.handle)
    after_runs = {run.ticket_key: run for run in after.work_runs}

    # Public composition remains one stable Campaign while exact authority
    # readback changes only the active Plan Revision.
    assert outcome.status == after.status
    assert after.campaign == public_successor.handle
    assert after.plan_revision_digest != public_successor.initial_revision_digest
    assert public_successor.gateway.replan_progresses == 1

    # The active successor state intentionally exposes the exact predecessor
    # classification through read-only lineage rather than carrying a stale
    # predecessor classification on the new revision.
    assert after.invalidation_classification is None
    assert len(after.revision_lineage) == 1
    lineage = after.revision_lineage[0]
    assert lineage.plan_revision_digest == public_successor.initial_revision_digest
    assert lineage.classification_action_id.startswith("replan:")

    # The unaffected completed Result survives exactly; the invalidated old
    # Candidate is not adopted into the successor Work Run.
    assert after_runs["issue:108"].work_run_key == before_runs["issue:108"].work_run_key
    assert after_runs["issue:108"].result_digest == before_runs["issue:108"].result_digest
    assert after_runs["issue:109"].work_run_key == before_runs["issue:109"].work_run_key
    assert after_runs["issue:109"].candidate_identity is None
    assert after_runs["issue:110"].work_run_key != before_runs["issue:110"].work_run_key
    assert after_runs["issue:110"].exclusive_resources == ("repository.target.v1",)
    assert "candidate:r0:109" in lineage.candidate_identities

    # These are public read-only types, not private activation machinery.
    assert isinstance(lineage, gwo_v8.RevisionLineageSummary)


def test_public_successor_readback_types_are_exported():
    import gwo_v8

    resource = gwo_v8.PlanInvalidationExclusiveResource(
        "issue:110",
        "repository.target.v1",
        "The existing owner needs the shared target resource.",
    )
    assert resource.ticket_key == "issue:110"
    assert gwo_v8.RevisionLineageSummary.__name__ == "RevisionLineageSummary"


def test_public_approved_dependency_successor_replays_without_a_second_planning_pass(
    public_dependency_successor,
):
    """An approved dependency changes affected Work Run identity once."""

    import gwo_v8
    from v8_successor_test_support import successor_payload

    public_dependency_successor.set_successor_payload(
        successor_payload(
            owners=("issue:110",),
            dependencies=(
                (
                    "issue:109",
                    "issue:110",
                    "The existing owner depends on the invalidated obligation.",
                ),
            ),
        )
    )

    before = gwo_v8.inspect(public_dependency_successor.handle)
    before_runs = {run.ticket_key: run for run in before.work_runs}
    first = gwo_v8.advance(
        public_dependency_successor.handle,
        plan_invalidation=public_dependency_successor.invalidation_for("issue:109"),
    )
    first_readback = gwo_v8.inspect(public_dependency_successor.handle)
    first_runs = {run.ticket_key: run for run in first_readback.work_runs}
    revision = first_readback.plan_revision_digest
    lineage = first_readback.revision_lineage

    assert first.status == first_readback.status
    assert revision != public_dependency_successor.initial_revision_digest
    assert public_dependency_successor.gateway.replan_progresses == 1
    assert first_runs["issue:109"].work_run_key != before_runs["issue:109"].work_run_key
    assert first_runs["issue:109"].candidate_identity is None
    assert first_runs["issue:108"].result_digest == before_runs["issue:108"].result_digest
    assert lineage and "candidate:r0:109" in lineage[0].candidate_identities

    # Recomposition is the durable restart/replay seam.  It must read the
    # already activated receipt and transition, not run another Coordinator.
    public_dependency_successor.reinstall()
    restarted = gwo_v8.inspect(public_dependency_successor.handle)
    replay = gwo_v8.advance(public_dependency_successor.handle)
    replay_readback = gwo_v8.inspect(public_dependency_successor.handle)

    assert restarted.plan_revision_digest == revision
    assert replay_readback.plan_revision_digest == revision
    assert replay.status == replay_readback.status
    assert public_dependency_successor.gateway.replan_progresses == 1
    assert replay_readback.revision_lineage == lineage


def test_public_stale_predecessor_candidate_is_rejected_after_successor_activation(
    public_dependency_successor,
):
    """A predecessor Candidate readback cannot enter successor execution."""

    import gwo_v8
    from v8_successor_test_support import successor_payload

    public_dependency_successor.set_successor_payload(
        successor_payload(
            owners=("issue:110",),
            dependencies=(
                (
                    "issue:109",
                    "issue:110",
                    "The existing owner depends on the invalidated obligation.",
                ),
            ),
        )
    )
    # Stop immediately after successor migration and before the new Work Run
    # can obtain a fresh effect receipt, leaving a deterministic crash-replay
    # boundary for the stale predecessor Candidate.
    public_dependency_successor.arm_crash("kernel_migration")
    with pytest.raises(RuntimeError):
        gwo_v8.advance(
            public_dependency_successor.handle,
            plan_invalidation=public_dependency_successor.invalidation_for("issue:109"),
        )

    public_dependency_successor.effects.replay_predecessor_candidate("issue:109")
    with pytest.raises(gwo_v8.ExecutionKernelError) as raised:
        gwo_v8.advance(
            public_dependency_successor.handle,
            "candidate-gate:stale-predecessor",
        )

    assert raised.value.code == "EFFECT_READBACK_INVALID"
    diagnostics = gwo_v8.inspect(public_dependency_successor.handle)
    current = {run.ticket_key: run for run in diagnostics.work_runs}["issue:109"]
    assert current.candidate_identity is None
    assert diagnostics.revision_lineage[0].candidate_identities == ("candidate:r0:109",)


def test_public_illegal_successor_output_fails_closed_without_partial_activation(
    public_successor,
):
    """An unapproved successor Ticket cannot publish or activate anything."""

    import gwo_v8
    from v8_successor_test_support import successor_payload

    public_successor.set_successor_payload(
        successor_payload(owners=("issue:999",))
    )
    with pytest.raises(gwo_v8.ExecutionKernelError):
        gwo_v8.advance(
            public_successor.handle,
            plan_invalidation=public_successor.invalidation_for("issue:109"),
        )

    diagnostics = gwo_v8.inspect(public_successor.handle)
    assert diagnostics.plan_revision_digest == public_successor.initial_revision_digest
    assert public_successor.gateway.replan_progresses == 1
    assert not diagnostics.revision_lineage


@pytest.mark.parametrize("field", ("contract", "campaign_source", "policy"))
def test_public_source_or_policy_drift_fails_closed_before_successor_activation(
    public_successor,
    field,
):
    """Successor compilation never uses a changed source or Policy Witness."""

    import gwo_v8
    from v8_successor_test_support import successor_payload

    public_successor.set_successor_payload(
        successor_payload(
            owners=("issue:110",),
            dependencies=(
                (
                    "issue:109",
                    "issue:110",
                    "The existing owner depends on the invalidated obligation.",
                ),
            ),
        )
    )
    public_successor.mutate_source(field)

    with pytest.raises(gwo_v8.ExecutionKernelError):
        gwo_v8.advance(
            public_successor.handle,
            plan_invalidation=public_successor.invalidation_for("issue:109"),
        )

    diagnostics = gwo_v8.inspect(public_successor.handle)
    assert diagnostics.plan_revision_digest == public_successor.initial_revision_digest
    assert not diagnostics.revision_lineage


def test_public_activation_cas_drift_retries_the_same_successor_pass(
    public_successor,
):
    """A CAS race leaves no partial activation and replays one exact pass."""

    import gwo_v8
    from v8_successor_test_support import successor_payload

    public_successor.set_successor_payload(
        successor_payload(
            owners=("issue:110",),
            resources=(
                (
                    "issue:110",
                    "repository.target.v1",
                    "The existing owner needs the shared target resource.",
                ),
            ),
        )
    )
    public_successor.arm_cas_conflict()

    with pytest.raises(gwo_v8.ExecutionKernelError):
        gwo_v8.advance(
            public_successor.handle,
            plan_invalidation=public_successor.invalidation_for("issue:109"),
        )

    failed = gwo_v8.inspect(public_successor.handle)
    assert failed.plan_revision_digest == public_successor.initial_revision_digest
    assert failed.invalidation_classification is not None
    assert not failed.revision_lineage
    assert public_successor.gateway.replan_progresses == 1

    public_successor.reinstall()
    gwo_v8.advance(public_successor.handle)
    recovered = gwo_v8.inspect(public_successor.handle)

    assert recovered.plan_revision_digest != public_successor.initial_revision_digest
    assert recovered.revision_lineage
    assert public_successor.gateway.replan_progresses == 1
