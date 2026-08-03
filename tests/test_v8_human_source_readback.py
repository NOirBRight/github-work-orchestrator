from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import sys

import pytest


SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "orchestrator" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


from gwo_v8._canonical import canonical_bytes, digest_bytes, digest_value
from gwo_v8.activation import GitHubContent
from gwo_v8.plan_control import CampaignHandle


REPOSITORY = "owner/repository"
CAMPAIGN_KEY = "campaign:one"
DECISION_ID = "decision:" + "1" * 24
READBACK_REF = "github://human-approval/one"
ACTOR_REF = "workflow://gwo-human-gate"
SOURCE_PATH = ".gwo-v8/human-tracker-source.json"
POLICY_PATH = ".gwo-v8/policy-witness.json"


def _decision(*, required_change: str = "authority", predecessor: str = "f" * 64):
    from gwo_v8.human_gate import HumanDecisionRecord, RequiredDurableSourceChange

    return HumanDecisionRecord(
        decision_id=DECISION_ID,
        campaign=CampaignHandle(REPOSITORY, CAMPAIGN_KEY),
        classification_action_id="replan:classification:one",
        plan_revision_digest="b" * 64,
        evidence_digests=("c" * 64, "d" * 64),
        required_change=required_change,
        detail="The frozen authority is insufficient for the required effect.",
        required_source=RequiredDurableSourceChange(
            required_change=required_change,
            source_kind=RequiredDurableSourceChange.source_kind_for(required_change),
            predecessor_source_digest=predecessor,
            required_subject=f"{CAMPAIGN_KEY}:{required_change}",
            detail="Read the exact authoritative source before continuing.",
        ),
    )


def _ticket(number: int) -> dict:
    key = f"issue:{number}"
    repository = {
        "full_name": REPOSITORY,
        "url": f"https://api.github.com/repos/{REPOSITORY}",
    }
    contract = {
        "id": number,
        "node_id": f"ISSUE_{number}",
        "number": number,
        "title": f"Contract {number}",
        "body": f"Complete contract {number}",
        "state": "open",
        "state_reason": None,
        "type": None,
        "repository": repository,
        "labels": [
            {
                "id": 1,
                "node_id": "LABEL_READY",
                "url": f"https://api.github.com/repos/{REPOSITORY}/labels/ready-for-agent",
                "name": "ready-for-agent",
                "color": "0052cc",
                "default": False,
                "description": "ready",
            }
        ],
        "comments": [],
        "updated_at": "2026-08-01T00:00:00Z",
    }
    labels = ["ready-for-agent"]
    blockers: list[dict] = []
    return {
        "key": key,
        "labels": labels,
        "source": {
            "ref": key,
            "digest": digest_value(
                {
                    "number": number,
                    "contract": contract,
                    "labels": labels,
                    "source_ref": key,
                    "native_blockers": blockers,
                }
            ),
        },
        "contract": contract,
        "native_blockers": blockers,
    }


def _tracker_source(*, source_change_digest: str | None = None) -> dict:
    tickets = [_ticket(108), _ticket(109)]
    campaign_source_core = {
        "input_ref": "refs/heads/main",
        "resolved_commit_oid": "a" * 40,
        "tree_oid": "b" * 40,
    }
    campaign_source = {
        **campaign_source_core,
        "digest": digest_value(campaign_source_core),
    }
    membership_core = {"ticket_keys": [ticket["key"] for ticket in tickets]}
    membership = {**membership_core, "digest": digest_value(membership_core)}
    source_core = {
        "kind": "gwo.human-tracker-source.v1",
        "repository": REPOSITORY,
        "campaign_key": CAMPAIGN_KEY,
        "target_branch": "main",
        "campaign_source": campaign_source,
        "membership": membership,
        "tickets": tickets,
        "product_release": None,
    }
    return {
        **source_core,
        "source_change_digest": source_change_digest or digest_value(source_core),
    }


