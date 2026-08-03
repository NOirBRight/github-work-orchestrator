# Gate Human-Approved Scope and Authority Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (- [ ]) syntax for tracking.

**Goal:** Require one durable, named human Decision and exact authoritative tracker/Policy Witness readback before a V8 Campaign may adopt a new Ticket, changed acceptance, changed Campaign membership, product/release choice, or broader authority, then compile and activate one bounded successor Plan Revision.

**Architecture:** Keep start, advance, and inspect as the only workflow operations. PlanControl persists the #134 Decision, reads a complete approved source bundle through a read-only adapter, performs exactly one successor Campaign Planning Pass, and reuses the #135 deterministic compilation/CAS/readback path. ExecutionKernel owns quiescence, budgets, idempotent replay, public status, and inspect lineage; no semantic role or Runtime boundary receives tracker or authority mutation capability.

**Tech Stack:** Python 3.13, frozen dataclasses, closed canonical JSON, SHA-256 digests, SQLite ExecutionKernel state, GitHub-backed PlanControl state, RuntimeGateway capability proofs, pytest, gh.

## Global Constraints

- Execute only after #135 is merged into the baseline. Treat its successor interfaces as inputs; do not rename or redesign PlanInvalidationDisposition, PlanInvalidationDecision, PlanInvalidationClassification, PlanControl.activate_successor, _PlanningAttempt.planning_protocol_id, REPLANNING_OUTPUT_PROTOCOL_ID, CoordinatorCapabilityProof, or the #135 successor compiler/readback seam.
- Future implementation uses superpowers:using-git-worktrees from a clean latest origin/main worktree. This authoring task changes no implementation code.
- Every production change follows TDD: write a behavior test, run and record RED, implement the minimum behavior, run GREEN, then refactor while green.
- The public surface remains start(repository, ready_refs, options?), advance(campaign_handle, wake_ref?, *, plan_invalidation?, human_decision?), and inspect(campaign_handle). human_decision is a typed non-authoritative input/wake hint, not a new operation.
- Public statuses remain exactly Complete, Running, Decision, Wait, and Blocked.
- While a human Decision is outstanding, Worker, Reviewer, Coordinator, RuntimeGateway, ExecutionKernel, and PlanControl perform zero Issue creation/editing, blocker mutation, label mutation, Campaign-membership mutation, merge mutation, or Authority Grant. PlanControl and ExecutionKernel may persist their own control state; successor publication/activation is forbidden until exact approved source and policy readback.
- Chat text, model output, unverified webhooks, local snapshots, caller mappings, and locally edited Policy Witness bytes cannot approve a Decision. Only authoritative tracker and Policy Witness readback with exact digests can continue it.
- Every continuation binds the stable CampaignHandle, predecessor Plan Revision digest, classification action ID, complete invalidation Evidence digest tuple, Decision digest, approval digest, tracker-source digest, Policy Witness digest, planning action ID, and Activation Receipt.
- A human successor contains the complete approved Campaign membership and Ticket contracts. It may not silently omit Tickets, admit external Tickets, rewrite acceptance from model output, or take Authority Grants from approval payload.
- Exactly one REPLANNING_OUTPUT_PROTOCOL_ID Planning Pass is allowed for each approved source. Restart/replay reuses the durable attempt and never calls another Coordinator pass.
- Repository policy defines finite successor-revision and repeated-invalidation limits. Duplicate Evidence consumes neither. Missing or malformed limits fail closed; Runtime options cannot enlarge or reset them.
- Tests do not call private activation to prove the public contract, manufacture Activation Receipts, edit SQLite directly, or assert on transcripts. Public acceptance uses exported start, advance, and inspect only.

---

## Normative Inputs Read Before Planning

- GitHub Issues #136, #135, #134, and parent #131 via gh, including labels, blockers, and comments.
- CONTEXT.md, docs/agents/domain.md, docs/agents/issue-tracker.md, and docs/agents/triage-labels.md.
- docs/design/gwo-v8-lean-architecture.md, docs/design/gwo-v8-lean-stabilization-spec.md, and docs/design/gwo-v8-lean-roadmap.md.
- Accepted ADR-0055, ADR-0056, ADR-0057, ADR-0058, ADR-0059, ADR-0060, ADR-0061, and ADR-0062, with ADR-0062 governing this bounded replanning contract.
- Existing V8 modules/tests under skills/orchestrator/scripts/gwo_v8 and tests, plus docs/superpowers/plans/2026-08-02-activate-approved-successor-plan-revision.md as the #135 interface handoff.

## Execution Baseline Gate

The #135 dependency is now closed and merged into `origin/main`; execution may proceed only from that merged baseline.

- [ ] Verify the blocker and baseline.

~~~powershell
$state = gh issue view 135 --repo NOirBRight/github-work-orchestrator --json state --jq .state
if ($state -ne 'CLOSED') { throw 'Issue #135 is not closed; stop execution.' }
git fetch origin
git log -1 --oneline origin/main
git show origin/main:skills/orchestrator/scripts/gwo_v8/plan_control.py | Select-String 'class PlanInvalidationClassification|def activate_successor'
git show origin/main:skills/orchestrator/scripts/gwo_v8/planning_protocol.py | Select-String 'REPLANNING_OUTPUT_PROTOCOL_ID'
~~~

