"""Shared CandidateReceipt foundation fixtures for Candidate Assurance tests."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path
import sys

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "orchestrator" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from gwo_v8.candidate_gate import (
    CandidateDiffEntryV1,
    CandidateDiffRecordV1,
    CandidateGateParent,
    CandidateIdentity,
    CandidateReadback,
    CandidateReceipt,
)
from gwo_v8._canonical import canonical_bytes, digest_bytes, load_canonical_json
from gwo_v8.execution_kernel import (
    ExecutionKernel,
    WorkRunAction,
    WorkRunObservation,
)
from gwo_v8.revision_identity import work_run_key, work_subject_digest
from gwo_v8.runtime_gateway import WorkRunPurpose, WorkRunSubject
from v8_successor_test_support import _StaticPlanReader, _minimal_active_campaign


def _minimal_candidate_campaign(ticket_key: str):
    """Adapt the predecessor fixture to the Candidate Assurance ticket.

    The predecessor helper is intentionally frozen for its successor tests: it
    has a different Campaign key and only knows the predecessor ticket set.
    Keep that helper untouched and make the small, local fixture handoff here.
    """

    active, _campaign = _minimal_active_campaign(("issue:108",))
    plan = load_canonical_json(active.plan_spec_bytes)
    work_item = next(item for item in plan["work"] if item["key"] == "issue:108")
    work_item = dict(work_item)
    work_item["key"] = ticket_key
    plan["campaign"] = dict(plan["campaign"])
    plan["campaign"]["key"] = active.handle.campaign_key
    plan["work"] = [work_item]
    payload = canonical_bytes(plan)
    revision = digest_bytes(payload)
    receipt = replace(
        active.activation_receipt,
        revision_digest=revision,
        ready_refs=(ticket_key,),
        ticket_keys=(ticket_key,),
    )
    claims = tuple(
        replace(
            proof,
            ticket_key=ticket_key,
            plan_revision_digest=revision,
        )
        for proof in active.claim_proofs
    )
    return (
        replace(
            active,
            current_revision_digest=revision,
            plan_spec_bytes=payload,
            activation_receipt=receipt,
            claim_proofs=claims,
        ),
        active.handle,
    )


@dataclass
class CandidateReceiptEffects:
    receipt: CandidateReceipt
    executed: list[WorkRunAction] = field(default_factory=list)

    def readback(self, _action: WorkRunAction) -> WorkRunObservation | None:
        return None

    def execute(self, action: WorkRunAction) -> WorkRunObservation:
        self.executed.append(action)
        return WorkRunObservation(
            phase="candidate_checks",
            stable_action_id=action.stable_action_id,
            receipt_digest=self.receipt.digest,
            candidate_receipt=self.receipt,
        )


def make_candidate_diff_record(
    *,
    candidate_commit_oid: str = "c" * 40,
    candidate_tree_oid: str = "d" * 40,
) -> CandidateDiffRecordV1:
    entry = CandidateDiffEntryV1(
        old_path=None,
        new_path="c3JjL21haW4ucHk",
        change_kind="add",
        old_mode=None,
        new_mode="100644",
        old_object_type=None,
        new_object_type="blob",
        old_oid=None,
        new_oid="3" * 40,
    )
    return CandidateDiffRecordV1(
        schema_version="CandidateDiffRecordV1",
        repository_object_format="sha1",
        base_commit_oid="a" * 40,
        base_tree_oid="b" * 40,
        candidate_commit_oid=candidate_commit_oid,
        candidate_tree_oid=candidate_tree_oid,
        entries=(entry,),
    )


def make_candidate_receipt(
    active=None,
    campaign=None,
    ticket_key: str = "issue:114",
    *,
    candidate_commit_oid: str = "c" * 40,
    candidate_tree_oid: str = "d" * 40,
) -> CandidateReceipt:
    supplied_campaign = active is not None and campaign is not None
    if not supplied_campaign:
        active, campaign = _minimal_active_campaign((ticket_key,))
    assert active is not None and campaign is not None
    subject_digest = None
    run_key = f"work-run:{ticket_key}"
    if supplied_campaign:
        plan = load_canonical_json(active.plan_spec_bytes)
        work_item = next(item for item in plan["work"] if item["key"] == ticket_key)
        subject_digest = work_subject_digest(plan, work_item)
        run_key = work_run_key(ticket_key, subject_digest)
    subject = WorkRunSubject(
        repository=campaign.repository,
        campaign_key=campaign.campaign_key,
        campaign_handle=campaign.campaign_key,
        plan_revision_digest=active.current_revision_digest,
        work_run_key=run_key,
        ticket_key=ticket_key,
        purpose=WorkRunPurpose.implementation(),
        prompt_artifact_digest="1" * 64,
        authority_subtree_digest="2" * 64,
        stable_action_id=f"worker:{ticket_key}",
    )
    parent = CandidateGateParent(
        runtime_subject=subject,
        ticket_contract_digest="3" * 64,
        policy_witness_digest="4" * 64,
        workspace_identity=f"workspace:{ticket_key}",
    )
    record = make_candidate_diff_record(
        candidate_commit_oid=candidate_commit_oid,
        candidate_tree_oid=candidate_tree_oid,
    )
    candidate = CandidateIdentity(
        reported_reference="refs/heads/candidate",
        base_commit_oid=record.base_commit_oid,
        base_tree_oid=record.base_tree_oid,
        candidate_commit_oid=record.candidate_commit_oid,
        candidate_tree_oid=record.candidate_tree_oid,
        changed_path_tokens=record.changed_path_tokens,
    )
    readback = CandidateReadback(
        repository=campaign.repository,
        candidate=candidate,
        diff_record=record,
    )
    receipt = CandidateReceipt.from_readback(
        parent=parent,
        reported_reference=candidate.reported_reference,
        readback=readback,
    )
    if subject_digest is not None:
        receipt = replace(
            receipt,
            runtime_subject_digest=subject_digest,
            receipt_digest=None,
        )
    return receipt


def read_kernel_state(kernel: ExecutionKernel, campaign):
    state = kernel._load(campaign)
    assert state is not None
    return state


def write_kernel_state(kernel: ExecutionKernel, campaign, state: dict[str, object]) -> None:
    kernel._save(campaign, state)


@pytest.fixture
def kernel_with_candidate_receipt(tmp_path):
    active, campaign = _minimal_candidate_campaign("issue:114")
    receipt = make_candidate_receipt(active, campaign, "issue:114")
    effects = CandidateReceiptEffects(receipt)
    kernel = ExecutionKernel(
        store_path=tmp_path / "candidate-receipt.sqlite3",
        plan_control=_StaticPlanReader(active),
        effects=effects,
    )
    kernel.advance(campaign)
    state = read_kernel_state(kernel, campaign)
    assert state["runs"]["issue:114"]["candidate_receipt"] == receipt.canonical()
    return kernel, effects, campaign, receipt