def _policy() -> dict:
    core = {
        "schema_version": 1,
        "ref": "policy:human-source",
        "replan": {
            "successor_revision_limit": 2,
            "repeated_invalidation_limit": 3,
        },
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
        "exclusive_resources": ["repository.target.v1"],
    }
    return {**core, "digest": digest_value(core)}


def _approval(
    decision,
    *,
    approval_state: str = "approved",
    required_change: str | None = None,
    actor_ref: str = ACTOR_REF,
    evidence_digests: list[str] | None = None,
    source_change_digest: str | None = None,
):
    source_digest = source_change_digest or digest_value(_tracker_source())
    return {
        "kind": "gwo.human-approval.v1",
        "decision_id": decision.decision_id,
        "classification_action_id": decision.classification_action_id,
        "predecessor_revision_digest": decision.plan_revision_digest,
        "evidence_digests": list(evidence_digests or decision.evidence_digests),
        "required_change": required_change or decision.required_change,
        "approval_state": approval_state,
        "approval_record_ref": READBACK_REF,
        "approval_actor_ref": actor_ref,
        "tracker_source_ref": SOURCE_PATH,
        "policy_witness_ref": POLICY_PATH,
        "source_change_digest": source_digest,
    }


class _ApprovalReader:
    def __init__(self, records):
        self.records = list(records)
        self.calls: list[tuple[str, str]] = []

    def read_human_approval(self, repository, readback_ref):
        self.calls.append((repository, readback_ref))
        if len(self.records) > 1:
            return deepcopy(self.records.pop(0))
        return deepcopy(self.records[0]) if self.records else None


class _ContentReader:
    def __init__(self, source: dict, policy: dict):
        self.contents = {
            SOURCE_PATH: GitHubContent(canonical_bytes(source), "blob:source"),
            POLICY_PATH: GitHubContent(canonical_bytes(policy), "blob:policy"),
        }
        self.read_sequences: dict[str, list[GitHubContent | None]] = {}
        self.calls: list[tuple[str, str, str]] = []

    def read(self, repository, branch, path):
        self.calls.append((repository, branch, path))
        sequence = self.read_sequences.get(path)
        if sequence:
            return sequence.pop(0)
        return self.contents.get(path)


def _source(*, approvals=None, source=None, policy=None, **kwargs):
    try:
        from gwo_v8.human_source import GitHubHumanApprovalSource
    except ModuleNotFoundError as error:
        pytest.fail(
            f"RED: GitHubHumanApprovalSource is not implemented yet: {error}",
            pytrace=False,
        )

    decision = kwargs.pop("decision", _decision())
    approval_reader = _ApprovalReader(approvals or [_approval(decision)])
    content_reader = _ContentReader(source or _tracker_source(), policy or _policy())
    adapter = GitHubHumanApprovalSource(
        approval_client=approval_reader,
        content_client=content_reader,
        control_branch="gwo-control",
        target_branch="main",
        policy_path=POLICY_PATH,
        tracker_source_path=SOURCE_PATH,
    )
    return adapter, approval_reader, content_reader, decision


@pytest.mark.parametrize(
    ("approval_state", "expected_code"),
    (
        ("pending", "HUMAN_SOURCE_READBACK_PENDING"),
        ("rejected", "HUMAN_SOURCE_REJECTED"),
        ("incomplete", "HUMAN_SOURCE_READBACK_INCOMPLETE"),
        ("ambiguous", "HUMAN_SOURCE_AMBIGUOUS"),
        ("reverted", "HUMAN_SOURCE_REVERTED"),
        ("out_of_policy", "HUMAN_SOURCE_OUT_OF_POLICY"),
    ),
)
def test_non_approved_authoritative_states_have_no_approved_bytes(
    approval_state: str, expected_code: str
):
    adapter, approval_reader, content_reader, decision = _source(
        approvals=[_approval(_decision(), approval_state=approval_state)]
    )

    result = adapter.read(decision.campaign, decision, READBACK_REF)

    assert result.state == approval_state
    assert result.code == expected_code
    assert result.approval_record_bytes is None
    assert result.tracker_source_bytes is None
    assert result.policy_witness_bytes is None
    assert len(approval_reader.calls) == 2