Expected: #135 is CLOSED and the merged baseline contains the named interfaces.

- [ ] Create a future isolated worktree and record the exact merged symbols.

~~~powershell
git worktree add D:\Workstation\gwo-worktrees\issue-136 -b codex/issue-136-human-gate origin/main
git -C D:\Workstation\gwo-worktrees\issue-136 status --short
git -C D:\Workstation\gwo-worktrees\issue-136 branch --show-current
~~~

Expected: clean worktree on codex/issue-136-human-gate. Re-anchor line numbers after the #135 merge.

- [ ] Prove the #135 focused baseline is green before the first RED test.

~~~powershell
python -m pytest tests/test_v8_plan_invalidation_classification.py tests/test_v8_successor_planning_protocol.py tests/test_v8_successor_plan.py tests/test_v8_successor_plancontrol.py tests/test_v8_successor_plancontrol_github.py tests/test_v8_successor_execution_kernel.py tests/test_v8_successor_host.py tests/test_v8_successor_plan_revision.py -q
~~~

## File and Responsibility Map

| File | Responsibility |
| --- | --- |
| skills/orchestrator/scripts/gwo_v8/human_gate.py | Private Decision, source-readback, choice, budget, attempt, and inspect-summary contracts |
| skills/orchestrator/scripts/gwo_v8/human_source.py | Read-only tracker/Policy Witness adapter and exact double-read barrier |
| skills/orchestrator/scripts/gwo_v8/runtime_gateway.py | Runtime role capability proof/conformance seam |
| skills/orchestrator/scripts/gwo_v8/plan_control.py | Durable Decision, approved-source validation, one Planning Pass, compilation, CAS, activation, readback |
| skills/orchestrator/scripts/gwo_v8/plan_control_github.py | Human Decision/attempt persistence and schema migration |
| skills/orchestrator/scripts/gwo_v8/plan_control_host.py | Private source/capability/PlanControl composition |
| skills/orchestrator/scripts/gwo_v8/execution_kernel.py | Quiescence, human-gate phases, budgets, public status, lineage, replay |
| skills/orchestrator/scripts/gwo_v8/__init__.py | Only public choice and read-only inspect-summary exports |
| skills/orchestrator/.skill-package.json | Synchronized package manifest |
| tests/v8_human_gate_test_support.py | Shared canonical source/approval fixtures, recording adapters, crash boundaries |
| tests/test_v8_human_gate_protocol.py | Closed contract tests |
| tests/test_v8_human_source_readback.py | Authoritative source tests |
| tests/test_v8_human_gate_capability.py | Runtime capability tests |
| tests/test_v8_human_gate_plancontrol.py | In-memory orchestration tests |
| tests/test_v8_human_gate_plancontrol_github.py | GitHub durability tests |
| tests/test_v8_human_gate_execution_kernel.py | Kernel state/budget tests |
| tests/test_v8_human_gate_public.py | Public start -> advance -> inspect acceptance |

No task may edit another task's files. Wave B is the only parallel wave.

## Maximum-Safe Parallelism

~~~mermaid
flowchart TD
    G["#135 merged baseline"] --> T1["Task 1: contracts and fixtures"]
    T1 --> T2["Task 2: tracker/policy readback"]
    T1 --> T3["Task 3: runtime capability proof"]
    T2 --> T4["Task 4: PlanControl, GitHub, host"]
    T3 --> T4
    T4 --> T5["Task 5: Kernel and public acceptance"]
~~~

Tasks 2 and 3 are independent after Task 1. Task 4 owns all PlanControl/host/durable integration. Task 5 owns the Kernel/package/public vertical slice. This is the maximum safe parallelism because additional workers would contend on these interfaces.

## Exact Contract and Error Codes

Task 1 defines these closed values:

~~~python
HUMAN_REQUIRED_CHANGES = (
    "new_ticket", "acceptance", "campaign_membership",
    "authority", "product", "replan_budget",
)
HUMAN_SOURCE_KINDS = ("tracker", "policy", "tracker_and_policy", "none")
HUMAN_SOURCE_STATES = (
    "pending", "approved", "rejected", "incomplete",
    "ambiguous", "reverted", "out_of_policy",
)
~~~

Define frozen, canonical, exact-schema records:

- RequiredDurableSourceChange(required_change, source_kind, predecessor_source_digest, required_subject, detail). Source kind is tracker for new_ticket, acceptance, campaign_membership, and product; policy for authority; none for replan_budget.
- HumanDecisionRecord(decision_id, campaign, classification_action_id, plan_revision_digest, evidence_digests, required_change, detail, required_source). The Decision ID is derived by PlanControl as decision: plus 24 hex characters of the canonical identity; callers cannot choose it.
- HumanDecisionChoice(decision_id, choice, readback_ref), accepting only approve/reject and a non-empty opaque readback reference. It contains no source bytes, actor claim, mutation, or grant.
- HumanSourceReadback(decision_id, state, approval_record_bytes, tracker_source_bytes, policy_witness_bytes, approval_record_digest, tracker_source_digest, policy_witness_digest, source_change_digest, readback_digest, code). Approved requires all bytes and digests; every digest is recomputed from exact canonical bytes.
- HumanGateAttempt(decision_id, campaign, predecessor_revision_digest, source_readback_digest, tracker_source_digest, policy_witness_digest, planning_action_id, planning_protocol_id, state, compilation_record_artifact_digest, activation_receipt_digest). planning_protocol_id must equal REPLANNING_OUTPUT_PROTOCOL_ID.
- ReplanBudgetPolicy(successor_revision_limit, repeated_invalidation_limit, policy_witness_digest), parsed only from policy.replan.successor_revision_limit and policy.replan.repeated_invalidation_limit; both are positive exact integers.
- HumanGateSummary(phase, decision_id, classification_action_id, required_change, evidence_digests, required_source_kind, reason_code, source_readback_digest, planning_action_id, predecessor_revision_digest, successor_revision_digest, successor_revisions_used, successor_revision_limit, repeated_invalidations, repeated_invalidation_limit). Closed phases are awaiting_human_choice, awaiting_durable_tracker_policy_readback, planning_validated_successor, active_successor, rejected_change, budget_exhausted. It contains no transcript/provider/model/actor conversation.
- HumanApprovalSource protocol: read(handle, decision, readback_ref) -> HumanSourceReadback. The interface has no writer method.

New exact fail-closed codes:

| Code | Condition | Result |
| --- | --- | --- |
| HUMAN_DECISION_RECORD_INVALID | Malformed Decision/source requirement or unknown field | No Decision or successor |
| HUMAN_DECISION_REQUIRED | #134 requires a durable human choice for the exact invalidation | One stable Decision; no source read or Planning |
| HUMAN_DECISION_READBACK_INVALID | Saved Decision missing or differs | Existing Decision remains |
| HUMAN_DECISION_CONFLICT | Same Decision ID has changed bytes | No source read or Planning |
| HUMAN_APPROVAL_INPUT_INVALID | Wrong choice type/Decision ID/empty readback ref | Raise; prior Decision unchanged |
| HUMAN_APPROVAL_UNAUTHORIZED | Approval provenance/actor is not the configured durable workflow | Decision; no successor |
| HUMAN_SOURCE_READBACK_INVALID | Malformed approval/source/policy schema | Wait; no successor |
| HUMAN_SOURCE_READBACK_PENDING | Approval/source not durably visible | Wait; no successor |
| HUMAN_SOURCE_READBACK_INCOMPLETE | Complete membership/contract/blocker/source facts missing | Wait; no successor |
| HUMAN_SOURCE_REJECTED | Exact authoritative workflow rejected the change | Decision; no successor |
| HUMAN_SOURCE_AMBIGUOUS | Conflicting authoritative records exist | Decision; no successor |
| HUMAN_SOURCE_REVERTED | Approved source returned to predecessor facts | Decision; no successor |
| HUMAN_SOURCE_OUT_OF_POLICY | Approved source asks for policy/authority not in Policy Witness | Decision; no successor |
| HUMAN_SOURCE_DIGEST_MISMATCH | Claimed/read-back bytes or Evidence disagree | Wait; no successor |
| HUMAN_SOURCE_CHANGED_DURING_READBACK | First and final source read differ | Wait; no successor |
| HUMAN_REQUIRED_CHANGE_MISMATCH | Source changed a kind other than the named required change | Decision; no successor |
| HUMAN_GATE_ATTEMPT_READBACK_INVALID | Attempt binds wrong source/revision/action or cannot hydrate | Fail closed; predecessor active |
| HUMAN_SUCCESSOR_PLANNING_FAILED | One approved-source Planning/compilation pass fails | Decision/failure; no activation |
| REPLAN_BUDGET_POLICY_INVALID | Missing/malformed/nonfinite policy limits | Decision; no pass |
| REPLAN_BUDGET_READBACK_INVALID | Persisted limit/counter digest differs | Fail closed; no effect |
| REPLAN_SUCCESSOR_BUDGET_EXHAUSTED | Successor count reached its limit | One stable Decision; no pass |
| REPLAN_INVALIDATION_BUDGET_EXHAUSTED | Distinct Evidence reached obligation limit | One stable Decision; no pass |

Preserve all #135 codes, including PLAN_INVALIDATION_CLASSIFICATION_FAILED, PLAN_INVALIDATION_CLASSIFICATION_READBACK_INVALID, PLAN_INVALIDATION_CLASSIFICATION_CONFLICT, PLAN_INVALIDATION_DECISION_INVALID, PLAN_INVALIDATION_CAPABILITY_PROOF_FAIL_CLOSED, ACTIVATION_CAS_CONFLICT, SUCCESSOR_ACTIVATION_FAILED, SUCCESSOR_ACTIVATION_READBACK_INVALID, REPLAN_SOURCE_CHANGED, REPLAN_POLICY_CHANGED, CAMPAIGN_REVISION_CHANGED, EFFECT_READBACK_INVALID, and DURABLE_STATE_INVALID.

## Task 1: Freeze Human Decision Contracts and Shared Test Support

**Files:** Create human_gate.py, tests/v8_human_gate_test_support.py, and tests/test_v8_human_gate_protocol.py. No existing #135 production file or package export is modified.

**Consumes:** #135 CampaignHandle, PlanInvalidationDecision, PlanInvalidationClassification, REPLANNING_OUTPUT_PROTOCOL_ID, and canonical helpers.

**Produces:** The exact records above, HumanApprovalSource, HumanGatePlanReadback, and test fixtures for every later task.

- [ ] Write RED tests for source-kind mapping, Decision digest binding all Evidence/source fields, opaque choice validation, approved readback requiring all digests, positive finite budgets, and closed inspect phases.

~~~python
def test_authority_requires_policy_source():
    source = RequiredDurableSourceChange(
        required_change="authority",
        source_kind="policy",
        predecessor_source_digest="1" * 64,
        required_subject="campaign:authority",
        detail="Read the approved Policy Witness.",
    )
    assert source.source_kind == "policy"
    with pytest.raises(HumanGateError) as error:
        RequiredDurableSourceChange(
            required_change="authority",
            source_kind="tracker",
            predecessor_source_digest="1" * 64,
            required_subject="campaign:authority",
            detail="wrong source",
        )
    assert error.value.code == "HUMAN_DECISION_RECORD_INVALID"
~~~

- [ ] Run RED and verify the failure is the missing module/type.

~~~powershell
python -m pytest tests/test_v8_human_gate_protocol.py -q
~~~

- [ ] Implement exact frozen dataclasses, canonical/from_canonical methods, digest checks, source-kind mapping, and HumanApprovalSource with no mutation methods. Reject missing/extra/wrong-type/duplicate/noncanonical values as HUMAN_DECISION_RECORD_INVALID.
- [ ] Create shared fixtures: HumanGateHarness with invalidation_for(ticket_key, required_change), publish_approved_source, publish_source_state, reinstall, ledger_snapshot, mutation_counts; recording gateway counters planning_progresses/replan_progresses/human_gate_reads; recording mutation counters issue_create, issue_edit, blocker_edit, label_edit, membership_edit, authority_grant, plan_activate, merge.
- [ ] Run GREEN and existing package/#133 regressions.

~~~powershell
python -m pytest tests/test_v8_human_gate_protocol.py -q
python -m pytest tests/test_orchestrator_package.py tests/test_v8_plan_invalidation.py -q
~~~

- [ ] Review contract/security, record RED/GREEN evidence, run git diff --check, and commit.

~~~powershell
git add skills/orchestrator/scripts/gwo_v8/human_gate.py tests/v8_human_gate_test_support.py tests/test_v8_human_gate_protocol.py
git commit -m "feat: define human scope gate contracts"
~~~

## Task 2: Implement Authoritative Tracker/Policy Witness Readback

**Files:** Create human_source.py and tests/test_v8_human_source_readback.py; modify only the read-only approval/source seam in github_snapshot.py.

**Consumes:** Task 1 contracts; existing GitHubReadySnapshotSource, GitHubIssueReadClient, GitHubContentClient, and Policy Witness canonicalization.

**Produces:** GitHubHumanApprovalSource.read(handle, decision, readback_ref) -> HumanSourceReadback. It cannot call a writer or activate a Plan Revision.

The adapter must read a structured gwo.human-approval.v1 record containing Decision ID, classification action, predecessor digest, required_change, exact Evidence digests, approval_state, approval_record_ref, approval_actor_ref, and source_change_digest. For approved, it must read a complete source containing all Campaign Tickets/contracts/blockers, membership, immutable Campaign source/target facts, and product_release when required; read the authoritative Policy Witness; repeat all reads; and return only exact bytes/digests. It must never parse chat/comments/model output/webhooks/local snapshots.

Use this fixed source projection. The ticket entries retain the complete #135 contract/blocker object; the excerpt shows only the fields that the human adapter adds and validates.