def test_minimal_policy_witness_is_rejected_before_approved_readback_is_returned():
    decision = _decision()
    minimal_policy = {
        "kind": "gwo.policy-witness.v1",
        "replan": {
            "successor_revision_limit": 1,
            "repeated_invalidation_limit": 1,
        },
    }
    minimal_policy["digest"] = digest_value(
        {key: value for key, value in minimal_policy.items() if key != "digest"}
    )
    adapter, _, _, _ = _source(policy=minimal_policy, decision=decision)

    with pytest.raises(Exception) as error:
        adapter.read(decision.campaign, decision, READBACK_REF)

    assert error.value.code == "REPLAN_BUDGET_POLICY_INVALID"


def test_default_trusted_actor_is_exact_not_prefix_only():
    decision = _decision()
    approval = _approval(decision, actor_ref="workflow://another-workflow")
    adapter, _, content_reader, _ = _source(approvals=[approval], decision=decision)

    with pytest.raises(Exception) as error:
        adapter.read(decision.campaign, decision, READBACK_REF)

    assert error.value.code == "HUMAN_APPROVAL_UNAUTHORIZED"
    assert content_reader.calls == []


def test_actor_configuration_cannot_supply_an_ambiguous_singleton_and_allowlist():
    from gwo_v8.human_source import GitHubHumanApprovalSource

    with pytest.raises(Exception) as error:
        GitHubHumanApprovalSource(
            approval_client=_ApprovalReader([]),
            content_client=_ContentReader(_tracker_source(), _policy()),
            control_branch="gwo-control",
            target_branch="main",
            approval_actor_ref=ACTOR_REF,
            allowed_approval_actor_refs=(ACTOR_REF,),
        )

    assert error.value.code == "PLAN_CONTROL_COMPOSITION_INVALID"


def test_source_readback_distinguishes_tracker_noop_from_classification_snapshot_digest():
    baseline_source = _tracker_source()
    baseline_digest = baseline_source["source_change_digest"]
    decision = _decision(predecessor=baseline_digest)
    approval = _approval(decision, source_change_digest=baseline_digest)
    adapter, _, _, _ = _source(
        approvals=[approval],
        source=baseline_source,
        decision=decision,
    )

    result = adapter.read(decision.campaign, decision, READBACK_REF)

    assert result.state == "reverted"
    assert result.code == "HUMAN_SOURCE_REVERTED"


def test_authority_approval_uses_policy_digest_domain_when_tracker_is_unchanged():
    from dataclasses import replace

    baseline_policy = _policy()
    decision = replace(
        _decision(),
        required_source=replace(
            _decision().required_source,
            predecessor_source_digest=baseline_policy["digest"],
            predecessor_snapshot_digest="a" * 64,
        ),
    )
    approval = _approval(
        decision,
        source_change_digest=baseline_policy["digest"],
    )
    adapter, _, _, _ = _source(
        approvals=[approval],
        source=_tracker_source(),
        policy=baseline_policy,
        decision=decision,
    )

    result = adapter.read(decision.campaign, decision, READBACK_REF)

    assert result.state == "reverted"
    assert result.code == "HUMAN_SOURCE_REVERTED"


def test_authority_approval_accepts_policy_change_without_tracker_change():
    from dataclasses import replace

    predecessor_policy = _policy()
    changed_policy = deepcopy(predecessor_policy)
    changed_policy["replan"]["successor_revision_limit"] = 3
    changed_policy["digest"] = digest_value(
        {key: value for key, value in changed_policy.items() if key != "digest"}
    )
    decision = replace(
        _decision(),
        required_source=replace(
            _decision().required_source,
            predecessor_source_digest=predecessor_policy["digest"],
            predecessor_snapshot_digest="a" * 64,
        ),
    )
    approval = _approval(decision, source_change_digest=changed_policy["digest"])
    adapter, _, _, _ = _source(
        approvals=[approval],
        source=_tracker_source(),
        policy=changed_policy,
        decision=decision,
    )

    result = adapter.read(decision.campaign, decision, READBACK_REF)

    assert result.state == "approved"
    assert result.source_change_digest == changed_policy["digest"]


def test_approved_readback_returns_exact_canonical_bytes_and_double_reads_every_source():
    adapter, approval_reader, content_reader, decision = _source()

    result = adapter.read(decision.campaign, decision, READBACK_REF)

    assert result.state == "approved"
    assert result.code == "HUMAN_SOURCE_APPROVED"
    assert result.approval_record_bytes == canonical_bytes(_approval(decision))
    assert result.tracker_source_bytes == canonical_bytes(_tracker_source())
    assert result.policy_witness_bytes == canonical_bytes(_policy())
    assert result.approval_record_digest == digest_bytes(result.approval_record_bytes)
    assert result.tracker_source_digest == digest_bytes(result.tracker_source_bytes)
    assert result.policy_witness_digest == digest_bytes(result.policy_witness_bytes)
    assert result.source_change_digest == _tracker_source()["source_change_digest"]
    assert len(approval_reader.calls) == 2
    assert content_reader.calls == [
        (REPOSITORY, "gwo-control", SOURCE_PATH),
        (REPOSITORY, "gwo-control", POLICY_PATH),
        (REPOSITORY, "gwo-control", SOURCE_PATH),
        (REPOSITORY, "gwo-control", POLICY_PATH),
    ]


def test_approved_readback_accepts_the_complete_compiler_policy_witness_projection():
    from copy import deepcopy

    from v8_successor_test_support import three_ticket_source_snapshot

    full_policy = deepcopy(three_ticket_source_snapshot()["policy"])
    full_policy["replan"] = {
        "successor_revision_limit": 2,
        "repeated_invalidation_limit": 3,
    }
    full_policy["digest"] = digest_value(
        {key: value for key, value in full_policy.items() if key != "digest"}
    )
    adapter, _, _, decision = _source(policy=full_policy)

    result = adapter.read(decision.campaign, decision, READBACK_REF)

    assert result.state == "approved"
    assert result.policy_witness_bytes == canonical_bytes(full_policy)


@pytest.mark.parametrize(
    "readback_ref",
    ("chat://approval/one", "model://approval/one", "webhook://approval/one", "local://approval/one"),
)
def test_chat_model_webhook_and_local_readback_references_cannot_approve(readback_ref):
    adapter, approval_reader, content_reader, decision = _source()

    with pytest.raises(Exception) as error:
        adapter.read(decision.campaign, decision, readback_ref)

    assert error.value.code == "HUMAN_APPROVAL_UNAUTHORIZED"
    assert approval_reader.calls == []
    assert content_reader.calls == []


def test_malformed_chat_payload_and_untrusted_actor_fail_closed_without_source_bytes():
    decision = _decision()
    adapter, approval_reader, content_reader, _ = _source(
        approvals=[{"message": "approve"}], decision=decision
    )

    with pytest.raises(Exception) as malformed:
        adapter.read(decision.campaign, decision, READBACK_REF)
    assert malformed.value.code == "HUMAN_SOURCE_READBACK_INVALID"
    assert content_reader.calls == []

    adapter, approval_reader, content_reader, _ = _source(
        approvals=[_approval(decision, actor_ref="chat://actor/one")], decision=decision
    )
    with pytest.raises(Exception) as unauthorized:
        adapter.read(decision.campaign, decision, READBACK_REF)
    assert unauthorized.value.code == "HUMAN_APPROVAL_UNAUTHORIZED"
    assert content_reader.calls == []