~~~json
{
  "kind": "gwo.human-tracker-source.v1",
  "repository": "owner/repo",
  "campaign_key": "campaign:one",
  "target_branch": "main",
  "campaign_source": {"input_ref": "refs/heads/main", "resolved_commit_oid": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "tree_oid": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb", "digest": "cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc"},
  "membership": {"ticket_keys": ["issue:108", "issue:109", "issue:140"], "digest": "dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd"},
  "tickets": [
    {"key": "issue:108", "source": {"ref": "issue:108", "digest": "1111111111111111111111111111111111111111111111111111111111111111"}, "contract": {}, "native_blockers": []},
    {"key": "issue:109", "source": {"ref": "issue:109", "digest": "2222222222222222222222222222222222222222222222222222222222222222"}, "contract": {}, "native_blockers": []},
    {"key": "issue:140", "source": {"ref": "issue:140", "digest": "3333333333333333333333333333333333333333333333333333333333333333"}, "contract": {}, "native_blockers": []}
  ],
  "product_release": null,
  "source_change_digest": "eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee"
}
~~~

- [ ] Write RED tests parametrized over pending, incomplete, rejected, ambiguous, reverted, and out_of_policy; assert no approved bytes. Add tests proving chat/webhook/local snapshots cannot approve and tampered approval/tracker/policy/Evidence/actor returns the exact new code.
- [ ] Run RED.

~~~powershell
python -m pytest tests/test_v8_human_source_readback.py -q
~~~

- [ ] Add a separate read-only GitHubHumanApprovalReadClient.read_human_approval(repository, readback_ref) -> Mapping or None. Keep existing GitHubIssueReadClient mutation-free; use a separate protocol so old doubles remain valid.
- [ ] Implement strict approval validation, complete membership projection, Policy Witness readback, digest recomputation, final double-read comparison, exact source-state mapping, and required-change validation. A source whose digest equals the predecessor is HUMAN_SOURCE_REVERTED.
- [ ] Run GREEN and snapshot/rebuild regressions.

~~~powershell
python -m pytest tests/test_v8_human_source_readback.py -q
python -m pytest tests/test_v8_plancontrol_production.py tests/test_v8_plancontrol_rebuild.py -q
~~~

- [ ] Review that the adapter has only read methods, no mutation reachability, and no partial bundle; run git diff --check and commit.

~~~powershell
git add skills/orchestrator/scripts/gwo_v8/human_source.py skills/orchestrator/scripts/gwo_v8/github_snapshot.py tests/test_v8_human_source_readback.py
git commit -m "feat: add authoritative human source readback"
~~~

## Task 3: Close Runtime Capability Boundary

**Files:** Modify only the capability seam in runtime_gateway.py; create tests/test_v8_human_gate_capability.py.

**Consumes:** Task 1 protocol; #135 CoordinatorCapabilityProof; existing CapabilityPolicy and CapabilityPolicyProof.

**Produces:** private RuntimeGateway.read_human_gate_capability(subject) -> HumanGateCapabilityProof and validate_human_gate_capability(proof).

The proof must bind subject, Policy Witness digest, Gateway configuration digest, Worker/Reviewer CapabilityPolicyProof, and CoordinatorCapabilityProof. It is valid only when all seven existing flags are false: worker_can_edit_issues, worker_can_edit_blockers, worker_can_edit_campaign_membership, worker_can_activate_plan_revision, worker_can_merge, worker_can_expand_authority, worker_can_invoke_global_planning. Coordinator proof must also deny tracker/label/membership mutation, authority grant, activation, merge, delegation, and global planning. Missing/tampered/forbidden proof raises existing PLAN_INVALIDATION_CAPABILITY_PROOF_FAIL_CLOSED. No method writes tracker, plan, merge, or authority.

- [ ] Write RED tests for Worker, Reviewer, and Coordinator forbidden flags, proof digest tamper, and zero mutation counters.

~~~python
@pytest.mark.parametrize("role", ("worker", "reviewer", "coordinator"))
def test_forbidden_capability_fails_closed(capability_harness, role):
    capability_harness.set_forbidden_capability(role, "edit_issues")
    with pytest.raises(RuntimeGatewayError) as error:
        capability_harness.gateway.read_human_gate_capability(
            capability_harness.subject
        )
    assert error.value.code == "PLAN_INVALIDATION_CAPABILITY_PROOF_FAIL_CLOSED"
    assert all(value == 0 for value in capability_harness.mutation_counts().values())
~~~

- [ ] Run RED, implement the private immutable proof by reusing existing proof schemas, and add a structural no-writer conformance assertion for the source/Gateway seam.
- [ ] Run GREEN and RuntimeGateway/#133 regressions.

~~~powershell
python -m pytest tests/test_v8_human_gate_capability.py -q
python -m pytest tests/test_v8_runtime_gateway.py tests/test_v8_plan_invalidation.py -q
~~~

- [ ] Review proof derivation from authoritative effective policy, verify no writer method was added, record RED/GREEN, and commit.

~~~powershell
git add skills/orchestrator/scripts/gwo_v8/runtime_gateway.py tests/test_v8_human_gate_capability.py
git commit -m "feat: prove human gate runtime capabilities"
~~~

## Task 4: Orchestrate the Gate in PlanControl and Persist GitHub State

**Files:** Modify plan_control.py, plan_control_github.py, and plan_control_host.py; create tests/test_v8_human_gate_plancontrol.py and tests/test_v8_human_gate_plancontrol_github.py.

**Consumes:** Tasks 1–3 and all #135 successor interfaces.

**Produces:** private require_human_decision(handle, classification) -> HumanDecisionRecord, advance_human_decision(handle, decision, choice) -> HumanGatePlanReadback, read_replan_budget_policy(handle) -> ReplanBudgetPolicy, and exact GitHub repository read/save methods for Decisions and attempts.

Implement this exact order:

1. On REQUIRE_HUMAN_DECISION, validate #135 Decision and save one HumanDecisionRecord, then exact-read it. Same action/Evidence returns the same record; changed bytes are HUMAN_DECISION_CONFLICT.
2. With no choice, return awaiting_human_choice without calling source, RuntimeGateway, Coordinator, Policy Witness, or Activation Receipt writer.
3. Validate exact HumanDecisionChoice; it is not approval. Call only HumanApprovalSource.read.
4. Persist/exact-read HumanSourceReadback before planning. Pending/incomplete/digest drift returns awaiting_durable_tracker_policy_readback; rejected/ambiguous/reverted/out_of_policy returns rejected_change. No Plan Revision attempt exists in either case.
5. Approved source is validated again for complete membership/contracts/blockers/target/product facts, exact Decision/Evidence, changed required source, and Policy Witness. Authority Grants are compiled from policy only.
6. Build one successor CampaignPlanningSubject and stable action replan:human:<decision-id>; use REPLANNING_OUTPUT_PROTOCOL_ID.
7. Call exactly one planning_preflight and one progress; persist/replay exact Artifacts without a second call.
8. Use #135 deterministic successor compilation. Reject omissions, external Tickets, no-op source, unsupported fields, and mixed Decision/Plan as HUMAN_SUCCESSOR_PLANNING_FAILED with exact nested cause.
9. Reuse #135 publication/CAS/readback helper, but do not call #135 activate_successor as a shortcut. Activation names stable CampaignHandle, exact predecessor, human planning action, complete Ticket keys, PlanSpec digest, and exact claims.
10. Return only after fresh exact readback of receipt/PlanSpec/claims/source/Decision/attempt.

Persist GitHub state schema exactly from #135 v4/v6 to v5/v7. Add immutable categories human_decisions and human_gate_attempts, keyed respectively by (repository, campaign_key, decision_id) and (repository, campaign_key, decision_id, source_readback_digest). Use exact canonical create-or-compare, Artifact hydration, and v4/v6-to-v5/v7 empty-category migration. Unknown fields/categories, changed bytes, missing Artifacts, or failed hydration are DURABLE_STATE_INVALID or HUMAN_GATE_ATTEMPT_READBACK_INVALID. Never infer approval.

- [ ] Write RED tests for one Decision save, no source/planning while outstanding, approved authority/new-Ticket source with one planning pass/activation, negative source states, duplicate save, restart, lost acknowledgements, and GitHub migration.
- [ ] Run focused RED.

~~~powershell
python -m pytest tests/test_v8_human_gate_plancontrol.py tests/test_v8_human_gate_plancontrol_github.py -q
~~~

- [ ] Implement in-memory exact persistence and host composition. Save Decision before source read; source readback before planning; attempt before every external/Gateway boundary. Use stable action:
~~~python
planning_action_id = "replan:human:" + digest_value({
    "decision_id": decision.decision_id,
    "source_readback_digest": source.readback_digest,
    "previous_revision_digest": active.current_revision_digest,
})[:24]
~~~
- [ ] Implement one approved-source Planning Pass and #135 compilation/CAS/readback; map negative source states without publishing. Do not route through start_successor or activate_successor.
- [ ] Implement GitHub v5/v7 serializers, migration, Artifact hydration, and exact conflict behavior.
- [ ] Run GREEN plus existing PlanControl/rebuild/production/successor-host regressions.

~~~powershell
python -m pytest tests/test_v8_human_gate_plancontrol.py tests/test_v8_human_gate_plancontrol_github.py -q
python -m pytest tests/test_v8_plancontrol_production.py tests/test_v8_plancontrol_rebuild.py tests/test_v8_successor_host.py -q
~~~

- [ ] Review one-pass/no-write/durable-order/CAS properties, run git diff --check, and commit.

~~~powershell
git add skills/orchestrator/scripts/gwo_v8/plan_control.py skills/orchestrator/scripts/gwo_v8/plan_control_github.py skills/orchestrator/scripts/gwo_v8/plan_control_host.py tests/test_v8_human_gate_plancontrol.py tests/test_v8_human_gate_plancontrol_github.py
git commit -m "feat: orchestrate approved human successor planning"
~~~

## Task 5: Drive the Gate in ExecutionKernel and Prove Public Acceptance

**Files:** Modify execution_kernel.py, __init__.py, and skills/orchestrator/.skill-package.json; create tests/test_v8_human_gate_execution_kernel.py and tests/test_v8_human_gate_public.py. Do not modify Task 1 support.

**Consumes:** Task 4 host ports, Task 1 HumanDecisionChoice/HumanGateSummary/ReplanBudgetPolicy, #135 successor migration/lineage, and PlanInvalidationObservation.

**Produces:** optional typed human_decision on advance, Diagnostics.human_gate, durable budget/phase/lineage state, and public start -> advance -> inspect proofs.

Persist exactly:

~~~python
state["human_gate"] = {
    "phase": "awaiting_human_choice",
    "decision": decision.canonical(),
    "choice": None,
    "source_readback": None,
    "planning_action_id": None,
    "reason_code": "HUMAN_DECISION_REQUIRED",
}
state["replan_budgets"] = {
    "policy_witness_digest": policy_digest,
    "successor_revisions_used": 0,
    "successor_revision_limit": policy.successor_revision_limit,
    "invalidation_limit": policy.repeated_invalidation_limit,
    "obligations": {},
}
~~~

Use exact keys:

~~~python
obligation_key = digest_value({
    "ticket_key": observation.ticket_key,
    "invalidated_obligation": observation.invalidated_obligation,
    "work_subject_digest": run["work_subject_digest"],
})
evidence_key = observation.evidence_digest
~~~

A duplicate Evidence or dedup identity does not increment a counter, change Decision identity, invoke Coordinator, or consume budget. Distinct Evidence increments one obligation count before a new classification/source route. On repeated-invalidation exhaustion, persist a replan_budget Decision and stop before classify_plan_invalidations. Increment successor count exactly once in the same state write that reconciles the winning exact Activation Receipt. A CAS loser, rejected source, failed Planning, or malformed readback consumes no successor budget.

Status derivation:
- awaiting_human_choice and budget_exhausted -> Decision;
- awaiting_durable_tracker_policy_readback -> Wait;
- planning_validated_successor -> Running;
- active_successor -> ordinary status from successor Work Runs;
- rejected_change -> Decision with exact source code;
- invalid/digest/changed readback -> Wait with exact code.
Inspect exposes only read-only exact Decision/Evidence/source/planning/revision/counter/lineage fields, never source bytes, actor, provider, model, or transcript.

- [ ] Write RED Kernel tests for no-choice quiescence, duplicate Evidence, budget policy readback, phase projection, restart, delayed source, and exact counter increments.

~~~python
def test_outstanding_human_decision_is_quiescent(public_human_gate):
    outcome = public_human_gate.advance_invalidated(
        required_change="new_ticket"
    )
    diagnostics = public_human_gate.inspect()
    assert outcome.status.value == "Decision"
    assert diagnostics.human_gate.phase == "awaiting_human_choice"
    assert public_human_gate.gateway.replan_progresses == 0
    assert all(value == 0 for value in public_human_gate.mutation_counts().values())
~~~

- [ ] Run RED, extend Kernel advance exactly with human_decision: HumanDecisionChoice | None, persist/read back state before external calls, and return Decision without a choice.
- [ ] Implement approval/rejection source mapping. Approved saves planning_validated_successor before PlanControl; negative source states save rejected_change or readback Wait and launch no effects. Any malformed phase/Decision/source/revision/receipt raises HUMAN_GATE_ATTEMPT_READBACK_INVALID or the exact nested #135 code before migration.
- [ ] Implement finite budget policy readback, duplicate suppression, stable exhaustion Decision IDs, complete revision/invalidation lineage, and crash recovery across Decision save, source save, Planning, activation, and migration.
- [ ] Export only HumanDecisionChoice and HumanGateSummary. Do not export records, source adapter, capability proof, attempts, budget policy, or repositories. Run scripts/sync_orchestrator.py.
- [ ] Add public tests using exported start/advance/inspect only:

  1. new Ticket;
  2. changed acceptance;
  3. Campaign membership;
  4. product/release;
  5. broader authority;
  6. rejected change;
  7. incomplete/delayed readback;
  8. duplicate event/repeated advance/restart;
  9. successor budget exhaustion;
  10. repeated-invalidation budget exhaustion.

Public approval flow must first show Decision/awaiting_human_choice, then typed approval with no source visible show Wait/awaiting_durable_tracker_policy_readback, then an externally published approved source followed by a wake activate one successor. Assert CampaignHandle stability, one new Planning Pass, one Activation Receipt, exact source/policy digests, zero prohibited mutation counters, and no old Candidate adoption. Rejection/drift/budget rows assert predecessor receipt remains active and no partial successor.

- [ ] Run GREEN public/Kernel/package/#135 regressions.

~~~powershell
python -m pytest tests/test_v8_human_gate_execution_kernel.py tests/test_v8_human_gate_public.py -q
python -m pytest tests/test_orchestrator_package.py tests/test_v8_canary_runner.py tests/test_v8_successor_plan_revision.py tests/test_v8_execution_kernel.py -q
~~~

- [ ] Review status order, public no-transcript shape, idempotency, complete lineage, zero effects, and budget semantics. Synchronize and commit.

~~~powershell
python scripts/sync_orchestrator.py
git diff --check
git add skills/orchestrator/scripts/gwo_v8/execution_kernel.py skills/orchestrator/scripts/gwo_v8/__init__.py skills/orchestrator/.skill-package.json tests/test_v8_human_gate_execution_kernel.py tests/test_v8_human_gate_public.py
git commit -m "feat: gate successor scope on human readback"
~~~

## Dependency and Interface Handoff

- #135 supplies REQUIRE_HUMAN_DECISION and the six closed required_change values. #136 binds each exact invalidation Evidence digest to HumanDecisionRecord and never lets a model choose ownership, acceptance, membership, product, or authority.
- #135 supplies classification action/snapshot/revision/capability digests, CoordinatorCapabilityProof, tagged _PlanningAttempt, REPLANNING_OUTPUT_PROTOCOL_ID, deterministic successor compilation, exact CAS/readback, and revision lineage. #136 supplies a new approved source/Policy Witness and runs one new tagged pass before reusing that machinery.
- #135 activate_successor(handle, classification) remains the approved-existing-Ticket path. The human path does not call it as a shortcut because source adoption must follow authoritative approval readback.
- The only new private seams are HumanApprovalSource, Decision/attempt persistence, host human-gate delegates, and Kernel human_decision/Diagnostics.human_gate. No Worker, Reviewer, Coordinator, Runtime Profile, Candidate, or public workflow redesign is allowed.

## Final Verification Gate

- [ ] Run the complete focused suite.

~~~powershell
python -m pytest tests/test_v8_human_gate_protocol.py tests/test_v8_human_source_readback.py tests/test_v8_human_gate_capability.py tests/test_v8_human_gate_plancontrol.py tests/test_v8_human_gate_plancontrol_github.py tests/test_v8_human_gate_execution_kernel.py tests/test_v8_human_gate_public.py tests/test_v8_plan_invalidation_classification.py tests/test_v8_successor_planning_protocol.py tests/test_v8_successor_plan.py tests/test_v8_successor_plancontrol.py tests/test_v8_successor_plancontrol_github.py tests/test_v8_successor_execution_kernel.py tests/test_v8_successor_host.py tests/test_v8_successor_plan_revision.py -q
~~~

Expected: all pass, with exactly one new Planning Pass for each approved human successor.

- [ ] Run the full suite and validation.

~~~powershell
python -m pytest -q
python scripts/quick_validate.py
~~~

- [ ] Audit scope and generated state.

~~~powershell
git diff --check
git status --short
git diff --stat origin/main...HEAD
git diff origin/main...HEAD -- skills/orchestrator/scripts/gwo_v8 skills/orchestrator/.skill-package.json tests
~~~

Expected: only the mapped files changed; no SQLite, ArtifactStore, cache, attachment, or legacy V2 output is committed. Record RED failure before every production GREEN result.

## Spec Coverage

| Requirement | Proof |
| --- | --- |
| Decision for new Ticket/acceptance/membership/product/authority with exact Evidence/source | Tasks 1, 4, 5; HumanDecisionRecord and public parametrized matrix |
| Zero mutations by every listed role while outstanding | Tasks 2, 3, 4, 5; read-only source, capability proof, mutation counters |
| No chat/model/webhook/local approval | Task 2 negative readback matrix |
| Exact authoritative tracker/Policy Witness readback | Tasks 2 and 4 double-read and digest checks |
| Rejected/incomplete/ambiguous/reverted/out-of-policy quiescence | Tasks 2, 4, 5 source-state and public tests |
| Finite budgets and duplicate Evidence exclusion | Tasks 1 and 5 counter/duplicate tests |
| Exhaustion one Decision, complete lineage, no new Coordinator pass | Task 5 budget public tests |
| Restart/events/repeated advance/delayed readback idempotence | Tasks 2, 4, 5 crash/replay tests |
| inspect phase distinction without transcript | Tasks 1 and 5 HumanGateSummary/public assertions |
| Public start -> advance -> inspect paths | Task 5 public suite |

## Open Questions and Risks

1. The issue does not name the upstream approval endpoint/storage object. The internal seam is fixed as read_human_approval(repository, readback_ref) and the closed gwo.human-approval.v1 record; the upstream workflow must map its authoritative durable record to that read-only call before Task 2 GREEN. Comments, chat, and webhook payloads remain invalid.
2. The current ready-reference input is not a complete successor membership source. If the upstream workflow exposes only the added Ticket, the adapter must return HUMAN_SOURCE_READBACK_INCOMPLETE rather than merge local state.
3. Product/release facts may not yet be represented by the #135 compiler. They must be bound into the canonical Campaign source/contract digest before Task 4; a model output cannot carry them.
4. The Policy Witness must contain the closed replan object. Missing or changed limits return REPLAN_BUDGET_POLICY_INVALID or REPLAN_BUDGET_READBACK_INVALID; no host default is permitted.
5. An authority approval may change the Policy Witness but cannot reset or increase original Campaign budget counters. A changed budget object fails closed.
6. If merged #135 durable versions differ from v4/v6, re-anchor the execution note and use the next single migration version; never append categories without a schema bump.
7. Source drift between the final double-read and CAS, or a competing CAS winner, must preserve the predecessor and make the existing exact retry path idempotent; it must not create a second Decision or Planning Pass.
8. A rejected Decision ID is terminal. A later approved change uses a new durable approval/source identity and cannot replay the rejected record.