@pytest.mark.parametrize(
    ("tamper", "expected_code"),
    (
        ("approval", "HUMAN_SOURCE_CHANGED_DURING_READBACK"),
        ("tracker", "HUMAN_SOURCE_CHANGED_DURING_READBACK"),
        ("policy", "HUMAN_SOURCE_CHANGED_DURING_READBACK"),
    ),
)
def test_final_double_read_rejects_changed_authoritative_bytes(tamper, expected_code):
    decision = _decision()
    first_approval = _approval(decision)
    second_approval = deepcopy(first_approval)
    first_source = _tracker_source()
    second_source = deepcopy(first_source)
    first_policy = _policy()
    second_policy = deepcopy(first_policy)
    if tamper == "approval":
        second_approval["source_change_digest"] = "1" * 64
    elif tamper == "tracker":
        second_source["target_branch"] = "release"
    else:
        second_policy["replan"]["successor_revision_limit"] = 4

    adapter, approval_reader, content_reader, _ = _source(
        approvals=[first_approval, second_approval],
        source=first_source,
        policy=first_policy,
        decision=decision,
    )
    if tamper == "tracker":
        content_reader.read_sequences[SOURCE_PATH] = [
            GitHubContent(canonical_bytes(first_source), "blob:source"),
            GitHubContent(canonical_bytes(second_source), "blob:source:changed"),
        ]
    elif tamper == "policy":
        content_reader.read_sequences[POLICY_PATH] = [
            GitHubContent(canonical_bytes(first_policy), "blob:policy"),
            GitHubContent(canonical_bytes(second_policy), "blob:policy:changed"),
        ]

    with pytest.raises(Exception) as error:
        adapter.read(decision.campaign, decision, READBACK_REF)
    assert error.value.code == expected_code


@pytest.mark.parametrize(
    ("tamper", "expected_code"),
    (
        ("evidence", "HUMAN_SOURCE_DIGEST_MISMATCH"),
        ("required_change", "HUMAN_REQUIRED_CHANGE_MISMATCH"),
        ("source_digest", "HUMAN_SOURCE_DIGEST_MISMATCH"),
    ),
)
def test_approval_and_source_mismatches_fail_with_exact_codes(tamper, expected_code):
    decision = _decision()
    if tamper == "evidence":
        approval = _approval(decision, evidence_digests=["0" * 64, "d" * 64])
        source = _tracker_source()
    elif tamper == "required_change":
        approval = _approval(decision, required_change="product")
        source = _tracker_source()
    else:
        source = _tracker_source(source_change_digest=decision.required_source.predecessor_source_digest)
        approval = _approval(decision, source_change_digest=source["source_change_digest"])
    adapter, _, _, _ = _source(
        approvals=[approval], source=source, decision=decision
    )

    if tamper == "source_digest":
        with pytest.raises(Exception) as error:
            adapter.read(decision.campaign, decision, READBACK_REF)
        assert error.value.code == expected_code
    else:
        with pytest.raises(Exception) as error:
            adapter.read(decision.campaign, decision, READBACK_REF)
        assert error.value.code == expected_code


def test_tracker_source_change_digest_must_bind_the_complete_projection():
    decision = _decision()
    source = _tracker_source()
    source["source_change_digest"] = "e" * 64
    approval = _approval(
        decision,
        source_change_digest=digest_bytes(canonical_bytes(source)),
    )
    adapter, _, _, _ = _source(
        approvals=[approval],
        source=source,
        decision=decision,
    )

    with pytest.raises(Exception) as error:
        adapter.read(decision.campaign, decision, READBACK_REF)

    assert error.value.code == "HUMAN_SOURCE_DIGEST_MISMATCH"


def test_missing_complete_tracker_source_is_incomplete_and_never_uses_local_snapshot():
    adapter, approval_reader, content_reader, decision = _source()
    content_reader.contents[SOURCE_PATH] = None

    result = adapter.read(decision.campaign, decision, READBACK_REF)

    assert result.state == "incomplete"
    assert result.code == "HUMAN_SOURCE_READBACK_INCOMPLETE"
    assert result.approval_record_bytes is None
    assert result.tracker_source_bytes is None
    assert result.policy_witness_bytes is None
    assert len(approval_reader.calls) == 2
