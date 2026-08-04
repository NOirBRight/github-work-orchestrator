# GWO V8 Batch Delivery and Recovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement Issues #116 and #117 as one real `BatchIntegrator` that consumes only accepted-Candidate receipts, carries one immutable Batch SHA through local verification, publication, PR, hosted CI, serialized target integration, and readback, and recovers deterministically without repeating unaffected work.

**Architecture:** Add a V3 `BatchIntegrator` deep module with a pure formation/compatibility core, a `PatchIdentityV1` tree-delta proof, a rebuildable SQLite delivery journal, and private Git/GitHub/CI/Integration-Lease drivers. The module exposes only typed `prepare`, `readback`, and `execute` operations to the future `ProductionWorkRunEffects` composition; it never calls RuntimeGateway, CandidateGate, an LLM, or the predecessor `Kernel.reconcile_once` path. Keep the predecessor `integration_batch.py` reachable only by its existing V2 compatibility tests until the #118 Cutover Guard removes that writer path.

**Tech Stack:** Python 3.13, pytest, frozen dataclasses, canonical JSON and SHA-256, Git CLI tree/readback operations, SQLite compare-and-swap, GitHub CLI/API readback, and typed fake drivers for deterministic crash and identity tests.

## Global Constraints

- Normative order is `CONTEXT.md`, accepted ADRs, `docs/design/gwo-v8-lean-architecture.md`, `docs/design/gwo-v8-lean-stabilization-spec.md`, and `docs/design/gwo-v8-lean-roadmap.md`.
- This plan implements #116 followed by #117; #116 requires the #110 ExecutionKernel seam and #114 accepted-Candidate receipt, while #117 also requires #115 Review Finding repair context.
- Beta1 is metadata/tracker repair only and is not production admission. These two Tickets are Beta2 feature-complete-preview work; they do not activate the V3 writer, change the default production path, or claim GA.
- The public workflow remains exactly `start(repository, ready_refs, options?)`, `advance(campaign_handle, wake_ref?)`, and `inspect(campaign_handle)`; this plan adds no public workflow operation or public status.
- Batch formation is same-Campaign, oldest-first, deterministic, and bounded to one through four members, with default four and a repository override.
- Strict Assurance, gitlink changes, and policy-classified protected, high-coupling, or non-decomposable Interaction Keys always produce Singleton Batches.
- Every external effect has a stable delivery action identity, is preceded by authoritative readback, and is persisted without holding a SQLite transaction across Git, GitHub, or CI I/O.
- The exact composed Batch SHA is the local-suite subject, pushed branch head, pull-request head, hosted-CI head, and serialized integration identity; target readback may be a merge commit only when it proves the Batch SHA as an ancestor through the PR merge mapping.
- Every complete observation binds exact delivery readbacks in `delivery_proofs`: one proof for a direct multi-member or initial Singleton Batch, or one ordered child Singleton proof per parent member after fallback. A fallback parent never fabricates a proof for its failed original Batch.
- Infrastructure failure retries the unchanged Batch SHA at most twice. Composition, exact-local, and code-class hosted failures dissolve one multi-member Batch once into deterministic Singleton Batches; there is no recursive split, regrouping search, or LLM attribution.
- `DeliveryIdentityMismatch` and `DeliveryAttributionAmbiguous` preserve all Candidate observations and Evidence and authorize neither Singleton fallback nor Worker resume.
- BatchIntegrator never mutates an accepted Candidate, creates a Candidate, creates a Review Subject, repeats Formal Review, consumes a Worker Slot, resumes a Worker, or expands Authority Grants. A failing Singleton returns a typed resume directive to the owning ExecutionKernel composition.
- All implementation work follows RED, prove RED, minimal GREEN, prove GREEN, refactor while green, and a focused/full verification gate. Python commands use `py -3.13`.
- The existing primary checkout `D:\Workstation\github-work-orchestrator` is not used. Execution starts from a clean worktree at the merged #110/#114 (and, for recovery, #115) Result commits.
- #115 and this plan's #116/#117 package-changing work use one **serial manifest lane**. The #115 package Result commit must finish before any #116 Task 1 package change starts; do not implement or commit those package-changing lanes in parallel even when their source files appear disjoint, because both update `skills/orchestrator/.skill-package.json`. The Batch tasks themselves remain ordered 1 through 10.
- Every commit that changes a package-listed file runs `py -3.13 scripts/sync_orchestrator.py`, immediately runs `py -3.13 scripts/sync_orchestrator.py --check`, and stages `skills/orchestrator/.skill-package.json` in that same commit. Manifest synchronization cannot be deferred to a later commit. Only work that changes no package manifest—read-only review, documentation-only edits, or external readback observation—may run in parallel, and it must not share a write set with the serial manifest lane.

---

## Current Baseline and Dependency Boundaries

The inspected baseline has three different delivery layers:

1. `skills/orchestrator/scripts/gwo_v8/integration_batch.py` contains only `GitIntegrationBatchAssembler`, `IntegrationBatch`, and `IntegrationBatchMember`. It sorts members, checks a shared base, creates a local merge commit, and stores a content-derived ref. It has no CandidateGate receipt contract, compatibility policy, local suite, PR, hosted receipt, target merge mapping, durable action recovery, or Singleton fallback.
2. `skills/orchestrator/scripts/gwo_v8/kernel.py` owns the predecessor `Kernel`, `DeliveryControl`, `GitHubCliDeliveryControl`, `v8_integration_leases`, and `v8_integration_batches`. Its `_advance_integration_batch` method is a V2/transitional workflow driver and must not be used by the V3 host.
3. `skills/orchestrator/scripts/gwo_v8/execution_kernel.py` is the V3 public-state machine. Its `WorkRunEffects` seam is currently:

```python
class WorkRunEffects(Protocol):
    def readback(self, action: WorkRunAction) -> WorkRunObservation | None: ...
    def execute(self, action: WorkRunAction) -> WorkRunObservation: ...
```

`ProductionWorkRunEffects` in the separate production-composition plan will map a `WorkRunAction(kind="batch_delivery")` to this plan's `BatchIntegrator.readback` and `BatchIntegrator.execute`, then translate the typed delivery observation into `WorkRunObservation`. This plan does not change `ExecutionKernel`'s public surface or duplicate its lifecycle state machine.

There is no `domain_store.py` in this checkout or elsewhere under `D:\Workstation`; the current SQLite schema is created by `Kernel.ensure_store_schema` and `ExecutionKernel.__init__`. Do not invent a second domain-store abstraction. `BatchDeliveryJournal` below adds versioned, module-private delivery records to the injected `store_path`; the future production host supplies the same SQLite path to `ExecutionKernel` and `BatchIntegrator`.

The accepted dependency boundary is:

```text
CandidateGate (#114/#115)
    -> AcceptedCandidateReceipt
    -> BatchIntegrator (#116/#117)
    -> BatchDeliveryObservation
    -> ProductionWorkRunEffects / ExecutionKernel (#110 composition)
```

The #114 foundation owns `CandidateReceipt`, the authoritative pre-acceptance
Candidate value. The #114/#115 CandidateGate handoff owns the immutable
`AcceptedCandidateReceipt` in `candidate_gate.py`; it points back to the
persisted Candidate receipt and adds only the verified acceptance facts needed
by delivery. `BatchIntegrator` imports that value type from
`.candidate_gate` and consumes it; it never declares a second receipt class,
reconstructs `CandidateDiffRecordV1`, or re-runs Review. It may import
canonical helpers and that receipt value type, but it must not import
CandidateGate implementation classes, `runtime_gateway.py`, `kernel.py`,
`integration_batch.py`, `goal_driver.py`, or any provider/model/CLI runtime
configuration. The receipt handoff is defined below so #114/#115 can produce
it without importing this module's drivers.

The handoff is one-way and lossless: CandidateGate sets
`candidate_receipt_digest` to the persisted #114 `CandidateReceipt.digest`,
copies its authoritative base/Candidate commit and tree OIDs into `base_sha`,
`candidate_sha`, `base_tree_oid`, and `candidate_tree_oid`, and adds the
accepted Review/Assurance/Policy/Evidence facts before emitting the immutable
accepted receipt. BatchIntegrator reads those fields directly; it does not
adapt or recompute a Candidate receipt.

### Serial manifest lane

The #115 CandidateGate Result is a hard predecessor, not a parallel work
stream. First merge its package-changing Result commit, including its own
`sync_orchestrator.py` → `sync_orchestrator.py --check` → same-commit
`skills/orchestrator/.skill-package.json` staging. Only then begin Task 1 of
this plan. Tasks 1–10 run in order on the same serial manifest lane; a task may
not leave package synchronization for a later task or commit. A separate
worker is allowed only for a read-only review, documentation-only change, or
external readback whose write set contains no package manifest and does not
overlap this lane. This is the only permitted parallelism; #115 and #116
package-changing commits are never concurrent.

## File and Responsibility Map

| File | Responsibility |
| --- | --- |
| `skills/orchestrator/scripts/gwo_v8/candidate_gate.py` | #114/#115-owned `CandidateReceipt`, `AcceptedCandidateReceipt`, `InteractionClassification`, and `InteractionKey`; CandidateGate derives these keys from its exact diff and this plan only consumes the frozen handoff. |
| `skills/orchestrator/scripts/gwo_v8/batch_integrator.py` | V3 `BatchIntegrator`, immutable delivery request/action/observation types, deterministic queue formation, compatibility decisions, delivery state machine, Singleton fallback, and typed identity/failure outcomes. |
| `skills/orchestrator/scripts/gwo_v8/batch_patch_identity.py` | Pure `PatchIdentityV1` encoding, raw Git-tree delta records, and Clean Base Advance proof values. No GitHub, SQLite, or runtime imports. |
| `skills/orchestrator/scripts/gwo_v8/_batch_integrator_store.py` | `BatchDeliveryJournal`, schema creation, action/receipt compare-and-swap, durable hosted-result adoption, and repository-global Integration Lease CAS. |
| `skills/orchestrator/scripts/gwo_v8/_batch_integrator_drivers.py` | Private Git, local-suite, publication/PR, hosted-CI, target-integration, and target-readback protocols plus production adapters. These names are not exported from `gwo_v8`. |
| `skills/orchestrator/scripts/gwo_v8/__init__.py` | Export `BatchIntegrator` and its immutable delivery boundary values, but not `AcceptedCandidateReceipt`; CandidateGate owns that export. Stop exporting `GitIntegrationBatchAssembler` as a V3 boundary. Existing predecessor imports remain direct to `integration_batch.py` until #118. |
| `skills/orchestrator/scripts/gwo_v8/integration_batch.py` | Add a predecessor-only module docstring and preserve the old adapter solely for existing V2 regression tests; no new V3 code may import it. #118 deletes this path with the other legacy writer paths. |
| `tests/v8_batch_test_support.py` | Real temporary Git repository builder, valid receipt factory, fake private drivers, SQLite crash injection, and exact readback counters. |
| `tests/test_v8_batch_integrator.py` | #116 receipt validation, formation, compatibility, PatchIdentityV1, local composition, lease, and exact delivery tests. |
| `tests/test_v8_batch_recovery.py` | #117 hosted receipt adoption, bounded infrastructure retry, Singleton fallback, restart convergence, wrong identity, and ambiguous attribution tests. |
| `tests/test_orchestrator_v8_integration_batch.py` | Change imports to the direct predecessor module and retain only the two explicitly marked predecessor assembler regression tests. |
| `tests/test_v8_batch_beta2.py` | Four-member Beta2 boundary evidence: three compatible Standard Candidates, one Strict Singleton, exact SHA mapping, and restart/failure evidence. |
| `scripts/write_v8_batch_evidence.py` | Resolve merged refs, derive pytest counts from JUnit, validate exact CI/Git/receipt readbacks, and deterministically generate or check the Beta2 evidence document. |
| `docs/e2e/gwo-v8-batch-integrator.md` | Record the merged Result SHAs, focused/full test evidence, isolated delivery readback, and the Beta2 exit decision without claiming cutover or GA. |

No task in this plan modifies `skills/orchestrator/scripts/gwo_v8/execution_kernel.py`, `skills/orchestrator/scripts/gwo_v8/kernel.py`, `skills/orchestrator/scripts/gwo_v8/reconstruction.py`, or `scripts/run_v8_canary.py`. The later production-composition plan owns their V3 wiring and the #118 plan owns physical legacy-path removal.

## Exact V3 Boundary Types

The following values are the contract that all tasks use. They are frozen,
canonical, and digest-addressed. The actual implementation may keep
validation helpers private, but it must retain these names, fields, and
semantics. Code shown as owned by `candidate_gate.py` is an upstream #114/#115
contract; it is not redefined in `batch_integrator.py`.

```python
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Literal, Protocol

from ._canonical import digest_value
from .candidate_gate import AcceptedCandidateReceipt, InteractionClassification, InteractionKey
```

`candidate_gate.py` is the sole owner of these interaction values. CandidateGate
derives them from the authoritative `CandidateDiffRecordV1`; BatchIntegrator
only imports and compares the exact upstream values. This plan intentionally
contains no mirror class or enum definition: the only BatchIntegrator import
is `from .candidate_gate import AcceptedCandidateReceipt,
InteractionClassification, InteractionKey`.

The upstream contract is fixed as follows: `InteractionClassification` has the
values `ordinary`, `protected`, `high_coupling`, and `non_decomposable`;
`InteractionKey` has exactly `namespace: str`, `value: str`, and
`classification: InteractionClassification`; its upstream `requires_singleton`
accessor is true for the last three values, and its upstream `canonical()`
mapping is `{ "namespace": namespace, "value": value,
"classification": classification.value }`. CandidateGate, not this plan's
support code, implements and validates that contract.

The upstream `candidate_gate.py` implementation must also expose the frozen
`AcceptedCandidateReceipt` with these exact fields and accessors (the existing
#114 `CandidateReceipt` remains the source for persisted Candidate facts):

```text
repository, campaign_key, plan_revision_digest, target_branch, ticket_key,
work_run_key, integration_node_key, accepted_sequence, base_sha,
base_tree_oid, candidate_sha, candidate_tree_oid, candidate_receipt_digest,
diff_schema_version="CandidateDiffRecordV1", diff_record_digest,
authority_subtree_digest, policy_witness_digest, review_subject_digest,
assurance="standard" | "strict", assurance_requirement_digest,
check_environment_digest, delivery_identity_digest, interaction_keys,
protected_surfaces, gitlink_change, evidence_digests,
review_finding_ledger_digest
```

It provides `digest: str` and `canonical() -> dict[str, object]`; `digest` is
the accepted-receipt digest produced and validated by CandidateGate. It has no
`result_digest`. BatchIntegrator imports this exact type and never shadows it.

```python
@dataclass(frozen=True)
class BatchTarget:
    repository: str
    target_branch: str
    target_head_sha: str
    target_tree_oid: str
    target_facts_digest: str


@dataclass(frozen=True)
class LocalSuiteDefinition:
    suite_id: str
    definition_digest: str
    command: tuple[str, ...]


@dataclass(frozen=True)
class HostedSuiteDefinition:
    suite_id: str
    hosted_name: str
    definition_digest: str


@dataclass(frozen=True)
class BatchIntegratorConfiguration:
    host_member_limit: int = 4
    repository_member_limits: dict[str, int] | None = None
    infrastructure_retry_limit: int = 2

    def __post_init__(self) -> None:
        if not 1 <= self.host_member_limit <= 4:
            raise BatchIntegratorError(
                "BATCH_MEMBER_LIMIT_INVALID",
                "member limit must be between one and four",
            )
        if self.infrastructure_retry_limit != 2:
            raise BatchIntegratorError(
                "BATCH_RETRY_POLICY_INVALID",
                "infrastructure retry limit is fixed at two",
            )
        for repository, limit in (self.repository_member_limits or {}).items():
            if not 1 <= limit <= 4:
                raise BatchIntegratorError(
                    "BATCH_MEMBER_LIMIT_INVALID",
                    f"member limit for {repository} must be between one and four",
                )

    def member_limit_for(self, repository: str) -> int:
        limit = (self.repository_member_limits or {}).get(
            repository, self.host_member_limit
        )
        if not 1 <= limit <= 4:
            raise BatchIntegratorError(
                "BATCH_MEMBER_LIMIT_INVALID",
                "member limit must be between one and four",
            )
        return limit


@dataclass(frozen=True)
class BatchDeliveryRequest:
    stable_action_id: str
    repository: str
    campaign_key: str
    plan_revision_digest: str
    target: BatchTarget
    accepted_candidates: tuple[AcceptedCandidateReceipt, ...]
    local_suite: LocalSuiteDefinition
    hosted_suites: tuple[HostedSuiteDefinition, ...]
    writer_generation: str
    activation_id: str

    @property
    def request_digest(self) -> str:
        return digest_value(
            {
                "kind": "batch-delivery-request.v1",
                "stable_action_id": self.stable_action_id,
                "repository": self.repository,
                "campaign_key": self.campaign_key,
                "plan_revision_digest": self.plan_revision_digest,
                "target": {
                    "repository": self.target.repository,
                    "target_branch": self.target.target_branch,
                    "target_head_sha": self.target.target_head_sha,
                    "target_tree_oid": self.target.target_tree_oid,
                    "target_facts_digest": self.target.target_facts_digest,
                },
                "accepted_candidates": [
                    candidate.canonical() for candidate in self.accepted_candidates
                ],
                "local_suite": {
                    "suite_id": self.local_suite.suite_id,
                    "definition_digest": self.local_suite.definition_digest,
                    "command": list(self.local_suite.command),
                },
                "hosted_suites": [
                    {
                        "suite_id": suite.suite_id,
                        "hosted_name": suite.hosted_name,
                        "definition_digest": suite.definition_digest,
                    }
                    for suite in self.hosted_suites
                ],
                "writer_generation": self.writer_generation,
                "activation_id": self.activation_id,
            }
        )


@dataclass(frozen=True)
class BatchDeliveryAction:
    stable_action_id: str
    request_digest: str
    batch_id: str
    batch_sha: str
    member_ticket_keys: tuple[str, ...]


@dataclass(frozen=True)
class MemberDeliveryObservation:
    ticket_key: str
    work_run_key: str
    candidate_sha: str
    status: Literal["integrated", "preserved", "resume_required", "blocked"]
    evidence_digests: tuple[str, ...]
    resume_reason: str | None = None


@dataclass(frozen=True)
class BatchDeliveryProof:
    delivery_stable_action_id: str
    delivery_request_digest: str
    batch_id: str
    batch_sha: str
    member_ticket_keys: tuple[str, ...]
    local_check_receipt_digest: str
    publication_receipt_digest: str
    pull_request_number: int
    pull_request_head_sha: str
    hosted_result_receipt_digest: str
    integration_lease_digest: str
    target_branch: str
    target_head_sha: str
    target_readback_digest: str
    target_contains_batch_sha: bool
    pull_request_merge_target_sha: str
    merge_method: Literal["merge"]
    proof_digest: str

    def body(self) -> dict[str, object]:
        return {
            "delivery_stable_action_id": self.delivery_stable_action_id,
            "delivery_request_digest": self.delivery_request_digest,
            "batch_id": self.batch_id,
            "batch_sha": self.batch_sha,
            "member_ticket_keys": list(self.member_ticket_keys),
            "local_check_receipt_digest": self.local_check_receipt_digest,
            "publication_receipt_digest": self.publication_receipt_digest,
            "pull_request_number": self.pull_request_number,
            "pull_request_head_sha": self.pull_request_head_sha,
            "hosted_result_receipt_digest": self.hosted_result_receipt_digest,
            "integration_lease_digest": self.integration_lease_digest,
            "target_branch": self.target_branch,
            "target_head_sha": self.target_head_sha,
            "target_readback_digest": self.target_readback_digest,
            "target_contains_batch_sha": self.target_contains_batch_sha,
            "pull_request_merge_target_sha": self.pull_request_merge_target_sha,
            "merge_method": self.merge_method,
        }

    @classmethod
    def create(cls, **facts: object) -> "BatchDeliveryProof":
        body = dict(facts)
        return cls(
            **body,
            proof_digest=digest_value(
                {"kind": "batch-delivery-proof.v1", **body}
            ),
        )

    def canonical(self) -> dict[str, object]:
        digest_values = (
            self.delivery_request_digest,
            self.batch_id,
            self.local_check_receipt_digest,
            self.publication_receipt_digest,
            self.hosted_result_receipt_digest,
            self.integration_lease_digest,
            self.target_readback_digest,
            self.proof_digest,
        )
        object_ids = (
            self.batch_sha,
            self.pull_request_head_sha,
            self.target_head_sha,
            self.pull_request_merge_target_sha,
        )
        if (
            not isinstance(self.delivery_stable_action_id, str)
            or not self.delivery_stable_action_id
            or not isinstance(self.target_branch, str)
            or not self.target_branch
            or type(self.member_ticket_keys) is not tuple
            or not self.member_ticket_keys
            or any(
                not isinstance(ticket_key, str) or not ticket_key
                for ticket_key in self.member_ticket_keys
            )
            or len(set(self.member_ticket_keys)) != len(self.member_ticket_keys)
            or type(self.pull_request_number) is not int
            or self.pull_request_number <= 0
            or any(
                not isinstance(value, str)
                or len(value) != 64
                or any(character not in "0123456789abcdef" for character in value)
                for value in digest_values
            )
            or any(
                not isinstance(value, str)
                or len(value) not in {40, 64}
                or any(character not in "0123456789abcdef" for character in value)
                for value in object_ids
            )
        ):
            raise DeliveryIdentityMismatch(
                "Batch delivery proof has an invalid action, digest, object, PR, or member identity"
            )
        expected = digest_value(
            {"kind": "batch-delivery-proof.v1", **self.body()}
        )
        if self.proof_digest != expected:
            raise DeliveryIdentityMismatch(
                "Batch delivery proof digest changed after readback"
            )
        if (
            self.pull_request_head_sha != self.batch_sha
            or self.target_contains_batch_sha is not True
            or self.pull_request_merge_target_sha != self.target_head_sha
            or self.merge_method != "merge"
        ):
            raise DeliveryIdentityMismatch(
                "Batch delivery proof does not preserve exact PR and target identity"
            )
        return {**self.body(), "proof_digest": self.proof_digest}


@dataclass(frozen=True)
class BatchDeliveryObservation:
    stable_action_id: str
    batch_id: str
    batch_sha: str
    phase: Literal["running", "wait", "complete", "decision", "blocked"]
    reason: str
    receipt_digest: str
    retry_count: int
    fallback_generation: int
    members: tuple[MemberDeliveryObservation, ...]
    delivery_proofs: tuple[BatchDeliveryProof, ...]

    def __post_init__(self) -> None:
        if (
            self.phase not in {"running", "wait", "complete", "decision", "blocked"}
            or type(self.retry_count) is not int
            or self.retry_count < 0
            or type(self.fallback_generation) is not int
            or self.fallback_generation not in {0, 1}
            or type(self.members) is not tuple
            or type(self.delivery_proofs) is not tuple
        ):
            raise DeliveryIdentityMismatch(
                "Batch observation phase, retry, fallback, members, or proofs are invalid"
            )
        if self.phase == "complete":
            if not self.delivery_proofs:
                raise DeliveryIdentityMismatch(
                    "complete Batch observation has no exact delivery proofs"
                )
            for proof in self.delivery_proofs:
                proof.canonical()
            member_ticket_keys = tuple(member.ticket_key for member in self.members)
            proof_ticket_keys = tuple(
                ticket_key
                for proof in self.delivery_proofs
                for ticket_key in proof.member_ticket_keys
            )
            if (
                any(member.status != "integrated" for member in self.members)
                or len(set(proof_ticket_keys)) != len(proof_ticket_keys)
                or proof_ticket_keys != member_ticket_keys
            ):
                raise DeliveryIdentityMismatch(
                    "complete Batch proof partition does not exactly cover integrated members"
                )
            if self.fallback_generation == 0:
                direct = self.delivery_proofs
                if (
                    len(direct) != 1
                    or direct[0].delivery_stable_action_id != self.stable_action_id
                    or direct[0].batch_id != self.batch_id
                    or direct[0].batch_sha != self.batch_sha
                ):
                    raise DeliveryIdentityMismatch(
                        "direct Batch proof does not match its action and Batch identity"
                    )
            elif self.fallback_generation == 1:
                if (
                    len(self.members) <= 1
                    or len(self.delivery_proofs) != len(self.members)
                    or any(len(proof.member_ticket_keys) != 1 for proof in self.delivery_proofs)
                    or any(
                        proof.delivery_stable_action_id == self.stable_action_id
                        for proof in self.delivery_proofs
                    )
                    or len(
                        {
                            proof.delivery_stable_action_id
                            for proof in self.delivery_proofs
                        }
                    )
                    != len(self.delivery_proofs)
                    or len({proof.batch_id for proof in self.delivery_proofs})
                    != len(self.delivery_proofs)
                    or len({proof.batch_sha for proof in self.delivery_proofs})
                    != len(self.delivery_proofs)
                ):
                    raise DeliveryIdentityMismatch(
                        "fallback parent must bind one distinct Singleton proof per member"
                    )
            else:
                raise DeliveryIdentityMismatch(
                    "complete Batch observation has an invalid fallback generation"
                )
        elif self.delivery_proofs:
            raise DeliveryIdentityMismatch(
                "non-complete Batch observation cannot claim delivery proofs"
            )
        if self.receipt_digest != digest_value(
            {"kind": "batch-observation.v1", **self.body()}
        ):
            raise DeliveryIdentityMismatch(
                "Batch observation receipt does not bind exact delivery proof"
            )

    def body(self) -> dict[str, object]:
        return {
            "stable_action_id": self.stable_action_id,
            "batch_id": self.batch_id,
            "batch_sha": self.batch_sha,
            "phase": self.phase,
            "reason": self.reason,
            "retry_count": self.retry_count,
            "fallback_generation": self.fallback_generation,
            "members": [asdict(member) for member in self.members],
            "delivery_proofs": [
                proof.canonical() for proof in self.delivery_proofs
            ],
        }

    def canonical(self) -> dict[str, object]:
        return {**self.body(), "receipt_digest": self.receipt_digest}


@dataclass(frozen=True)
class AncestorReadback:
    ancestor_sha: str
    descendant_sha: str
    is_ancestor: bool
    readback_digest: str

    def body(self) -> dict[str, object]:
        return {
            "ancestor_sha": self.ancestor_sha,
            "descendant_sha": self.descendant_sha,
            "is_ancestor": self.is_ancestor,
        }

    def validate(self) -> None:
        if digest_value({"kind": "ancestor-readback.v1", **self.body()}) != self.readback_digest:
            raise BatchIntegratorError(
                "CLEAN_BASE_ANCESTOR_READBACK_MISMATCH",
                "ancestor readback digest is not authoritative",
            )


@dataclass(frozen=True)
class TargetDeltaReadback:
    base_sha: str
    target_head_sha: str
    interaction_keys: tuple[InteractionKey, ...]
    protected_interaction_keys: tuple[InteractionKey, ...]
    facts_digest: str
    readback_digest: str

    def body(self) -> dict[str, object]:
        return {
            "base_sha": self.base_sha,
            "target_head_sha": self.target_head_sha,
            "interaction_keys": [key.canonical() for key in self.interaction_keys],
            "protected_interaction_keys": [
                key.canonical() for key in self.protected_interaction_keys
            ],
        }

    def canonical(self) -> dict[str, object]:
        body = self.body()
        if digest_value(body) != self.facts_digest:
            raise BatchIntegratorError(
                "TARGET_DELTA_FACTS_DIGEST_MISMATCH",
                "target delta Interaction Key facts are not canonical",
            )
        if digest_value({"kind": "target-delta-readback.v1", **body}) != self.readback_digest:
            raise BatchIntegratorError(
                "TARGET_DELTA_READBACK_DIGEST_MISMATCH",
                "target delta readback is not authoritative",
            )
        return body


class BatchIntegratorError(RuntimeError):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


class DeliveryIdentityMismatch(BatchIntegratorError):
    def __init__(self, detail: str) -> None:
        super().__init__("DELIVERY_IDENTITY_MISMATCH", detail)


class DeliveryAttributionAmbiguous(BatchIntegratorError):
    def __init__(self, detail: str) -> None:
        super().__init__("DELIVERY_ATTRIBUTION_AMBIGUOUS", detail)


class BatchIntegrator:
    def __init__(
        self,
        *,
        journal: "BatchDeliveryJournal",
        git: GitBatchDriver,
        local: LocalSuiteDriver,
        hosted: HostedBatchDriver,
        configuration: BatchIntegratorConfiguration,
    ) -> None:
        self.journal = journal
        self.git = git
        self.local = local
        self.hosted = hosted
        self.configuration = configuration
        self.formation_calls = 0
        self._requests: dict[str, BatchDeliveryRequest] = {}

    def prepare(self, request: BatchDeliveryRequest) -> BatchDeliveryAction:
        self._validate_request(request)
        self.formation_calls += 1
        if not request.accepted_candidates:
            raise BatchIntegratorError("BATCH_EMPTY", "accepted candidate set is empty")
        batch_id = digest_value(
            {"kind": "batch-id.v1", "request_digest": request.request_digest}
        )
        return BatchDeliveryAction(
            stable_action_id=request.stable_action_id,
            request_digest=request.request_digest,
            batch_id=batch_id,
            batch_sha=request.accepted_candidates[0].candidate_sha,
            member_ticket_keys=tuple(
                item.ticket_key for item in request.accepted_candidates
            ),
        )

    def readback(
        self, action: BatchDeliveryAction
    ) -> BatchDeliveryObservation | None:
        return None

    def execute(self, action: BatchDeliveryAction) -> BatchDeliveryObservation:
        existing = self.readback(action)
        if existing is not None:
            return existing
        raise BatchIntegratorError(
            "BATCH_EXECUTION_NOT_READY",
            "delivery execution is installed by the later action-loop task",
        )

    @staticmethod
    def _validate_request(request: BatchDeliveryRequest) -> None:
        if request.target.repository != request.repository:
            raise BatchIntegratorError(
                "BATCH_TARGET_REPOSITORY_MISMATCH",
                "request and target repository differ",
            )
```

The `AcceptedCandidateReceipt` has no `result_digest`: the architecture says a code-producing Result exists only after exact Batch integration and target readback. Its `review_finding_ledger_digest` lets a failing Singleton return repair context without reading or modifying CandidateGate's ledger.

The private driver boundary is:

```python
class GitBatchDriver(Protocol):
    def read_target(self, target: BatchTarget) -> BatchTarget: ...
    def read_ancestor(
        self, ancestor_sha: str, descendant_sha: str
    ) -> AncestorReadback: ...
    def read_target_delta(
        self, base_sha: str, target: BatchTarget
    ) -> TargetDeltaReadback: ...
    def read_ref(self, ref: str) -> str | None: ...
    def update_ref_cas(
        self, ref: str, expected_sha: str | None, new_sha: str
    ) -> str: ...
    def compose_batch(
        self,
        batch_id: str,
        target: BatchTarget,
        members: tuple[AcceptedCandidateReceipt, ...],
    ) -> str: ...
    def clean_base_advance(
        self,
        batch_id: str,
        target: BatchTarget,
        member: AcceptedCandidateReceipt,
    ) -> "CleanBaseAdvanceProof": ...


class LocalSuiteDriver(Protocol):
    def run(
        self, batch_sha: str, suite: LocalSuiteDefinition
    ) -> "LocalCheckReceipt": ...


class HostedBatchDriver(Protocol):
    def read_publication(self, repository: str, batch_sha: str) -> "BatchPublicationReceipt | None": ...
    def publish_once(
        self, repository: str, batch_sha: str, manifest_digest: str
    ) -> "BatchPublicationReceipt": ...
    def read_pull_request(self, repository: str, batch_sha: str) -> "PullRequestReadback": ...
    def read_hosted_result(
        self, repository: str, batch_sha: str, suite: HostedSuiteDefinition
    ) -> "HostedResultObservation": ...
    def retry_hosted(
        self, repository: str, batch_sha: str, provider_check_id: str
    ) -> None: ...
    def integrate_serially(
        self, repository: str, batch_sha: str, target: BatchTarget, pull_request: "PullRequestReadback"
    ) -> "TargetIntegrationReadback": ...
```

`BatchPublicationReceipt`, `PullRequestReadback`, `HostedResultObservation`, `TargetIntegrationReadback`, `LocalCheckReceipt`, `BatchJournalRecord`, and `IntegrationLeaseReceipt` are frozen private-driver values with exact SHA, suite, provider-check, target, version, and receipt-digest validation. Their concrete fields are listed in the tasks that introduce them.

---

### Task 1: Freeze the accepted-Candidate and delivery identity contract

**Files:**
- Create: `skills/orchestrator/scripts/gwo_v8/batch_integrator.py`
- Create: `tests/v8_batch_test_support.py`
- Create: `tests/test_v8_batch_integrator.py`
- Modify: `skills/orchestrator/scripts/gwo_v8/__init__.py`

**Interfaces:**
- Consumes: the #114/#115 accepted-Candidate receipt facts named above; no CandidateGate or Runtime implementation import.
- Consumes the immutable `AcceptedCandidateReceipt`, `InteractionClassification`, and `InteractionKey` imported from `gwo_v8.candidate_gate`; it does not produce or redefine CandidateGate's diff-derived delivery facts. Produces suite definitions, request/action/observation values, the ordered `BatchDeliveryProof` partition later filled only from exact delivery readbacks, and typed `BatchIntegratorError` subclasses for all later tasks.

- [ ] **Step 1: Write the failing validation tests.**

Add these exact tests to `tests/test_v8_batch_integrator.py`:

```python
def test_accepted_candidate_receipt_digest_binds_every_delivery_fact():
    first = make_accepted_candidate_receipt(ticket_key="issue:1", candidate_sha="a" * 40)
    changed = make_accepted_candidate_receipt(
        ticket_key="issue:1", candidate_sha="a" * 40,
        delivery_identity_digest="b" * 64,
    )

    assert first.digest != changed.digest
    assert first.canonical()["diff_schema_version"] == "CandidateDiffRecordV1"
    assert first.canonical()["review_finding_ledger_digest"] == first.review_finding_ledger_digest


def test_accepted_candidate_receipt_rejects_noncanonical_evidence_and_sequence():
    with pytest.raises(BatchIntegratorError, match="accepted_sequence"):
        make_accepted_candidate_receipt(accepted_sequence=-1)
    with pytest.raises(BatchIntegratorError, match="evidence_digests"):
        make_accepted_candidate_receipt(evidence_digests=("f" * 64, "e" * 64))


def test_batch_request_digest_changes_when_member_set_or_target_changes():
    request = make_batch_request(
        accepted_candidates=(make_accepted_candidate_receipt(ticket_key="issue:1"),)
    )
    changed_members = replace(
        request,
        accepted_candidates=(
            request.accepted_candidates[0],
            make_accepted_candidate_receipt(ticket_key="issue:2", accepted_sequence=2),
        ),
    )
    changed_target = replace(request, target=replace(request.target, target_head_sha="b" * 40))

    assert request.request_digest != changed_members.request_digest
    assert request.request_digest != changed_target.request_digest
```

`tests/v8_batch_test_support.py` must define these exact factories:

```python
from dataclasses import replace
from typing import Literal

from gwo_v8._canonical import digest_value
from gwo_v8.batch_integrator import (
    BatchDeliveryRequest,
    BatchIntegrator,
    BatchIntegratorConfiguration,
    BatchIntegratorError,
    BatchTarget,
    DeliveryAttributionAmbiguous,
    HostedSuiteDefinition,
    LocalSuiteDefinition,
)
from gwo_v8.candidate_gate import (
    AcceptedCandidateReceipt,
    InteractionClassification,
    InteractionKey,
)


def make_interaction_key(
    value: str = "api:ordinary", *, classification: InteractionClassification = InteractionClassification.ORDINARY
) -> InteractionKey:
    return InteractionKey(namespace="test", value=value, classification=classification)

def make_accepted_candidate_receipt(
    *,
    repository: str = "owner/repo",
    campaign_key: str = "campaign:test",
    target_branch: str = "main",
    ticket_key: str = "issue:1",
    candidate_sha: str = "c" * 40,
    accepted_sequence: int = 1,
    base_sha: str = "b" * 40,
    base_tree_oid: str = "1" * 40,
    candidate_tree_oid: str | None = None,
    delivery_identity_digest: str = "d" * 64,
    evidence_digests: tuple[str, ...] = ("e" * 64,),
    assurance: Literal["standard", "strict"] = "standard",
    interaction_keys: tuple[InteractionKey, ...] | None = None,
    protected_surfaces: tuple[str, ...] = (),
    gitlink_change: bool = False,
) -> AcceptedCandidateReceipt:
    index = accepted_sequence
    actual_candidate_sha = (
        candidate_sha
        if candidate_sha != "c" * 40
        else f"{index + 10:040x}"
    )
    actual_candidate_tree_oid = candidate_tree_oid or f"{index + 100:040x}"
    actual_interaction_keys = (
        interaction_keys
        if interaction_keys is not None
        else (make_interaction_key(f"api:{ticket_key}"),)
    )
    return AcceptedCandidateReceipt(
        repository=repository,
        campaign_key=campaign_key,
        plan_revision_digest="1" * 64,
        target_branch=target_branch,
        ticket_key=ticket_key,
        work_run_key=f"work-run:{index}",
        integration_node_key=f"integration:{index}",
        accepted_sequence=accepted_sequence,
        base_sha=base_sha,
        base_tree_oid=base_tree_oid,
        candidate_sha=actual_candidate_sha,
        candidate_tree_oid=actual_candidate_tree_oid,
        candidate_receipt_digest=digest_value(
            {"kind": "candidate_receipt", "ticket_key": ticket_key, "candidate_sha": actual_candidate_sha}
        ),
        diff_schema_version="CandidateDiffRecordV1",
        diff_record_digest="2" * 64,
        authority_subtree_digest="3" * 64,
        policy_witness_digest="4" * 64,
        review_subject_digest="5" * 64,
        assurance=assurance,
        assurance_requirement_digest=digest_value(
            {"assurance": assurance, "ticket_key": ticket_key}
        ),
        check_environment_digest="6" * 64,
        delivery_identity_digest=delivery_identity_digest,
        interaction_keys=actual_interaction_keys,
        protected_surfaces=tuple(sorted(protected_surfaces)),
        gitlink_change=gitlink_change,
        evidence_digests=evidence_digests,
        review_finding_ledger_digest="7" * 64,
    )

def make_batch_request(
    *,
    accepted_candidates: tuple[AcceptedCandidateReceipt, ...],
    stable_action_id: str = "delivery-action:1",
    target_head_sha: str = "b" * 40,
) -> BatchDeliveryRequest:
    return BatchDeliveryRequest(
        stable_action_id=stable_action_id,
        repository="owner/repo",
        campaign_key="campaign:test",
        plan_revision_digest="1" * 64,
        target=BatchTarget(
            repository="owner/repo",
            target_branch="main",
            target_head_sha=target_head_sha,
            target_tree_oid="8" * 40,
            target_facts_digest="9" * 64,
        ),
        accepted_candidates=accepted_candidates,
        local_suite=LocalSuiteDefinition(
            suite_id="local",
            definition_digest="a" * 64,
            command=("py", "-3.13", "-c", "print('batch-local-suite')"),
        ),
        hosted_suites=(
            HostedSuiteDefinition(
                suite_id="hosted",
                hosted_name="GWO Canary CI",
                definition_digest="b" * 64,
            ),
        ),
        writer_generation="writer:test",
        activation_id="activation:test",
    )
```

The support module is extended only at the task that creates each imported
production value: Task 2 adds tree helpers, Task 3 adds journal/receipt
helpers, Task 5 adds real Git/composition helpers, and Task 6 adds hosted
drivers plus the final assembly. No Task 1 import names a later-created
module.

`make_integrator` is the final concrete assembly helper (added in Task 6). It creates
`Path(repository) / "v8.sqlite3"`, selects `GitCliBatchDriver` only when the
path already contains the real repository built above, otherwise selects the
deterministic recording Git double used by hosted/recovery tests, and wires
`crash_hook_for(crash_after)` into the driver boundary. Its keyword defaults
are `hosted_outcomes=()`, `publication_batch_sha=None`,
`hosted_identity_mismatch=None`, `target_merge_method="merge"`,
`target_contains_batch=True`, and `delivery_failure=None`. The recording
driver set exposes concrete lists named
`batch_shas`, `published_shas`, `hosted_read_shas`, `pull_request_heads`,
`integrated_shas`, `retry_shas`, `created_batch_member_sets`,
`preserved_evidence_digests`, and `resume_directives`, plus integer counters
`compose_calls`, `publish_calls`, `hosted_read_calls`, and `integrate_calls`;
its `formation_calls` property returns the BatchIntegrator formation counter,
while `composition_calls` returns the recording Git driver's composition
counter. Task 5 implements the production Git adapter against the same
constructor contract, while the recording doubles above are the complete
test-only implementations; no SHA-only repository substitute is permitted for
the two real-repository helpers above.

- [ ] **Step 2: Run RED and prove the boundary is absent.**

```powershell
py -3.13 -m pytest tests/test_v8_batch_integrator.py::test_accepted_candidate_receipt_digest_binds_every_delivery_fact -q
```

Expected: FAIL during collection because the V3 delivery boundary and
`BatchDeliveryRequest` are not yet defined; `AcceptedCandidateReceipt` is
already supplied by the merged #114/#115 `candidate_gate.py` prerequisite.

- [ ] **Step 3: Implement the minimal frozen values.**

Implement exact SHA-1/SHA-256 object-ID validation, lowercase SHA-256 digest validation, tuple ordering/uniqueness validation, immutable defensive copies, and canonical digest calculation through `_canonical.digest_value`. The constructor must reject a `result_digest` key if a caller constructs a canonical mapping; the accepted-Candidate boundary contains only Candidate/Evidence facts. `BatchDeliveryRequest.request_digest` must be `digest_value` over the stable action, repository, Campaign, Plan Revision, target facts, all canonical member receipts, local suite, hosted suites, writer generation, and Activation ID.

Use the concrete `BatchIntegratorError`, `DeliveryIdentityMismatch`, and
`DeliveryAttributionAmbiguous` bodies in the exact boundary block above; Task 1
implements those definitions once and later tasks import them rather than
shadowing them.

Paste this implementation into `batch_integrator.py`; it imports CandidateGate's
types and does not define an interaction enum or key mirror:

```python
import re
from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal

from ._canonical import digest_value
from .candidate_gate import AcceptedCandidateReceipt, InteractionClassification, InteractionKey


class BatchIntegratorError(RuntimeError):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


class DeliveryIdentityMismatch(BatchIntegratorError):
    def __init__(self, detail: str) -> None:
        super().__init__("DELIVERY_IDENTITY_MISMATCH", detail)


class DeliveryAttributionAmbiguous(BatchIntegratorError):
    def __init__(self, detail: str) -> None:
        super().__init__("DELIVERY_ATTRIBUTION_AMBIGUOUS", detail)


def _require_object_id(name: str, value: str) -> str:
    if re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", value) is None:
        raise BatchIntegratorError("BATCH_OBJECT_ID_INVALID", f"{name} must be a lowercase Git object ID")
    return value


def _require_digest(name: str, value: str) -> str:
    if re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise BatchIntegratorError("BATCH_DIGEST_INVALID", f"{name} must be a lowercase SHA-256 digest")
    return value


def _require_sorted_unique(name: str, values: tuple[str, ...]) -> tuple[str, ...]:
    frozen = tuple(values)
    if frozen != tuple(sorted(set(frozen))):
        raise BatchIntegratorError("BATCH_CANONICAL_ORDER_INVALID", f"{name} must be sorted and unique")
    return frozen


def _accepted_candidate_body(candidate: AcceptedCandidateReceipt) -> dict[str, object]:
    body = dict(candidate.canonical())
    if "result_digest" in body:
        raise BatchIntegratorError("BATCH_RESULT_PREMATURE", "accepted Candidate cannot contain result_digest")
    if candidate.accepted_sequence < 0:
        raise BatchIntegratorError("BATCH_SEQUENCE_INVALID", "accepted_sequence must be non-negative")
    for name in ("base_sha", "base_tree_oid", "candidate_sha", "candidate_tree_oid"):
        _require_object_id(name, getattr(candidate, name))
    for name in (
        "candidate_receipt_digest",
        "diff_record_digest",
        "authority_subtree_digest",
        "policy_witness_digest",
        "review_subject_digest",
        "assurance_requirement_digest",
        "check_environment_digest",
        "delivery_identity_digest",
        "review_finding_ledger_digest",
    ):
        _require_digest(name, getattr(candidate, name))
    evidence = _require_sorted_unique("evidence_digests", tuple(candidate.evidence_digests))
    for digest in evidence:
        _require_digest("evidence_digests item", digest)
    _require_sorted_unique("protected_surfaces", tuple(candidate.protected_surfaces))
    interaction_order = tuple(
        (key.namespace, key.value, key.classification.value)
        for key in candidate.interaction_keys
    )
    if interaction_order != tuple(sorted(set(interaction_order))):
        raise BatchIntegratorError(
            "BATCH_INTERACTION_ORDER_INVALID",
            "interaction_keys must be sorted and unique",
        )
    _require_digest("accepted_candidate_digest", candidate.digest)
    return body


@dataclass(frozen=True)
class BatchTarget:
    repository: str
    target_branch: str
    target_head_sha: str
    target_tree_oid: str
    target_facts_digest: str

    def __post_init__(self) -> None:
        _require_object_id("target_head_sha", self.target_head_sha)
        _require_object_id("target_tree_oid", self.target_tree_oid)
        _require_digest("target_facts_digest", self.target_facts_digest)

    def canonical(self) -> dict[str, str]:
        return {
            "repository": self.repository,
            "target_branch": self.target_branch,
            "target_head_sha": self.target_head_sha,
            "target_tree_oid": self.target_tree_oid,
            "target_facts_digest": self.target_facts_digest,
        }


@dataclass(frozen=True)
class LocalSuiteDefinition:
    suite_id: str
    definition_digest: str
    command: tuple[str, ...]

    def __post_init__(self) -> None:
        command = tuple(self.command)
        if not self.suite_id or not command or any(not item for item in command):
            raise BatchIntegratorError("BATCH_LOCAL_SUITE_INVALID", "local suite ID and command are required")
        _require_digest("local definition_digest", self.definition_digest)
        object.__setattr__(self, "command", command)

    def canonical(self) -> dict[str, object]:
        return {
            "suite_id": self.suite_id,
            "definition_digest": self.definition_digest,
            "command": list(self.command),
        }


@dataclass(frozen=True)
class HostedSuiteDefinition:
    suite_id: str
    hosted_name: str
    definition_digest: str

    def __post_init__(self) -> None:
        if not self.suite_id or not self.hosted_name:
            raise BatchIntegratorError("BATCH_HOSTED_SUITE_INVALID", "hosted suite identity is required")
        _require_digest("hosted definition_digest", self.definition_digest)

    def canonical(self) -> dict[str, str]:
        return {
            "suite_id": self.suite_id,
            "hosted_name": self.hosted_name,
            "definition_digest": self.definition_digest,
        }


@dataclass(frozen=True)
class BatchIntegratorConfiguration:
    host_member_limit: int = 4
    repository_member_limits: dict[str, int] | None = None
    infrastructure_retry_limit: int = 2

    def __post_init__(self) -> None:
        if not 1 <= self.host_member_limit <= 4:
            raise BatchIntegratorError("BATCH_MEMBER_LIMIT_INVALID", "member limit must be between one and four")
        if self.infrastructure_retry_limit != 2:
            raise BatchIntegratorError("BATCH_RETRY_POLICY_INVALID", "infrastructure retry limit is fixed at two")
        copied = dict(sorted((self.repository_member_limits or {}).items()))
        if any(not 1 <= limit <= 4 for limit in copied.values()):
            raise BatchIntegratorError("BATCH_MEMBER_LIMIT_INVALID", "repository member limit must be between one and four")
        object.__setattr__(self, "repository_member_limits", MappingProxyType(copied))

    def member_limit_for(self, repository: str) -> int:
        return self.repository_member_limits.get(repository, self.host_member_limit)


@dataclass(frozen=True)
class BatchDeliveryRequest:
    stable_action_id: str
    repository: str
    campaign_key: str
    plan_revision_digest: str
    target: BatchTarget
    accepted_candidates: tuple[AcceptedCandidateReceipt, ...]
    local_suite: LocalSuiteDefinition
    hosted_suites: tuple[HostedSuiteDefinition, ...]
    writer_generation: str
    activation_id: str

    def __post_init__(self) -> None:
        candidates = tuple(self.accepted_candidates)
        suites = tuple(self.hosted_suites)
        if not self.stable_action_id or not self.repository or not self.campaign_key:
            raise BatchIntegratorError("BATCH_REQUEST_IDENTITY_INVALID", "request identity is required")
        _require_digest("plan_revision_digest", self.plan_revision_digest)
        ordered = tuple(sorted(candidates, key=lambda item: (item.accepted_sequence, item.ticket_key, item.candidate_sha)))
        if candidates != ordered:
            raise BatchIntegratorError("BATCH_CANDIDATE_ORDER_INVALID", "accepted_candidates must use canonical queue order")
        sequences = tuple(item.accepted_sequence for item in candidates)
        if len(sequences) != len(set(sequences)):
            raise BatchIntegratorError("BATCH_SEQUENCE_DUPLICATE", "accepted_sequence must be unique")
        for candidate in candidates:
            _accepted_candidate_body(candidate)
            if (
                candidate.repository != self.repository
                or candidate.campaign_key != self.campaign_key
                or candidate.plan_revision_digest != self.plan_revision_digest
                or candidate.target_branch != self.target.target_branch
            ):
                raise BatchIntegratorError("BATCH_CANDIDATE_SCOPE_MISMATCH", "accepted Candidate is outside the request scope")
        suite_ids = tuple(suite.suite_id for suite in suites)
        if not suites or suite_ids != tuple(sorted(set(suite_ids))):
            raise BatchIntegratorError("BATCH_HOSTED_SUITE_ORDER_INVALID", "hosted_suites must be non-empty, sorted, and unique")
        object.__setattr__(self, "accepted_candidates", candidates)
        object.__setattr__(self, "hosted_suites", suites)

    def canonical(self) -> dict[str, object]:
        return {
            "kind": "batch-delivery-request.v1",
            "stable_action_id": self.stable_action_id,
            "repository": self.repository,
            "campaign_key": self.campaign_key,
            "plan_revision_digest": self.plan_revision_digest,
            "target": self.target.canonical(),
            "accepted_candidates": [
                _accepted_candidate_body(candidate) for candidate in self.accepted_candidates
            ],
            "local_suite": self.local_suite.canonical(),
            "hosted_suites": [suite.canonical() for suite in self.hosted_suites],
            "writer_generation": self.writer_generation,
            "activation_id": self.activation_id,
        }

    @property
    def request_digest(self) -> str:
        return digest_value(self.canonical())


@dataclass(frozen=True)
class BatchDeliveryAction:
    stable_action_id: str
    request_digest: str
    batch_id: str
    batch_sha: str
    member_ticket_keys: tuple[str, ...]

    def __post_init__(self) -> None:
        _require_digest("request_digest", self.request_digest)
        _require_digest("batch_id", self.batch_id)
        _require_object_id("batch_sha", self.batch_sha)
        object.__setattr__(
            self,
            "member_ticket_keys",
            _require_sorted_unique("member_ticket_keys", tuple(self.member_ticket_keys)),
        )
```

- [ ] **Step 4: Run GREEN and the focused validation gate.**

```powershell
py -3.13 -m pytest tests/test_v8_batch_integrator.py::test_accepted_candidate_receipt_digest_binds_every_delivery_fact tests/test_v8_batch_integrator.py::test_accepted_candidate_receipt_rejects_noncanonical_evidence_and_sequence tests/test_v8_batch_integrator.py::test_batch_request_digest_changes_when_member_set_or_target_changes -q
```

Expected: three tests PASS. Run `py -3.13 -m pytest tests/test_orchestrator_package.py -q` and confirm the existing package tests still pass after exporting only the new V3 boundary values.
Then run `py -3.13 scripts/sync_orchestrator.py` followed immediately by
`py -3.13 scripts/sync_orchestrator.py --check`; the check is not run against
an unsynchronized package.

- [ ] **Step 5: Commit the boundary only.**

```powershell
py -3.13 scripts/sync_orchestrator.py
py -3.13 scripts/sync_orchestrator.py --check
git add skills/orchestrator/scripts/gwo_v8/batch_integrator.py skills/orchestrator/scripts/gwo_v8/__init__.py skills/orchestrator/.skill-package.json tests/v8_batch_test_support.py tests/test_v8_batch_integrator.py
git commit -m "feat: define the V3 BatchIntegrator boundary"
```

### Task 2: Implement and prove `PatchIdentityV1`

**Files:**
- Create: `skills/orchestrator/scripts/gwo_v8/batch_patch_identity.py`
- Modify: `skills/orchestrator/scripts/gwo_v8/batch_integrator.py`
- Modify: `tests/v8_batch_test_support.py`
- Modify: `tests/test_v8_batch_integrator.py`

**Interfaces:**
- Consumes: exact base/Candidate/target Git-tree OIDs from `AcceptedCandidateReceipt` and raw tree entries from the private Git driver.
- Produces: `PatchIdentityEntry`, `PatchIdentityV1`, `patch_identity_v1`, and `CleanBaseAdvanceProof` for composition and Singleton retry.

- [ ] **Step 1: Write the failing tree-delta tests.**

Add these exact tests:

```python
def test_patch_identity_v1_is_independent_of_entry_input_order():
    entries = (
        make_patch_entry("b.txt", old_oid="1" * 40, new_oid="2" * 40),
        make_patch_entry("a.txt", old_oid="3" * 40, new_oid="4" * 40),
    )
    assert patch_identity_v1("sha1", entries) == patch_identity_v1("sha1", tuple(reversed(entries)))


def test_patch_identity_v1_changes_for_mode_binary_and_gitlink_identity():
    base = make_patch_entry("tool", old_mode="100644", new_mode="100755")
    binary = make_patch_entry("image.bin", old_oid="1" * 40, new_oid="2" * 40)
    gitlink = make_patch_entry("submodule", old_mode="160000", new_mode="160000", old_oid="3" * 40, new_oid="4" * 40, old_object_type="gitlink", new_object_type="gitlink")

    assert len({patch_identity_v1("sha1", (entry,)) for entry in (base, binary, gitlink)}) == 3


def test_clean_base_advance_rejects_recomputed_patch_identity_mismatch():
    member = make_accepted_candidate_receipt()
    with pytest.raises(BatchIntegratorError, match="PatchIdentityV1"):
        require_clean_base_advance(
            member=member,
            original_patch_digest="a" * 64,
            recomputed_patch_digest="b" * 64,
            ancestor=make_ancestor_readback(member.base_sha, "b" * 40),
            target_delta=make_target_delta(member.base_sha, "b" * 40),
        )


def test_clean_base_advance_requires_authoritative_original_base_ancestor():
    member = make_accepted_candidate_receipt()
    ancestor = make_ancestor_readback(member.base_sha, "b" * 40, is_ancestor=False)

    with pytest.raises(BatchIntegratorError, match="CLEAN_BASE_ANCESTOR_REQUIRED"):
        require_clean_base_advance(
            member=member,
            original_patch_digest=member.diff_record_digest,
            recomputed_patch_digest=member.diff_record_digest,
            ancestor=ancestor,
            target_delta=make_target_delta(member.base_sha, "b" * 40),
        )


def test_clean_base_advance_rejects_protected_target_delta_interaction_key():
    member = make_accepted_candidate_receipt()
    protected = make_interaction_key(
        "schema:root", classification=InteractionClassification.PROTECTED
    )

    with pytest.raises(BatchIntegratorError, match="TARGET_DELTA_PROTECTED_INTERACTION"):
        require_clean_base_advance(
            member=member,
            original_patch_digest=member.diff_record_digest,
            recomputed_patch_digest=member.diff_record_digest,
            ancestor=make_ancestor_readback(member.base_sha, "b" * 40),
            target_delta=make_target_delta(
                member.base_sha, "b" * 40, interaction_keys=(protected,)
            ),
        )
```

The support module is extended in this task, after `batch_patch_identity.py`
exists; this import is intentionally absent from Task 1:

```python
from gwo_v8.batch_patch_identity import PatchIdentityEntry
from gwo_v8.batch_integrator import AncestorReadback, TargetDeltaReadback

def make_batch_target(
    *,
    repository: str = "owner/repo",
    target_branch: str = "main",
    target_head_sha: str = "b" * 40,
    target_tree_oid: str = "8" * 40,
    target_facts_digest: str = "9" * 64,
) -> BatchTarget:
    return BatchTarget(
        repository=repository,
        target_branch=target_branch,
        target_head_sha=target_head_sha,
        target_tree_oid=target_tree_oid,
        target_facts_digest=target_facts_digest,
    )


def make_three_standard_receipts() -> tuple[AcceptedCandidateReceipt, ...]:
    return tuple(
        make_accepted_candidate_receipt(
            ticket_key=f"issue:{index}",
            accepted_sequence=index,
        )
        for index in range(1, 4)
    )


def make_patch_entry(
    path: str,
    *,
    old_path: str | None = None,
    new_path: str | None = None,
    change_kind: Literal["add", "delete", "modify", "type-change"] = "modify",
    old_mode: str = "100644",
    new_mode: str = "100644",
    old_oid: str = "a" * 40,
    new_oid: str = "a" * 40,
    old_object_type: Literal["blob", "gitlink"] = "blob",
    new_object_type: Literal["blob", "gitlink"] = "blob",
) -> PatchIdentityEntry:
    return PatchIdentityEntry(
        old_path=old_path if old_path is not None else path,
        new_path=new_path if new_path is not None else path,
        change_kind=change_kind,
        old_mode=old_mode,
        new_mode=new_mode,
        old_object_type=old_object_type,
        new_object_type=new_object_type,
        old_oid=old_oid,
        new_oid=new_oid,
    )


def make_ancestor_readback(
    ancestor_sha: str,
    descendant_sha: str,
    *,
    is_ancestor: bool = True,
) -> AncestorReadback:
    body = {
        "ancestor_sha": ancestor_sha,
        "descendant_sha": descendant_sha,
        "is_ancestor": is_ancestor,
    }
    return AncestorReadback(
        **body,
        readback_digest=digest_value({"kind": "ancestor-readback.v1", **body}),
    )


def make_target_delta(
    base_sha: str,
    target_head_sha: str,
    *,
    interaction_keys: tuple[InteractionKey, ...] = (),
) -> TargetDeltaReadback:
    protected = tuple(key for key in interaction_keys if key.requires_singleton)
    body = {
        "base_sha": base_sha,
        "target_head_sha": target_head_sha,
        "interaction_keys": [key.canonical() for key in interaction_keys],
        "protected_interaction_keys": [key.canonical() for key in protected],
    }
    return TargetDeltaReadback(
        base_sha=base_sha,
        target_head_sha=target_head_sha,
        interaction_keys=interaction_keys,
        protected_interaction_keys=protected,
        facts_digest=digest_value(body),
        readback_digest=digest_value({"kind": "target-delta-readback.v1", **body}),
    )

```


- [ ] **Step 2: Run RED.**

```powershell
py -3.13 -m pytest tests/test_v8_batch_integrator.py::test_patch_identity_v1_is_independent_of_entry_input_order -q
```

Expected: FAIL during collection because `batch_patch_identity.py` and `patch_identity_v1` do not exist.

- [ ] **Step 3: Implement the exact encoding.**

Use the architecture's algorithm without Git's heuristic `patch-id`: `LP(bytes)` is an unsigned 64-bit big-endian length followed by bytes; prepend `b"gwo.patch-identity.v1\\0"`, the ASCII object format, and the bytewise-sorted complete entry encodings; hash with SHA-256. `PatchIdentityEntry` must carry old/new complete repository-relative path bytes, change kind, six-digit modes, old/new object type, and raw old/new OID bytes. Reject unsafe paths, case-folding ambiguity, missing object IDs, rename/copy inference, and a `gitlink` entry in a non-Singleton member. The only valid change kinds are `add`, `delete`, `modify`, and `type-change`.

```python
import hashlib
from dataclasses import dataclass
from typing import Literal, TYPE_CHECKING

from ._canonical import digest_value
from .candidate_gate import AcceptedCandidateReceipt, InteractionKey

if TYPE_CHECKING:
    from .batch_integrator import AncestorReadback, TargetDeltaReadback


@dataclass(frozen=True)
class PatchIdentityEntry:
    old_path: str
    new_path: str
    change_kind: Literal["add", "delete", "modify", "type-change"]
    old_mode: str
    new_mode: str
    old_object_type: Literal["blob", "gitlink"]
    new_object_type: Literal["blob", "gitlink"]
    old_oid: str
    new_oid: str

    def __post_init__(self) -> None:
        if self.change_kind not in {"add", "delete", "modify", "type-change"}:
            raise ValueError("unsupported PatchIdentityV1 change kind")
        for path in (self.old_path, self.new_path):
            if not path or path.startswith("/") or "\\" in path or ".." in path.split("/"):
                raise ValueError("PatchIdentityV1 paths must be safe repository-relative paths")
        for mode in (self.old_mode, self.new_mode):
            if len(mode) != 6 or not mode.isdigit():
                raise ValueError("PatchIdentityV1 modes must be six-digit Git modes")
        for oid in (self.old_oid, self.new_oid):
            if len(oid) not in {40, 64} or any(character not in "0123456789abcdef" for character in oid):
                raise ValueError("PatchIdentityV1 object IDs must be lowercase hexadecimal")
        if self.old_object_type == "gitlink" or self.new_object_type == "gitlink":
            if self.change_kind not in {"add", "delete", "modify", "type-change"}:
                raise ValueError("gitlink identity has an unsupported change kind")

    def encoded(self) -> bytes:
        fields = (
            self.old_path.encode("utf-8"),
            self.new_path.encode("utf-8"),
            self.change_kind.encode("ascii"),
            self.old_mode.encode("ascii"),
            self.new_mode.encode("ascii"),
            self.old_object_type.encode("ascii"),
            self.new_object_type.encode("ascii"),
            bytes.fromhex(self.old_oid),
            bytes.fromhex(self.new_oid),
        )
        return b"".join(length_prefix(field) for field in fields)


@dataclass(frozen=True)
class PatchIdentityV1:
    repository_object_format: Literal["sha1", "sha256"]
    entries: tuple[PatchIdentityEntry, ...]

    @property
    def digest(self) -> str:
        return patch_identity_v1(self.repository_object_format, self.entries)


def length_prefix(value: bytes) -> bytes:
    return len(value).to_bytes(8, "big", signed=False) + value


def patch_identity_v1(
    repository_object_format: Literal["sha1", "sha256"],
    entries: tuple[PatchIdentityEntry, ...],
) -> str:
    encoded = sorted(entry.encoded() for entry in entries)
    payload = (
        b"gwo.patch-identity.v1\x00"
        + length_prefix(repository_object_format.encode("ascii"))
        + b"".join(length_prefix(entry) for entry in encoded)
    )
    return hashlib.sha256(payload).hexdigest()
```

`CleanBaseAdvanceProof` must contain original base/Candidate tree OIDs, original target head/tree OIDs, advanced member tree OID, original patch digest, recomputed patch digest, and a digest over all those facts. Use this concrete value and guard; there is no unimplemented helper declaration:

```python
@dataclass(frozen=True)
class CleanBaseAdvanceProof:
    base_sha: str
    base_tree_oid: str
    candidate_sha: str
    candidate_tree_oid: str
    target_head_sha: str
    target_tree_oid: str
    original_base_is_ancestor: bool
    ancestor_readback_digest: str
    target_delta_interaction_keys: tuple[InteractionKey, ...]
    target_delta_protected_interaction_keys: tuple[InteractionKey, ...]
    target_delta_facts_digest: str
    advanced_member_tree_oid: str
    original_patch_digest: str
    recomputed_patch_digest: str
    proof_digest: str


def require_clean_base_advance(
    *,
    member: AcceptedCandidateReceipt,
    original_patch_digest: str,
    recomputed_patch_digest: str,
    ancestor: AncestorReadback,
    target_delta: TargetDeltaReadback,
    target_tree_oid: str = "8" * 40,
    advanced_member_tree_oid: str | None = None,
) -> CleanBaseAdvanceProof:
    ancestor.validate()
    if ancestor.ancestor_sha != member.base_sha or ancestor.descendant_sha != target_delta.target_head_sha:
        raise BatchIntegratorError(
            "CLEAN_BASE_ANCESTOR_READBACK_MISMATCH",
            "ancestor readback does not name the member base and current target",
        )
    if not ancestor.is_ancestor:
        raise BatchIntegratorError(
            "CLEAN_BASE_ANCESTOR_REQUIRED",
            "original Candidate base is not an authoritative target ancestor",
        )
    target_delta.canonical()
    if target_delta.base_sha != member.base_sha:
        raise BatchIntegratorError(
            "TARGET_DELTA_BASE_MISMATCH",
            "target delta facts do not start at the Candidate base",
        )
    if target_delta.protected_interaction_keys:
        raise BatchIntegratorError(
            "TARGET_DELTA_PROTECTED_INTERACTION",
            "target delta shares a protected Interaction Key with the Candidate",
        )
    if original_patch_digest != recomputed_patch_digest:
        raise BatchIntegratorError(
            "CLEAN_BASE_PATCH_IDENTITY_MISMATCH",
            "PatchIdentityV1 changed across Clean Base Advance",
        )
    advanced_tree = advanced_member_tree_oid or member.candidate_tree_oid
    body = {
        "base_sha": member.base_sha,
        "base_tree_oid": member.base_tree_oid,
        "candidate_sha": member.candidate_sha,
        "candidate_tree_oid": member.candidate_tree_oid,
        "target_head_sha": target_delta.target_head_sha,
        "target_tree_oid": target_tree_oid,
        "original_base_is_ancestor": ancestor.is_ancestor,
        "ancestor_readback_digest": ancestor.readback_digest,
        "target_delta_interaction_keys": [
            key.canonical() for key in target_delta.interaction_keys
        ],
        "target_delta_protected_interaction_keys": [
            key.canonical() for key in target_delta.protected_interaction_keys
        ],
        "target_delta_facts_digest": target_delta.facts_digest,
        "advanced_member_tree_oid": advanced_tree,
        "original_patch_digest": original_patch_digest,
        "recomputed_patch_digest": recomputed_patch_digest,
    }
    return CleanBaseAdvanceProof(**body, proof_digest=digest_value(body))
```

- [ ] **Step 4: Run GREEN and refactor only while green.**

```powershell
py -3.13 -m pytest tests/test_v8_batch_integrator.py -k "patch_identity or clean_base" -q
```

Expected: all PatchIdentityV1 tests PASS. Refactor private encoding helpers only after this command remains green; do not substitute `git patch-id` or the CandidateGate diff digest.

- [ ] **Step 5: Commit the pure identity algorithm.**

```powershell
py -3.13 scripts/sync_orchestrator.py
py -3.13 scripts/sync_orchestrator.py --check
git add skills/orchestrator/scripts/gwo_v8/batch_patch_identity.py skills/orchestrator/scripts/gwo_v8/batch_integrator.py skills/orchestrator/.skill-package.json tests/v8_batch_test_support.py tests/test_v8_batch_integrator.py
git commit -m "feat: add exact PatchIdentityV1 proofs"
```

### Task 3: Add the rebuildable delivery journal and Integration-Lease CAS

**Files:**
- Create: `skills/orchestrator/scripts/gwo_v8/_batch_integrator_store.py`
- Modify: `skills/orchestrator/scripts/gwo_v8/batch_integrator.py`
- Modify: `tests/v8_batch_test_support.py`
- Modify: `tests/test_v8_batch_integrator.py`
- Create: `tests/test_v8_batch_recovery.py`

**Interfaces:**
- Consumes: `BatchDeliveryAction`, stable request digest, writer generation, and Activation ID.
- Produces: `BatchDeliveryJournal`, `BatchJournalRecord`, `HostedResultReceipt`, and `IntegrationLeaseReceipt` with compare-and-swap semantics.

- [ ] **Step 1: Write the failing stale-write and replay tests.**

At the top of the two test modules, use `from dataclasses import replace`; the
recovery test module additionally uses `import sqlite3` for the deep-module
corruption test in Task 8. Do not add a production test-corruption method.
The integrator test module also imports `digest_value` from
`gwo_v8._canonical` for the exact lease-digest assertion below.

Add these exact tests:

```python
def test_integration_lease_compare_and_swap_keeps_the_first_holder(tmp_path):
    journal = SqliteBatchDeliveryJournal(tmp_path / "v8.sqlite3")
    first = journal.acquire_integration_lease("owner/repo", "action:one", "gen:1", "activation:1")

    with pytest.raises(BatchIntegratorError, match="INTEGRATION_LEASE_UNAVAILABLE"):
        journal.acquire_integration_lease("owner/repo", "action:two", "gen:1", "activation:1")

    assert journal.read_integration_lease("owner/repo") == first


def test_same_holder_new_generation_cannot_replace_current_integration_lease(tmp_path):
    journal = SqliteBatchDeliveryJournal(tmp_path / "v8.sqlite3")
    first = journal.acquire_integration_lease("owner/repo", "action:one", "gen:1", "activation:1")

    with pytest.raises(BatchIntegratorError, match="INTEGRATION_LEASE_UNAVAILABLE"):
        journal.acquire_integration_lease("owner/repo", "action:one", "gen:2", "activation:2")

    assert journal.read_integration_lease("owner/repo") == first


def test_stale_lease_release_cannot_delete_reacquired_current_receipt(tmp_path):
    journal = SqliteBatchDeliveryJournal(tmp_path / "v8.sqlite3")
    first = journal.acquire_integration_lease("owner/repo", "action:one", "gen:1", "activation:1")
    journal.release_integration_lease("owner/repo", first)
    current = journal.acquire_integration_lease("owner/repo", "action:one", "gen:2", "activation:2")

    with pytest.raises(BatchIntegratorError, match="INTEGRATION_LEASE_OWNER_MISMATCH"):
        journal.release_integration_lease("owner/repo", first)

    assert journal.read_integration_lease("owner/repo") == current


def test_batch_journal_record_and_integration_lease_receipt_have_exact_bodies(tmp_path):
    journal = SqliteBatchDeliveryJournal(tmp_path / "v8.sqlite3")
    action = make_batch_action()
    record = journal.create_action(action, action.request_digest)
    lease = journal.acquire_integration_lease(
        "owner/repo", "action:one", "gen:1", "activation:1"
    )

    assert record.body() == {
        "stable_action_id": action.stable_action_id,
        "request_digest": action.request_digest,
        "batch_id": action.batch_id,
        "batch_sha": action.batch_sha,
        "phase": "prepared",
        "reason": "prepared",
        "retry_count": 0,
        "fallback_generation": 0,
        "state_json": "{}",
        "version": 0,
    }
    assert lease.body() == {
        "repository": "owner/repo",
        "holder": "action:one",
        "writer_generation": "gen:1",
        "activation_id": "activation:1",
    }
    assert lease.lease_digest == digest_value(
        {"kind": "integration-lease.v1", **lease.body()}
    )


def test_stale_batch_action_write_does_not_overwrite_newer_version(tmp_path):
    journal = SqliteBatchDeliveryJournal(tmp_path / "v8.sqlite3")
    action = make_batch_action()
    created = journal.create_action(action, action.request_digest)
    newer = journal.advance_action(created, phase="published", reason="publication read back")

    with pytest.raises(BatchIntegratorError, match="BATCH_ACTION_CAS_CONFLICT"):
        journal.compare_and_swap_action(
            action.stable_action_id,
            expected_version=created.version,
            expected_phase="prepared",
            next_record=replace(newer, phase="integrating", version=created.version + 1),
        )

    assert journal.read_action(action.stable_action_id).phase == "published"


def test_identical_terminal_hosted_receipt_replays_but_wrong_identity_fails(tmp_path):
    journal = SqliteBatchDeliveryJournal(tmp_path / "v8.sqlite3")
    receipt = make_hosted_result_receipt()
    assert journal.persist_hosted_result(receipt) == receipt
    assert journal.persist_hosted_result(receipt) == receipt

    with pytest.raises(DeliveryIdentityMismatch):
        journal.persist_hosted_result(replace(receipt, batch_sha="b" * 40))
```

Only after `_batch_integrator_store.py` is created does the support module add
these imports and helpers:

```python
from gwo_v8._batch_integrator_store import HostedResultReceipt, SqliteBatchDeliveryJournal
from gwo_v8.batch_integrator import BatchDeliveryAction

def make_batch_action(
    *,
    stable_action_id: str = "delivery-action:1",
    request_digest: str = "a" * 64,
    batch_id: str = "b" * 64,
    batch_sha: str = "c" * 40,
    member_ticket_keys: tuple[str, ...] = ("issue:1",),
) -> BatchDeliveryAction:
    return BatchDeliveryAction(
        stable_action_id=stable_action_id,
        request_digest=request_digest,
        batch_id=batch_id,
        batch_sha=batch_sha,
        member_ticket_keys=member_ticket_keys,
    )


def make_hosted_result_receipt(
    *,
    stable_action_id: str = "delivery-action:1",
    batch_sha: str = "c" * 40,
    suite_id: str = "hosted",
    provider_check_id: str = "check:1",
    outcome: Literal["passed", "code_failure", "infrastructure_failure"] = "passed",
    observation_digest: str = "e" * 64,
) -> HostedResultReceipt:
    body = {
        "stable_action_id": stable_action_id,
        "batch_sha": batch_sha,
        "suite_id": suite_id,
        "provider_check_id": provider_check_id,
        "outcome": outcome,
        "observation_digest": observation_digest,
        "source_ref": "checks:hosted",
    }
    return HostedResultReceipt(
        **body,
        receipt_digest=digest_value({"kind": "hosted_result_receipt.v1", **body}),
    )


```


- [ ] **Step 2: Run RED.**

```powershell
py -3.13 -m pytest tests/test_v8_batch_integrator.py::test_integration_lease_compare_and_swap_keeps_the_first_holder -q
```

Expected: FAIL during collection because `SqliteBatchDeliveryJournal` and the receipt values are not defined.

- [ ] **Step 3: Implement one SQLite schema and exact CAS.**

`SqliteBatchDeliveryJournal` must use the injected `store_path`, create parent directories, enable `sqlite3.Row`, and create these tables without holding a transaction across external I/O:

```sql
CREATE TABLE IF NOT EXISTS v8_batch_delivery_actions (
    stable_action_id TEXT PRIMARY KEY,
    request_digest TEXT NOT NULL,
    batch_id TEXT NOT NULL,
    batch_sha TEXT NOT NULL,
    phase TEXT NOT NULL,
    reason TEXT NOT NULL,
    retry_count INTEGER NOT NULL,
    fallback_generation INTEGER NOT NULL,
    state_json TEXT NOT NULL,
    version INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS v8_batch_hosted_receipts (
    stable_action_id TEXT NOT NULL,
    batch_sha TEXT NOT NULL,
    suite_id TEXT NOT NULL,
    provider_check_id TEXT NOT NULL,
    outcome TEXT NOT NULL,
    observation_digest TEXT NOT NULL,
    source_ref TEXT NOT NULL,
    receipt_digest TEXT NOT NULL,
    PRIMARY KEY (stable_action_id, batch_sha, suite_id, provider_check_id)
);
CREATE TABLE IF NOT EXISTS v8_batch_integration_leases (
    repository TEXT PRIMARY KEY,
    holder TEXT NOT NULL,
    writer_generation TEXT NOT NULL,
    activation_id TEXT NOT NULL,
    lease_digest TEXT NOT NULL
);
```

The store module defines these exact frozen values before it defines the
journal methods; Task 1 and Task 2 support code therefore cannot import them:

```python
import sqlite3
from dataclasses import dataclass, replace
from pathlib import Path
import re
from typing import Callable, Literal

from ._canonical import digest_value
from .batch_integrator import (
    BatchDeliveryAction,
    BatchIntegratorError,
    DeliveryIdentityMismatch,
)


@dataclass(frozen=True)
class BatchJournalRecord:
    stable_action_id: str
    request_digest: str
    batch_id: str
    batch_sha: str
    phase: Literal["prepared", "composed", "local_checked", "published", "hosted", "integrating", "complete", "wait", "decision", "blocked"]
    reason: str
    retry_count: int
    fallback_generation: int
    state_json: str
    version: int

    def body(self) -> dict[str, object]:
        return {
            "stable_action_id": self.stable_action_id,
            "request_digest": self.request_digest,
            "batch_id": self.batch_id,
            "batch_sha": self.batch_sha,
            "phase": self.phase,
            "reason": self.reason,
            "retry_count": self.retry_count,
            "fallback_generation": self.fallback_generation,
            "state_json": self.state_json,
            "version": self.version,
        }


@dataclass(frozen=True)
class IntegrationLeaseReceipt:
    repository: str
    holder: str
    writer_generation: str
    activation_id: str
    lease_digest: str

    def body(self) -> dict[str, str]:
        return {
            "repository": self.repository,
            "holder": self.holder,
            "writer_generation": self.writer_generation,
            "activation_id": self.activation_id,
        }

    @classmethod
    def create(
        cls,
        repository: str,
        holder: str,
        writer_generation: str,
        activation_id: str,
    ) -> "IntegrationLeaseReceipt":
        body = {
            "repository": repository,
            "holder": holder,
            "writer_generation": writer_generation,
            "activation_id": activation_id,
        }
        return cls(
            **body,
            lease_digest=digest_value({"kind": "integration-lease.v1", **body}),
        )


@dataclass(frozen=True)
class HostedResultReceipt:
    stable_action_id: str
    batch_sha: str
    suite_id: str
    provider_check_id: str
    outcome: Literal["passed", "code_failure", "infrastructure_failure"]
    observation_digest: str
    source_ref: str
    receipt_digest: str

    def body(self) -> dict[str, str]:
        return {
            "stable_action_id": self.stable_action_id,
            "batch_sha": self.batch_sha,
            "suite_id": self.suite_id,
            "provider_check_id": self.provider_check_id,
            "outcome": self.outcome,
            "observation_digest": self.observation_digest,
            "source_ref": self.source_ref,
        }

    @classmethod
    def create(
        cls,
        stable_action_id: str,
        batch_sha: str,
        suite_id: str,
        provider_check_id: str,
        outcome: Literal["passed", "code_failure", "infrastructure_failure"],
        observation_digest: str,
        source_ref: str,
    ) -> "HostedResultReceipt":
        body = {
            "stable_action_id": stable_action_id,
            "batch_sha": batch_sha,
            "suite_id": suite_id,
            "provider_check_id": provider_check_id,
            "outcome": outcome,
            "observation_digest": observation_digest,
            "source_ref": source_ref,
        }
        return cls(
            **body,
            receipt_digest=digest_value({"kind": "hosted_result_receipt.v1", **body}),
        )


class SqliteBatchDeliveryJournal:
    def __init__(
        self,
        store_path: str | Path,
        crash_hook: Callable[[str], None] | None = None,
    ) -> None:
        self.store_path = Path(store_path)
        self.store_path.parent.mkdir(parents=True, exist_ok=True)
        self.crash_hook = crash_hook or (lambda _boundary: None)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.store_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS v8_batch_delivery_actions (
                    stable_action_id TEXT PRIMARY KEY,
                    request_digest TEXT NOT NULL,
                    batch_id TEXT NOT NULL,
                    batch_sha TEXT NOT NULL,
                    phase TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    retry_count INTEGER NOT NULL,
                    fallback_generation INTEGER NOT NULL,
                    state_json TEXT NOT NULL,
                    version INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS v8_batch_hosted_receipts (
                    stable_action_id TEXT NOT NULL,
                    batch_sha TEXT NOT NULL,
                    suite_id TEXT NOT NULL,
                    provider_check_id TEXT NOT NULL,
                    outcome TEXT NOT NULL,
                    observation_digest TEXT NOT NULL,
                    source_ref TEXT NOT NULL,
                    receipt_digest TEXT NOT NULL,
                    PRIMARY KEY (stable_action_id, batch_sha, suite_id, provider_check_id)
                );
                CREATE TABLE IF NOT EXISTS v8_batch_integration_leases (
                    repository TEXT PRIMARY KEY,
                    holder TEXT NOT NULL,
                    writer_generation TEXT NOT NULL,
                    activation_id TEXT NOT NULL,
                    lease_digest TEXT NOT NULL
                );
                """
            )

    def read_action(self, stable_action_id: str) -> BatchJournalRecord | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM v8_batch_delivery_actions WHERE stable_action_id=?",
                (stable_action_id,),
            ).fetchone()
        return None if row is None else BatchJournalRecord(**dict(row))

    def _insert_action_if_absent(self, record: BatchJournalRecord) -> None:
        existing = self.read_action(record.stable_action_id)
        if existing is not None:
            if existing != record:
                raise DeliveryIdentityMismatch("batch action identity changed")
            return
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO v8_batch_delivery_actions
                (stable_action_id, request_digest, batch_id, batch_sha, phase,
                 reason, retry_count, fallback_generation, state_json, version)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                tuple(record.body().values()),
            )

    def compare_and_swap_action(
        self,
        stable_action_id: str,
        *,
        expected_version: int,
        expected_phase: str,
        next_record: BatchJournalRecord,
    ) -> BatchJournalRecord:
        with self._connect() as connection:
            changed = connection.execute(
                """
                UPDATE v8_batch_delivery_actions
                   SET state_json=?, phase=?, version=?, reason=?,
                       retry_count=?, fallback_generation=?
                 WHERE stable_action_id=? AND phase=? AND version=?
                """,
                (
                    next_record.state_json,
                    next_record.phase,
                    next_record.version,
                    next_record.reason,
                    next_record.retry_count,
                    next_record.fallback_generation,
                    stable_action_id,
                    expected_phase,
                    expected_version,
                ),
            ).rowcount
            if changed != 1:
                raise BatchIntegratorError(
                    "BATCH_ACTION_CAS_CONFLICT",
                    "stale action phase or version",
                )
        return self.read_action(stable_action_id)

    def read_integration_lease(self, repository: str) -> IntegrationLeaseReceipt | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM v8_batch_integration_leases WHERE repository=?",
                (repository,),
            ).fetchone()
        return None if row is None else IntegrationLeaseReceipt(**dict(row))

    def acquire_integration_lease(
        self,
        repository: str,
        holder: str,
        writer_generation: str,
        activation_id: str,
    ) -> IntegrationLeaseReceipt:
        requested = IntegrationLeaseReceipt.create(
            repository, holder, writer_generation, activation_id
        )
        self._validate_lease_digest(requested)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            current = connection.execute(
                "SELECT * FROM v8_batch_integration_leases WHERE repository=?",
                (repository,),
            ).fetchone()
            if current is not None:
                current_receipt = IntegrationLeaseReceipt(**dict(current))
                self._validate_lease_digest(current_receipt)
                if current_receipt != requested:
                    connection.rollback()
                    raise BatchIntegratorError(
                        "INTEGRATION_LEASE_UNAVAILABLE",
                        "repository lease identity is already active",
                    )
            else:
                connection.execute(
                    """
                    INSERT INTO v8_batch_integration_leases
                        (repository, holder, writer_generation, activation_id, lease_digest)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        requested.repository,
                        requested.holder,
                        requested.writer_generation,
                        requested.activation_id,
                        requested.lease_digest,
                    ),
                )
            connection.commit()
        return requested

    def release_integration_lease(self, repository: str, lease: IntegrationLeaseReceipt) -> None:
        if type(lease) is not IntegrationLeaseReceipt:
            raise BatchIntegratorError(
                "INTEGRATION_LEASE_OWNER_MISMATCH",
                "lease release requires the exact lease receipt",
            )
        self._validate_lease_digest(lease)
        if lease.repository != repository:
            raise BatchIntegratorError(
                "INTEGRATION_LEASE_OWNER_MISMATCH",
                "lease release repository does not match the receipt",
            )
        with self._connect() as connection:
            changed = connection.execute(
                """
                DELETE FROM v8_batch_integration_leases
                 WHERE repository=? AND holder=? AND writer_generation=?
                   AND activation_id=? AND lease_digest=?
                """,
                (
                    repository,
                    lease.holder,
                    lease.writer_generation,
                    lease.activation_id,
                    lease.lease_digest,
                ),
            ).rowcount
            if changed != 1:
                raise BatchIntegratorError(
                    "INTEGRATION_LEASE_OWNER_MISMATCH",
                    "lease release is not owned by the holder",
                )
            connection.commit()

    @staticmethod
    def _validate_hosted_receipt_digest(receipt: HostedResultReceipt) -> None:
        if (
            type(receipt.outcome) is not str
            or receipt.outcome
            not in {"passed", "code_failure", "infrastructure_failure"}
        ):
            raise DeliveryIdentityMismatch("hosted receipt outcome is invalid")
        for field_name in (
            "stable_action_id",
            "suite_id",
            "provider_check_id",
            "source_ref",
        ):
            value = getattr(receipt, field_name)
            if type(value) is not str or not value or "\x00" in value:
                raise DeliveryIdentityMismatch(
                    f"hosted receipt {field_name} identity is malformed"
                )
        if (
            type(receipt.batch_sha) is not str
            or re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", receipt.batch_sha)
            is None
        ):
            raise DeliveryIdentityMismatch("hosted receipt batch SHA is malformed")
        for field_name in ("observation_digest", "receipt_digest"):
            value = getattr(receipt, field_name)
            if type(value) is not str or re.fullmatch(r"[0-9a-f]{64}", value) is None:
                raise DeliveryIdentityMismatch(
                    f"hosted receipt {field_name} is malformed"
                )
        expected = digest_value({"kind": "hosted_result_receipt.v1", **receipt.body()})
        if expected != receipt.receipt_digest:
            raise DeliveryIdentityMismatch("hosted receipt digest mismatch")
```

The journal's action constructors use this exact shape, so Task 7's
readback-first loop and the Task 3 stale-write test share one API:

```python
def create_action(
    self,
    action: BatchDeliveryAction,
    request_digest: str,
    *,
    phase: str = "prepared",
    reason: str = "prepared",
    retry_count: int = 0,
    fallback_generation: int = 0,
    state_json: str = "{}",
) -> BatchJournalRecord:
    record = BatchJournalRecord(
        stable_action_id=action.stable_action_id,
        request_digest=request_digest,
        batch_id=action.batch_id,
        batch_sha=action.batch_sha,
        phase=phase,
        reason=reason,
        retry_count=retry_count,
        fallback_generation=fallback_generation,
        state_json=state_json,
        version=0,
    )
    self._insert_action_if_absent(record)
    return self.read_action(action.stable_action_id)


def advance_action(
    self, record: BatchJournalRecord, *, phase: str, reason: str
) -> BatchJournalRecord:
    return self.compare_and_swap_action(
        record.stable_action_id,
        expected_version=record.version,
        expected_phase=record.phase,
        next_record=replace(
            record,
            phase=phase,
            reason=reason,
            version=record.version + 1,
        ),
    )
```

`compare_and_swap_action` must execute an `UPDATE v8_batch_delivery_actions`
statement that sets `state_json`, `phase`, and `version` and has the exact
guard `WHERE stable_action_id=? AND phase=? AND version=?`; it must require
exactly one changed row and raise `BATCH_ACTION_CAS_CONFLICT` on zero rows.
Lease acquisition must use one `BEGIN IMMEDIATE` transaction with an
`INSERT INTO v8_batch_integration_leases` with an
`ON CONFLICT(repository) DO UPDATE` clause
whose update predicate is `holder=?`, and only exact replay may use that
predicate; a different holder raises `INTEGRATION_LEASE_UNAVAILABLE` without
changing the stored row. Release must delete only
`WHERE repository=? AND holder=?`, otherwise raise
`INTEGRATION_LEASE_OWNER_MISMATCH`. Hosted receipt persistence must allow exact
replay and reject any changed stable action, Batch SHA, suite, provider check,
outcome, or observation digest as `DeliveryIdentityMismatch`.

`SqliteBatchDeliveryJournal.__init__(store_path, crash_hook=None)` stores the
hook and calls it only in this concrete persistence boundary:

```python
def persist_hosted_result(self, receipt: HostedResultReceipt) -> HostedResultReceipt:
    self._validate_hosted_receipt_digest(receipt)
    existing = self.read_hosted_result(
        receipt.stable_action_id,
        receipt.batch_sha,
        receipt.suite_id,
        receipt.provider_check_id,
    )
    if existing is not None:
        if existing != receipt:
            raise DeliveryIdentityMismatch("hosted receipt identity changed")
        return existing
    with self._connect() as connection:
        connection.execute(
            "INSERT INTO v8_batch_hosted_receipts VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                receipt.stable_action_id,
                receipt.batch_sha,
                receipt.suite_id,
                receipt.provider_check_id,
                receipt.outcome,
                receipt.observation_digest,
                receipt.source_ref,
                receipt.receipt_digest,
            ),
        )
        connection.commit()
    self.crash_hook("hosted_receipt_persisted")
    return receipt


def read_hosted_result(
    self,
    stable_action_id: str,
    batch_sha: str,
    suite_id: str,
    provider_check_id: str,
) -> HostedResultReceipt | None:
    with self._connect() as connection:
        row = connection.execute(
            "SELECT * FROM v8_batch_hosted_receipts WHERE stable_action_id=? AND batch_sha=? AND suite_id=? AND provider_check_id=?",
            (stable_action_id, batch_sha, suite_id, provider_check_id),
        ).fetchone()
    if row is None:
        return None
    receipt = HostedResultReceipt(**dict(row))
    self._validate_hosted_receipt_digest(receipt)
    return receipt
```

The hook is therefore raised *after* the receipt row is committed and *before*
BatchIntegrator is allowed to call `integrate_serially`. A restart reads the
row, validates its key and digest, skips `read_hosted_result` at the provider,
and resumes at the serialized target-integration step.

- [ ] **Step 4: Run GREEN and SQLite restart tests.**

```powershell
py -3.13 -m pytest tests/test_v8_batch_integrator.py -k "lease or stale_batch_action or terminal_hosted_receipt" tests/test_v8_batch_recovery.py -k "journal" -q
```

Expected: all selected tests PASS, including a new journal instance reading the same action, lease, and hosted receipt bytes after construction.

- [ ] **Step 5: Commit the persistence boundary.**

```powershell
py -3.13 scripts/sync_orchestrator.py
py -3.13 scripts/sync_orchestrator.py --check
git add skills/orchestrator/scripts/gwo_v8/_batch_integrator_store.py skills/orchestrator/scripts/gwo_v8/batch_integrator.py skills/orchestrator/.skill-package.json tests/v8_batch_test_support.py tests/test_v8_batch_integrator.py tests/test_v8_batch_recovery.py
git commit -m "feat: add BatchIntegrator journal and lease CAS"
```

### Task 4: Implement deterministic Batch formation and compatibility

**Files:**
- Modify: `skills/orchestrator/scripts/gwo_v8/batch_integrator.py`
- Modify: `tests/v8_batch_test_support.py`
- Modify: `tests/test_v8_batch_integrator.py`

**Interfaces:**
- Consumes: valid `AcceptedCandidateReceipt` values, one authoritative `BatchTarget`, and `BatchIntegratorConfiguration`.
- Produces: `CompatibilityDecision`, `form_batch_members`, deterministic `batch_id`, and the immutable one-to-four member set used by composition.

- [ ] **Step 1: Write the failing formation tests.**

Add these exact tests:

```python
def test_forms_oldest_pairwise_compatible_candidates_up_to_four_without_waiting():
    queue = tuple(make_accepted_candidate_receipt(ticket_key=f"issue:{n}", accepted_sequence=n) for n in range(1, 6))
    # The ordinary conflict is the exact same ordinary key as issue:1. Other
    # ordinary keys are independent and must remain pairwise compatible.
    queue = (queue[0], replace(queue[1], interaction_keys=queue[0].interaction_keys), queue[2], queue[3], queue[4])

    selected = form_batch_members(queue, make_batch_target(), member_limit=4)

    assert [item.ticket_key for item in selected] == ["issue:1", "issue:3", "issue:4", "issue:5"]


def test_formation_is_same_campaign_and_strict_or_gitlink_is_singleton():
    seed = make_accepted_candidate_receipt(ticket_key="issue:1", campaign_key="campaign:a")
    other_campaign = make_accepted_candidate_receipt(ticket_key="issue:2", campaign_key="campaign:b", accepted_sequence=2)
    strict = make_accepted_candidate_receipt(ticket_key="issue:3", assurance="strict", accepted_sequence=3)
    gitlink = make_accepted_candidate_receipt(ticket_key="issue:4", gitlink_change=True, accepted_sequence=4)

    assert form_batch_members((seed, other_campaign), make_batch_target(), member_limit=4) == (seed,)
    assert form_batch_members((seed, strict), make_batch_target(), member_limit=4) == (seed,)
    assert form_batch_members((strict,), make_batch_target(), member_limit=4) == (strict,)
    assert form_batch_members((seed, gitlink), make_batch_target(), member_limit=4) == (seed,)
    assert form_batch_members((gitlink,), make_batch_target(), member_limit=4) == (gitlink,)


def test_policy_classified_interaction_key_forces_singleton():
    protected = make_accepted_candidate_receipt(
        interaction_keys=(make_interaction_key("schema:root", classification=InteractionClassification.PROTECTED),)
    )
    ordinary = make_accepted_candidate_receipt(ticket_key="issue:2", accepted_sequence=2)

    assert form_batch_members((protected, ordinary), make_batch_target(), member_limit=4) == (protected,)


def test_member_limit_rejects_zero_or_more_than_four_and_accepts_repository_override():
    with pytest.raises(BatchIntegratorError, match="member limit"):
        BatchIntegratorConfiguration(host_member_limit=0)
    with pytest.raises(BatchIntegratorError, match="member limit"):
        BatchIntegratorConfiguration(host_member_limit=5)
    configuration = BatchIntegratorConfiguration(host_member_limit=4, repository_member_limits={"owner/repo": 2})
    assert configuration.member_limit_for("owner/repo") == 2
```

- [ ] **Step 2: Run RED.**

```powershell
py -3.13 -m pytest tests/test_v8_batch_integrator.py::test_forms_oldest_pairwise_compatible_candidates_up_to_four_without_waiting -q
```

Expected: FAIL because `form_batch_members` and `make_batch_target` have no implementation.

- [ ] **Step 3: Implement the pure selection algorithm.**

Sort by `(accepted_sequence, ticket_key, candidate_sha)`; reject duplicate `accepted_sequence` values within the same Campaign. Select the first eligible Candidate as the seed, scan later receipts in that order, and add a receipt only when it is pairwise compatible with every frozen member. Continue scanning after an incompatible receipt, and stop immediately at the configured limit or when the scan ends. Ignore other-Campaign receipts rather than admitting them. Compatibility must compare repository, Campaign, Plan Revision, target branch, base/target identity or a valid Clean Base Advance, Policy Witness/authority facts, delivery identity, Assurance Requirement, check environment, protected surfaces, and every pairwise Interaction Key. A strict, gitlink, protected, high-coupling, or non-decomposable receipt returns `SINGLETON_REQUIRED` and cannot be paired. CandidateGate owns the key classification and key shape; BatchIntegrator only imports those values. For two ordinary keys, an exact `(namespace, value)` match is an interaction and is incompatible; different ordinary keys are independent. This is why the test's issue:2 exact-key collision is skipped while issue:3, issue:4, and issue:5 are selected.

Use these exact decision values:

```python
class CompatibilityDecision(StrEnum):
    COMPATIBLE = "compatible"
    SINGLETON_REQUIRED = "singleton_required"
    INCOMPATIBLE = "incompatible"
    CLEAN_BASE_ADVANCE = "clean_base_advance"


def form_batch_members(
    candidates: tuple[AcceptedCandidateReceipt, ...],
    target: BatchTarget,
    *,
    member_limit: int,
) -> tuple[AcceptedCandidateReceipt, ...]:
    if not 1 <= member_limit <= 4:
        raise BatchIntegratorError("BATCH_MEMBER_LIMIT_INVALID", "member limit must be between one and four")
    ordered = tuple(sorted(candidates, key=lambda item: (item.accepted_sequence, item.ticket_key, item.candidate_sha)))
    sequences = [item.accepted_sequence for item in ordered]
    if len(sequences) != len(set(sequences)):
        raise BatchIntegratorError("BATCH_SEQUENCE_DUPLICATE", "accepted_sequence must be unique")
    if not ordered:
        return ()
    seed = ordered[0]
    if seed.repository != target.repository or seed.target_branch != target.target_branch:
        return ()
    selected: list[AcceptedCandidateReceipt] = [seed]
    if seed.assurance == "strict" or seed.gitlink_change or any(
        key.requires_singleton for key in seed.interaction_keys
    ):
        return (seed,)
    for candidate in ordered[1:]:
        if len(selected) == member_limit:
            break
        if candidate.repository != target.repository or candidate.target_branch != target.target_branch:
            continue
        if candidate.campaign_key != seed.campaign_key or candidate.plan_revision_digest != seed.plan_revision_digest:
            continue
        decision = _pairwise_compatibility(candidate, selected, target)
        if decision in (CompatibilityDecision.COMPATIBLE, CompatibilityDecision.CLEAN_BASE_ADVANCE):
            selected.append(candidate)
    return tuple(selected)


def _pairwise_compatibility(
    candidate: AcceptedCandidateReceipt,
    selected: list[AcceptedCandidateReceipt],
    target: BatchTarget,
) -> CompatibilityDecision:
    if candidate.assurance == "strict" or candidate.gitlink_change:
        return CompatibilityDecision.SINGLETON_REQUIRED
    if any(key.requires_singleton for key in candidate.interaction_keys):
        return CompatibilityDecision.SINGLETON_REQUIRED
    for member in selected:
        if (
            candidate.authority_subtree_digest != member.authority_subtree_digest
            or candidate.policy_witness_digest != member.policy_witness_digest
            or candidate.delivery_identity_digest != member.delivery_identity_digest
            or candidate.assurance_requirement_digest != member.assurance_requirement_digest
            or candidate.check_environment_digest != member.check_environment_digest
            or candidate.protected_surfaces != member.protected_surfaces
        ):
            return CompatibilityDecision.INCOMPATIBLE
        if candidate.base_sha != member.base_sha or candidate.base_tree_oid != member.base_tree_oid:
            return CompatibilityDecision.CLEAN_BASE_ADVANCE
        for left in candidate.interaction_keys:
            for right in member.interaction_keys:
                if (
                    left.classification == InteractionClassification.ORDINARY
                    and right.classification == InteractionClassification.ORDINARY
                    and left.namespace == right.namespace
                    and left.value == right.value
                ):
                    return CompatibilityDecision.INCOMPATIBLE
    if candidate.base_sha != target.target_head_sha:
        return CompatibilityDecision.CLEAN_BASE_ADVANCE
    return CompatibilityDecision.COMPATIBLE
```

`batch_id` is `digest_value` over the Campaign, Plan Revision, target branch/facts, sorted member receipt digests, local/hosted suite definition digests, and the Batch protocol version. It is not the Batch SHA. The same request and read-back target must produce the same Batch ID.

- [ ] **Step 4: Run GREEN and test the no-wait rule.**

```powershell
py -3.13 -m pytest tests/test_v8_batch_integrator.py -k "forms_ or member_limit" -q
```

Expected: all selected tests PASS. Add a `BlockingCandidateReader` test double in `tests/v8_batch_test_support.py` and prove `form_batch_members` never calls a Runtime, CandidateGate, timer, or callback source; the pure function returns as soon as its finite tuple scan ends.

- [ ] **Step 5: Commit formation.**

```powershell
py -3.13 scripts/sync_orchestrator.py
py -3.13 scripts/sync_orchestrator.py --check
git add skills/orchestrator/scripts/gwo_v8/batch_integrator.py skills/orchestrator/.skill-package.json tests/v8_batch_test_support.py tests/test_v8_batch_integrator.py
git commit -m "feat: form deterministic compatible Integration Batches"
```

### Task 5: Compose immutable Batch SHAs and exact local verification

**Files:**
- Create: `skills/orchestrator/scripts/gwo_v8/_batch_integrator_drivers.py`
- Modify: `skills/orchestrator/scripts/gwo_v8/batch_integrator.py`
- Modify: `skills/orchestrator/scripts/gwo_v8/batch_patch_identity.py`
- Modify: `tests/v8_batch_test_support.py`
- Modify: `tests/test_v8_batch_integrator.py`

**Interfaces:**
- Consumes: a frozen member tuple and authoritative target readback from Task 4.
- Produces: private Git composition, the content-derived ref named by `f"refs/gwo-v8/integration-batches/{batch_id}"`, Clean Base Advance proofs, `LocalCheckReceipt`, and an immutable `batch_sha` in `BatchDeliveryAction`.

- [ ] **Step 1: Write the failing composition and crash-boundary tests.**

Add these exact tests:

```python
def test_composes_three_members_without_moving_target_and_reads_exact_tree(tmp_path):
    repository, target_sha, candidates = make_disjoint_git_candidates(tmp_path, count=3)
    integrator, drivers = make_composition_integrator(repository)
    request = make_batch_request(
        accepted_candidates=tuple(candidates),
        target_head_sha=target_sha,
    )

    action = integrator.prepare(request)

    assert drivers.git.read_target(request.target).target_head_sha == target_sha
    assert drivers.git.tree_contains(action.batch_sha, "module-1.py")
    assert drivers.git.tree_contains(action.batch_sha, "module-2.py")
    assert drivers.git.tree_contains(action.batch_sha, "module-3.py")
    assert drivers.git.read_ref(f"refs/gwo-v8/integration-batches/{action.batch_id}") == action.batch_sha


def test_existing_batch_ref_is_reused_without_a_second_merge(tmp_path):
    repository, target_sha, candidates = make_disjoint_git_candidates(tmp_path, count=2)
    first, drivers = make_composition_integrator(repository)
    request = make_batch_request(accepted_candidates=tuple(candidates), target_head_sha=target_sha)
    first_action = first.prepare(request)
    second, restarted_drivers = make_composition_integrator(repository)

    second_action = second.prepare(request)

    assert second_action == first_action
    assert restarted_drivers.git.compose_calls == 0


def test_lost_batch_ref_recomposition_reuses_the_same_deterministic_commit_sha(tmp_path):
    repository, target_sha, candidates = make_disjoint_git_candidates(tmp_path, count=2)
    first, _drivers = make_composition_integrator(repository)
    request = make_batch_request(accepted_candidates=tuple(candidates), target_head_sha=target_sha)
    first_action = first.prepare(request)

    drop_batch_ref(repository, first_action.batch_id)
    restarted, restarted_drivers = make_composition_integrator(repository)
    second_action = restarted.prepare(request)

    assert second_action.batch_sha == first_action.batch_sha
    assert restarted_drivers.git.compose_calls == 1


def test_clean_base_advance_requires_each_member_patch_identity_before_multi_member_compose(tmp_path):
    repository, advanced_target_sha, candidates = make_advanced_target_candidates(tmp_path, count=2)
    integrator, drivers = make_composition_integrator(repository)
    request = make_batch_request(
        accepted_candidates=tuple(candidates),
        target_head_sha=advanced_target_sha,
    )

    action = integrator.prepare(request)

    assert len(drivers.git.clean_base_advance_calls) == 2
    assert action.batch_sha != advanced_target_sha


def test_clean_base_advance_patch_identity_mismatch_fails_before_multi_member_compose(tmp_path):
    repository, target_sha, candidates = make_advanced_target_candidates(tmp_path, count=2)
    integrator, drivers = make_composition_integrator(repository)
    drivers.git.recomputed_patch_digest = "f" * 64

    with pytest.raises(BatchIntegratorError, match="CLEAN_BASE_PATCH_IDENTITY_MISMATCH"):
        integrator.prepare(make_batch_request(accepted_candidates=tuple(candidates), target_head_sha=target_sha))

    assert drivers.git.compose_calls == 0


def test_clean_base_advance_non_ancestor_fails_before_multi_member_compose(tmp_path):
    repository, target_sha, candidates = make_advanced_target_candidates(tmp_path, count=2)
    integrator, drivers = make_composition_integrator(
        repository, ancestor_is_ancestor=False
    )

    with pytest.raises(BatchIntegratorError, match="CLEAN_BASE_ANCESTOR_REQUIRED"):
        integrator.prepare(
            make_batch_request(
                accepted_candidates=tuple(candidates),
                target_head_sha=target_sha,
            )
        )

    assert drivers.git.compose_calls == 0


def test_clean_base_advance_protected_target_delta_fails_before_multi_member_compose(tmp_path):
    repository, target_sha, candidates = make_advanced_target_candidates(tmp_path, count=2)
    protected = make_interaction_key(
        "schema:root", classification=InteractionClassification.PROTECTED
    )
    integrator, drivers = make_composition_integrator(
        repository, target_delta_interaction_keys=(protected,)
    )

    with pytest.raises(BatchIntegratorError, match="TARGET_DELTA_PROTECTED_INTERACTION"):
        integrator.prepare(
            make_batch_request(
                accepted_candidates=tuple(candidates),
                target_head_sha=target_sha,
            )
        )

    assert drivers.git.compose_calls == 0


def test_crash_after_batch_ref_publication_is_recovered_by_exact_ref_readback(tmp_path):
    repository, target_sha, candidates = make_disjoint_git_candidates(tmp_path, count=2)
    first, drivers = make_composition_integrator(repository, crash_after="batch_ref_publication")
    request = make_batch_request(accepted_candidates=tuple(candidates), target_head_sha=target_sha)

    with pytest.raises(CrashInjected, match="batch_ref_publication"):
        first.prepare(request)

    restarted, restarted_drivers = make_composition_integrator(repository)
    action = restarted.prepare(request)

    assert action.batch_sha == drivers.git.read_ref(f"refs/gwo-v8/integration-batches/{action.batch_id}")
    assert restarted_drivers.git.compose_calls == 0
```

`tests/v8_batch_test_support.py` must define `make_disjoint_git_candidates`, `make_advanced_target_candidates`, `make_integrator`, and `CrashInjected(boundary: str)` with real temporary repositories, not a fake SHA-only repository for these tests.

Task 5 now adds the real Git/tree helpers and the composition-only recording
support. It is the first task allowed to import `_batch_integrator_drivers`;
there is still no hosted/publication support import here:

```python
import hashlib
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from gwo_v8._batch_integrator_drivers import GitBatchDriver, GitCliBatchDriver, LocalCheckReceipt, LocalSuiteDriver
from gwo_v8._batch_integrator_store import SqliteBatchDeliveryJournal
from gwo_v8.batch_integrator import AncestorReadback, TargetDeltaReadback
from gwo_v8.batch_patch_identity import CleanBaseAdvanceProof, require_clean_base_advance

class CrashInjected(RuntimeError):
    def __init__(self, boundary: str) -> None:
        self.boundary = boundary
        super().__init__(f"crash injected at {boundary}")


def crash_hook_for(boundary: str | None) -> Callable[[str], None]:
    def hook(observed_boundary: str) -> None:
        if boundary is not None and observed_boundary == boundary:
            raise CrashInjected(observed_boundary)

    return hook


def _run_git(
    repository: Path, *arguments: str, env: dict[str, str] | None = None
) -> str:
    completed = subprocess.run(
        ("git", *arguments),
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )
    return completed.stdout.strip()


def make_disjoint_git_candidates(
    root: Path, *, count: int
) -> tuple[Path, str, tuple[AcceptedCandidateReceipt, ...]]:
    repository = root / "disjoint-repository"
    repository.mkdir(parents=True, exist_ok=True)
    _run_git(repository, "init", "--quiet", "--initial-branch=main")
    _run_git(repository, "config", "user.email", "gwo-tests@example.invalid")
    _run_git(repository, "config", "user.name", "GWO Test Builder")
    (repository / "README.md").write_text("GWO test base\n", encoding="utf-8")
    _run_git(repository, "add", "README.md")
    _run_git(repository, "commit", "--quiet", "-m", "test base")
    target_sha = _run_git(repository, "rev-parse", "HEAD")
    target_tree_oid = _run_git(repository, "rev-parse", "HEAD^{tree}")
    candidates: list[AcceptedCandidateReceipt] = []
    for index in range(1, count + 1):
        branch = f"candidate-{index}"
        _run_git(repository, "switch", "--quiet", "-c", branch, target_sha)
        path = repository / f"module-{index}.py"
        path.write_text(f"VALUE = {index}\n", encoding="utf-8")
        _run_git(repository, "add", path.name)
        _run_git(repository, "commit", "--quiet", "-m", f"candidate {index}")
        candidate_sha = _run_git(repository, "rev-parse", "HEAD")
        candidate_tree_oid = _run_git(repository, "rev-parse", "HEAD^{tree}")
        candidates.append(
            make_accepted_candidate_receipt(
                repository="owner/repo",
                ticket_key=f"issue:{index}",
                accepted_sequence=index,
                base_sha=target_sha,
                base_tree_oid=target_tree_oid,
                candidate_sha=candidate_sha,
                candidate_tree_oid=candidate_tree_oid,
            )
        )
    _run_git(repository, "switch", "--quiet", "main")
    return repository, target_sha, tuple(candidates)


def make_advanced_target_candidates(
    root: Path, *, count: int
) -> tuple[Path, str, tuple[AcceptedCandidateReceipt, ...]]:
    repository, original_target_sha, candidates = make_disjoint_git_candidates(root, count=count)
    _run_git(repository, "switch", "--quiet", "main")
    (repository / "target-only.py").write_text("TARGET_ONLY = True\n", encoding="utf-8")
    _run_git(repository, "add", "target-only.py")
    _run_git(repository, "commit", "--quiet", "-m", "advance target")
    advanced_target_sha = _run_git(repository, "rev-parse", "HEAD")
    assert all(member.base_sha == original_target_sha for member in candidates)
    return repository, advanced_target_sha, candidates


def drop_batch_ref(repository: Path, batch_id: str) -> None:
    _run_git(repository, "update-ref", "-d", f"refs/gwo-v8/integration-batches/{batch_id}")


class RecordingGitBatchDriver:
    def __init__(
        self,
        *,
        crash_hook: Callable[[str], None],
        ancestor_is_ancestor: bool = True,
        target_delta_interaction_keys: tuple[InteractionKey, ...] = (),
    ) -> None:
        self.crash_hook = crash_hook
        self.ancestor_is_ancestor = ancestor_is_ancestor
        self.target_delta_interaction_keys = target_delta_interaction_keys
        self.refs: dict[str, str] = {}
        self.compose_calls = 0
        self.clean_base_advance_calls: list[str] = []
        self.recomputed_patch_digest: str | None = None
        self.created_batch_member_sets: list[tuple[str, ...]] = []
        self.preserved_evidence_digests: list[tuple[str, ...]] = []
        self.singleton_member_candidate_shas: list[str] = []
        self.singleton_member_evidence_digests: list[tuple[str, ...]] = []
        self.resume_directives: list[tuple[str, str]] = []
        self.tree_paths: dict[str, set[str]] = {}

    def read_target(self, target: BatchTarget) -> BatchTarget:
        return target

    def read_ancestor(self, ancestor_sha: str, descendant_sha: str) -> AncestorReadback:
        body = {
            "ancestor_sha": ancestor_sha,
            "descendant_sha": descendant_sha,
            "is_ancestor": self.ancestor_is_ancestor,
        }
        return AncestorReadback(
            **body,
            readback_digest=digest_value({"kind": "ancestor-readback.v1", **body}),
        )

    def read_target_delta(
        self, base_sha: str, target: BatchTarget
    ) -> TargetDeltaReadback:
        return make_target_delta(
            base_sha,
            target.target_head_sha,
            interaction_keys=self.target_delta_interaction_keys,
        )

    def read_ref(self, ref: str) -> str | None:
        return self.refs.get(ref)

    def update_ref_cas(self, ref: str, expected_sha: str | None, new_sha: str) -> str:
        current = self.refs.get(ref)
        if current != expected_sha:
            raise BatchIntegratorError("BATCH_REF_CAS_CONFLICT", f"unexpected current SHA for {ref}")
        self.refs[ref] = new_sha
        return new_sha

    def compose_batch(
        self,
        batch_id: str,
        target: BatchTarget,
        members: tuple[AcceptedCandidateReceipt, ...],
    ) -> str:
        self.compose_calls += 1
        self.created_batch_member_sets.append(tuple(member.ticket_key for member in members))
        if len(members) > 1:
            self.preserved_evidence_digests.extend(
                member.evidence_digests for member in members
            )
        if len(members) == 1:
            self.singleton_member_candidate_shas.append(members[0].candidate_sha)
            self.singleton_member_evidence_digests.append(members[0].evidence_digests)
            batch_sha = members[0].candidate_sha
        else:
            batch_sha = hashlib.sha1(
                digest_value(
                    {
                        "batch_id": batch_id,
                        "target": target.target_head_sha,
                        "members": [member.digest for member in members],
                    }
                ).encode("ascii")
            ).hexdigest()
        self.refs[f"refs/gwo-v8/integration-batches/{batch_id}"] = batch_sha
        self.tree_paths[batch_sha] = {
            f"module-{index}.py" for index in range(1, len(members) + 1)
        }
        self.crash_hook("batch_ref_publication")
        return batch_sha

    def clean_base_advance(
        self,
        batch_id: str,
        target: BatchTarget,
        member: AcceptedCandidateReceipt,
    ) -> CleanBaseAdvanceProof:
        self.clean_base_advance_calls.append(member.ticket_key)
        recomputed = self.recomputed_patch_digest or member.diff_record_digest
        return require_clean_base_advance(
            member=member,
            original_patch_digest=member.diff_record_digest,
            recomputed_patch_digest=recomputed,
            ancestor=self.read_ancestor(member.base_sha, target.target_head_sha),
            target_delta=self.read_target_delta(member.base_sha, target),
            target_tree_oid=target.target_tree_oid,
            advanced_member_tree_oid=member.candidate_tree_oid,
        )

    def tree_contains(self, batch_sha: str, path: str) -> bool:
        return path in self.tree_paths.get(batch_sha, set())


class RecordingLocalSuiteDriver:
    def __init__(self) -> None:
        self.batch_shas: list[str] = []

    def run(self, batch_sha: str, suite: LocalSuiteDefinition) -> LocalCheckReceipt:
        self.batch_shas.append(batch_sha)
        body = {
            "batch_sha": batch_sha,
            "suite_id": suite.suite_id,
            "definition_digest": suite.definition_digest,
            "outcome": "passed",
            "source_ref": f"refs/gwo-v8/integration-batches/{batch_sha}",
        }
        observation_digest = digest_value({"kind": "local-observation.v1", **body})
        return LocalCheckReceipt(
            **body,
            observation_digest=observation_digest,
            receipt_digest=digest_value({"kind": "local-check-receipt.v1", **body, "observation_digest": observation_digest}),
        )




class NoopHostedDriver:
    def read_publication(self, repository, batch_sha):
        return None

    def publish_once(self, repository, batch_sha, manifest_digest):
        raise AssertionError("hosted driver is not part of Task 5")

    def read_pull_request(self, repository, batch_sha):
        raise AssertionError("hosted driver is not part of Task 5")

    def read_hosted_result(self, repository, batch_sha, suite):
        raise AssertionError("hosted driver is not part of Task 5")

    def retry_hosted(self, repository, batch_sha, provider_check_id):
        raise AssertionError("hosted driver is not part of Task 5")

    def integrate_serially(self, repository, batch_sha, target, pull_request):
        raise AssertionError("hosted driver is not part of Task 5")


@dataclass
class CompositionDriverSet:
    git: RecordingGitBatchDriver
    local: RecordingLocalSuiteDriver


def make_composition_integrator(
    repository: Path,
    *,
    ancestor_is_ancestor: bool = True,
    target_delta_interaction_keys: tuple[InteractionKey, ...] = (),
):
    root = Path(repository)
    root.mkdir(parents=True, exist_ok=True)
    crash_hook = crash_hook_for(None)
    if (root / ".git").is_dir() and ancestor_is_ancestor and not target_delta_interaction_keys:
        git = GitCliBatchDriver(root, crash_hook=crash_hook)
    else:
        git = RecordingGitBatchDriver(
            crash_hook=crash_hook,
            ancestor_is_ancestor=ancestor_is_ancestor,
            target_delta_interaction_keys=target_delta_interaction_keys,
        )
    local = RecordingLocalSuiteDriver()
    integrator = BatchIntegrator(
        journal=SqliteBatchDeliveryJournal(root / "v8.sqlite3"),
        git=git,
        local=local,
        hosted=NoopHostedDriver(),
        configuration=BatchIntegratorConfiguration(),
    )
    return integrator, CompositionDriverSet(git=git, local=local)
```


- [ ] **Step 2: Run RED.**

```powershell
py -3.13 -m pytest tests/test_v8_batch_integrator.py::test_composes_three_members_without_moving_target_and_reads_exact_tree -q
```

Expected: FAIL during collection because Task 5 has not created
`_batch_integrator_drivers.py`/`GitCliBatchDriver` yet; after that import is
present, the same test remains RED because `BatchIntegrator.prepare` has no
real composition/readback implementation.

- [ ] **Step 3: Implement deterministic Git composition and local readback.**

Define the production adapter constructor before its protocol methods:

```python
from pathlib import Path
from typing import Callable

class GitCliBatchDriver:
    def __init__(self, repository: Path, *, crash_hook: Callable[[str], None]) -> None:
        self.repository = repository
        self.crash_hook = crash_hook
        self.compose_calls = 0
        self.clean_base_advance_calls: list[str] = []
```

The multi-member commit is deterministic by construction, not merely by
message. Use these exact identity and timestamp values for every Batch merge
commit, while keeping the candidate commits' identities untouched:

```python
import os

DETERMINISTIC_AUTHOR_NAME = "GWO V8 Batch Integrator"
DETERMINISTIC_AUTHOR_EMAIL = "gwo-v8-batch-integrator@example.invalid"
DETERMINISTIC_COMMIT_DATE = "1970-01-01T00:00:00Z"

commit_env = os.environ.copy()
commit_env.update(
    {
        "GIT_AUTHOR_NAME": DETERMINISTIC_AUTHOR_NAME,
        "GIT_AUTHOR_EMAIL": DETERMINISTIC_AUTHOR_EMAIL,
        "GIT_AUTHOR_DATE": DETERMINISTIC_COMMIT_DATE,
        "GIT_COMMITTER_NAME": DETERMINISTIC_AUTHOR_NAME,
        "GIT_COMMITTER_EMAIL": DETERMINISTIC_AUTHOR_EMAIL,
        "GIT_COMMITTER_DATE": DETERMINISTIC_COMMIT_DATE,
    }
)
run_git(
    "commit",
    "--no-edit",
    "--author",
    f"{DETERMINISTIC_AUTHOR_NAME} <{DETERMINISTIC_AUTHOR_EMAIL}>",
    "--date",
    DETERMINISTIC_COMMIT_DATE,
    env=commit_env,
)
```

`run_git` passes `env` through to `subprocess.run`; parent order is the sorted
`integration_node_key` order, the tree is read back before the ref CAS, and
the commit message is exactly `f"GWO V8 Integration Batch {batch_id}"`. Thus a
lost local ref followed by recomposition in the same repository produces the
same tree and commit SHA, which is asserted by the preceding test.

`GitCliBatchDriver.compose_batch` must:

1. read the target branch and every Candidate commit/tree before any write;
2. for every Clean Base Advance member, call `read_ancestor(member.base_sha,
   target.target_head_sha)` and `read_target_delta(member.base_sha, target)`;
   validate both digests and fail closed when the base is not an ancestor or
   the target delta contains any protected Interaction Key;
3. read the ref named by `f"refs/gwo-v8/integration-batches/{batch_id}"`; return its exact SHA after validating it contains the requested member trees, or fail with `BATCH_REF_IDENTITY_MISMATCH`;
4. for a same-base Singleton, use the exact Candidate SHA;
5. for a multi-member Batch, create a detached worktree at the common base, merge Candidate SHAs in sorted `integration_node_key` order with `--no-ff --no-commit`, abort on conflict, and create the deterministic merge commit above;
6. for Clean Base Advance, apply each Candidate alone to the exact target in an isolated worktree, reject any conflict, recompute `PatchIdentityV1(target_tree, advanced_member_tree)`, and require equality with the stored original digest before composing multiple members;
7. update the local ref with `update-ref` compare-and-swap and read it back; never move the configured target branch during preparation.

The local driver must run `LocalSuiteDefinition.command` with `shell=False` at the exact detached `batch_sha`, capture stdout/stderr digests, and return:

```python
@dataclass(frozen=True)
class LocalCheckReceipt:
    batch_sha: str
    suite_id: str
    definition_digest: str
    outcome: Literal["passed", "code_failure", "infrastructure_failure"]
    observation_digest: str
    source_ref: str
    receipt_digest: str
```

Before Task 7 adds durable action replay, Task 5 replaces the Task 1 minimal
`prepare` body with this concrete composition body so its RED/GREEN tests
exercise the real Git ref and tree:

```python
def prepare(self, request: BatchDeliveryRequest) -> BatchDeliveryAction:
    self._validate_request(request)
    self.formation_calls += 1
    target = self.git.read_target(request.target)
    members = form_batch_members(
        request.accepted_candidates,
        target,
        member_limit=self.configuration.member_limit_for(request.repository),
    )
    if not members:
        raise BatchIntegratorError("BATCH_EMPTY", "no eligible accepted Candidate")
    batch_id = digest_value(
        {
            "kind": "batch-id.v1",
            "campaign_key": request.campaign_key,
            "plan_revision_digest": request.plan_revision_digest,
            "target": target.target_facts_digest,
            "members": [member.digest for member in members],
        }
    )
    batch_sha = self.git.compose_batch(batch_id, target, members)
    self._requests[request.stable_action_id] = request
    return BatchDeliveryAction(
        stable_action_id=request.stable_action_id,
        request_digest=request.request_digest,
        batch_id=batch_id,
        batch_sha=batch_sha,
        member_ticket_keys=tuple(member.ticket_key for member in members),
    )
```

- [ ] **Step 4: Run GREEN and the local exact-SHA gate.**

```powershell
py -3.13 -m pytest tests/test_v8_batch_integrator.py -k "compose or clean_base or crash_after_batch_ref" -q
```

Expected: all selected tests PASS. Confirm the local suite's receipt and the Git ref both name the same exact `batch_sha`; a local-suite failure does not mutate target or Candidates.

- [ ] **Step 5: Commit exact composition.**

```powershell
py -3.13 scripts/sync_orchestrator.py
py -3.13 scripts/sync_orchestrator.py --check
git add skills/orchestrator/scripts/gwo_v8/_batch_integrator_drivers.py skills/orchestrator/scripts/gwo_v8/batch_integrator.py skills/orchestrator/scripts/gwo_v8/batch_patch_identity.py skills/orchestrator/.skill-package.json tests/v8_batch_test_support.py tests/test_v8_batch_integrator.py
git commit -m "feat: compose and verify immutable Batch SHAs"
```

### Task 6: Implement publication, PR, hosted-CI, Integration-Lease, and target readback

**Files:**
- Modify: `skills/orchestrator/scripts/gwo_v8/_batch_integrator_drivers.py`
- Modify: `skills/orchestrator/scripts/gwo_v8/_batch_integrator_store.py`
- Modify: `skills/orchestrator/scripts/gwo_v8/batch_integrator.py`
- Modify: `tests/v8_batch_test_support.py`
- Modify: `tests/test_v8_batch_integrator.py`

**Interfaces:**
- Consumes: exact `batch_sha`, `LocalCheckReceipt`, Batch Check Manifest, and `IntegrationLeaseReceipt`.
- Produces: immutable `BatchPublicationReceipt`, `PullRequestReadback`, `HostedResultObservation`, `TargetIntegrationReadback`, and `BatchDeliveryProof`; a complete `BatchDeliveryObservation` exposes an ordered, exact proof partition for `ResultIntegrityProof` and never asks composition to infer delivery facts. A direct Batch has one proof; a completed fallback parent has one child Singleton proof per member.

- [ ] **Step 1: Write the failing exact-boundary tests.**

Add these exact tests:

```python
from dataclasses import replace
import re


def test_local_suite_publication_pr_hosted_ci_and_target_name_one_batch_sha(tmp_path):
    integrator, drivers = make_integrator(tmp_path, hosted_outcomes=("passed",))
    action = integrator.prepare(make_batch_request(accepted_candidates=make_three_standard_receipts()))

    observation = integrator.execute(action)

    assert observation.phase == "complete"
    assert drivers.local.batch_shas == [action.batch_sha]
    assert drivers.hosted.published_shas == [action.batch_sha]
    assert drivers.hosted.hosted_read_shas == [action.batch_sha]
    assert drivers.hosted.integrated_shas == [action.batch_sha]
    assert drivers.hosted.pull_request_heads == [action.batch_sha]
    assert len(observation.delivery_proofs) == 1
    proof = observation.delivery_proofs[0]
    assert proof.delivery_stable_action_id == action.stable_action_id
    assert proof.delivery_request_digest == action.request_digest
    assert proof.batch_id == action.batch_id
    assert proof.batch_sha == action.batch_sha
    assert proof.member_ticket_keys == action.member_ticket_keys
    assert proof.pull_request_number == 1
    assert proof.pull_request_head_sha == action.batch_sha
    assert proof.target_branch == "main"
    assert proof.target_contains_batch_sha is True
    assert proof.pull_request_merge_target_sha == proof.target_head_sha
    assert proof.merge_method == "merge"
    for digest in (
        proof.local_check_receipt_digest,
        proof.publication_receipt_digest,
        proof.hosted_result_receipt_digest,
        proof.integration_lease_digest,
        proof.target_readback_digest,
        proof.proof_digest,
    ):
        assert re.fullmatch(r"[0-9a-f]{64}", digest)


def test_complete_observation_rejects_any_tampered_delivery_proof(tmp_path):
    integrator, _drivers = make_integrator(tmp_path, hosted_outcomes=("passed",))
    action = integrator.prepare(
        make_batch_request(accepted_candidates=make_three_standard_receipts())
    )
    observation = integrator.execute(action)
    assert len(observation.delivery_proofs) == 1

    with pytest.raises(DeliveryIdentityMismatch):
        replace(
            observation,
            delivery_proofs=(
                replace(
                    observation.delivery_proofs[0],
                    target_head_sha="f" * 40,
                ),
            ),
        )


def test_publication_wrong_batch_sha_is_identity_failure_without_retry_or_resume(tmp_path):
    integrator, drivers = make_integrator(tmp_path, publication_batch_sha="f" * 40)
    action = integrator.prepare(make_batch_request(accepted_candidates=make_three_standard_receipts()))

    with pytest.raises(DeliveryIdentityMismatch):
        integrator.execute(action)

    assert drivers.hosted.publish_calls == 0
    assert drivers.hosted.published_shas == []
    assert drivers.hosted.retry_calls == 0


def test_hosted_result_wrong_suite_or_provider_check_is_identity_failure(tmp_path):
    integrator, drivers = make_integrator(tmp_path, hosted_identity_mismatch="suite")
    action = integrator.prepare(make_batch_request(accepted_candidates=make_three_standard_receipts()))

    with pytest.raises(DeliveryIdentityMismatch):
        integrator.execute(action)

    assert drivers.hosted.retry_calls == 0
    assert integrator.readback(action) is not None


def test_target_readback_accepts_merge_commit_only_when_batch_is_ancestor(tmp_path):
    integrator, drivers = make_integrator(tmp_path, target_merge_method="merge", target_contains_batch=True)
    action = integrator.prepare(make_batch_request(accepted_candidates=make_three_standard_receipts()))

    assert integrator.execute(action).phase == "complete"
    assert drivers.hosted.integrated_shas == [action.batch_sha]


def test_target_readback_rejects_a_merge_mapping_not_equal_to_target_head(tmp_path):
    integrator, drivers = make_integrator(
        tmp_path,
        delivery_failure="wrong_merge_target",
    )
    action = integrator.prepare(
        make_batch_request(accepted_candidates=make_three_standard_receipts())
    )

    with pytest.raises(DeliveryIdentityMismatch):
        integrator.execute(action)

    assert drivers.hosted.integrated_shas == [action.batch_sha]
    readback = integrator.readback(action)
    assert readback is None or readback.phase != "complete"


@pytest.mark.parametrize("merge_method", ["squash", "rebase"])
def test_target_readback_rejects_identity_rewriting_merge_methods(tmp_path, merge_method):
    integrator, drivers = make_integrator(tmp_path, target_merge_method=merge_method)
    action = integrator.prepare(make_batch_request(accepted_candidates=make_three_standard_receipts()))

    with pytest.raises(DeliveryIdentityMismatch):
        integrator.execute(action)

    assert drivers.hosted.integrated_shas == []
```

Only after Task 5 creates `_batch_integrator_drivers.py` does the support
module add the hosted/publication value imports and the final integration
assembly:

```python
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Literal

from gwo_v8._batch_integrator_drivers import (
    BatchPublicationReceipt,
    HostedBatchDriver,
    HostedResultObservation,
    LocalCheckReceipt,
    PullRequestReadback,
    TargetIntegrationReadback,
)
from gwo_v8._batch_integrator_store import SqliteBatchDeliveryJournal

class RecordingHostedBatchDriver:
    def __init__(
        self,
        *,
        outcomes: tuple[Literal["passed", "code_failure", "infrastructure_failure"], ...],
        publication_batch_sha: str | None,
        identity_mismatch: Literal["suite", "provider"] | None,
        target_merge_method: Literal["merge", "squash", "rebase", "unknown"],
        target_contains_batch: bool,
        delivery_failure: Literal[
            "wrong_batch_sha",
            "wrong_merge_target",
            "ambiguous_provider",
        ] | None,
    ) -> None:
        self.outcomes = outcomes
        self.publication_batch_sha = publication_batch_sha
        self.identity_mismatch = identity_mismatch
        self.target_merge_method = target_merge_method
        self.target_contains_batch = target_contains_batch
        self.delivery_failure = delivery_failure
        self.publish_calls = 0
        self.hosted_read_calls = 0
        self.integrate_calls = 0
        self.retry_calls = 0
        self.published_shas: list[str] = []
        self.hosted_read_shas: list[str] = []
        self.pull_request_heads: list[str] = []
        self.integrated_shas: list[str] = []
        self.retry_shas: list[str] = []
        self.target_mutations: list[str] = []

    def read_publication(self, repository: str, batch_sha: str) -> BatchPublicationReceipt | None:
        if self.publication_batch_sha is None:
            return None
        body = {
            "repository": repository,
            "batch_sha": self.publication_batch_sha,
            "branch_ref": "refs/heads/gwo/batches/test",
            "evidence_manifest_digest": "a" * 64,
            "source_ref": "github:publication-readback",
        }
        return BatchPublicationReceipt(
            **body,
            receipt_digest=digest_value({"kind": "batch-publication.v1", **body}),
        )

    def publish_once(self, repository: str, batch_sha: str, manifest_digest: str) -> BatchPublicationReceipt:
        self.publish_calls += 1
        published_sha = batch_sha
        self.published_shas.append(published_sha)
        body = {
            "repository": repository,
            "batch_sha": published_sha,
            "branch_ref": "refs/heads/gwo/batches/test",
            "evidence_manifest_digest": manifest_digest,
            "source_ref": "github:publication",
        }
        return BatchPublicationReceipt(
            **body,
            receipt_digest=digest_value({"kind": "batch-publication.v1", **body}),
        )

    def read_pull_request(self, repository: str, batch_sha: str) -> PullRequestReadback:
        self.pull_request_heads.append(batch_sha)
        body = {
            "number": 1,
            "repository": repository,
            "head_sha": batch_sha,
            "base_branch": "main",
            "merge_commit_sha": (
                "d" * 40
                if self.delivery_failure == "wrong_merge_target"
                else "e" * 40
            ),
            "merge_method": self.target_merge_method,
            "source_ref": "github:pull-request",
        }
        return PullRequestReadback(
            **body,
            readback_digest=digest_value({"kind": "pull-request-readback.v1", **body}),
        )

    def read_hosted_result(
        self, repository: str, batch_sha: str, suite: HostedSuiteDefinition
    ) -> HostedResultObservation:
        if self.delivery_failure == "ambiguous_provider":
            raise DeliveryAttributionAmbiguous("two provider checks matched the exact hosted suite")
        self.hosted_read_calls += 1
        self.hosted_read_shas.append(batch_sha)
        outcome = self.outcomes[min(self.hosted_read_calls - 1, len(self.outcomes) - 1)]
        suite_id = "wrong-suite" if self.identity_mismatch == "suite" else suite.suite_id
        provider_check_id = "wrong-check" if self.identity_mismatch == "provider" else "check:1"
        return HostedResultObservation(
            repository=repository,
            batch_sha=batch_sha,
            suite_id=suite_id,
            provider_check_id=provider_check_id,
            outcome=outcome,
            observation_digest="e" * 64,
            source_ref="checks:hosted",
        )

    def retry_hosted(self, repository: str, batch_sha: str, provider_check_id: str) -> None:
        self.retry_calls += 1
        self.retry_shas.append(batch_sha)

    def integrate_serially(
        self,
        repository: str,
        batch_sha: str,
        target: BatchTarget,
        pull_request: PullRequestReadback,
    ) -> TargetIntegrationReadback:
        self.integrate_calls += 1
        self.integrated_shas.append(batch_sha)
        self.target_mutations.append(batch_sha)
        observed_batch_sha = "f" * 40 if self.delivery_failure == "wrong_batch_sha" else batch_sha
        body = {
            "repository": repository,
            "target_branch": target.target_branch,
            "target_head_sha": "e" * 40,
            "batch_sha": observed_batch_sha,
            "pull_request_number": pull_request.number,
            "pull_request_head_sha": pull_request.head_sha,
            "merge_commit_sha": "d" * 40,
            "merge_method": self.target_merge_method,
            "batch_is_ancestor": self.target_contains_batch,
            "source_ref": "github:target-readback",
        }
        return TargetIntegrationReadback(
            **body,
            readback_digest=digest_value({"kind": "target-readback.v1", **body}),
        )




@dataclass
class RecordingDriverSet:
    git: GitBatchDriver
    local: LocalSuiteDriver
    hosted: HostedBatchDriver
    forbidden_boundary_calls: int = 0
    candidategate_calls: int = 0
    review_calls: int = 0
    integrator: BatchIntegrator | None = None

    @property
    def formation_calls(self) -> int:
        return 0 if self.integrator is None else self.integrator.formation_calls

    @property
    def composition_calls(self) -> int:
        return self.git.compose_calls

    @property
    def batch_shas(self) -> list[str]:
        return self.local.batch_shas

    @property
    def target_mutations(self) -> list[str]:
        return self.hosted.target_mutations

    @property
    def created_batch_member_sets(self) -> list[tuple[str, ...]]:
        return self.git.created_batch_member_sets

    @property
    def preserved_evidence_digests(self) -> list[tuple[str, ...]]:
        return self.git.preserved_evidence_digests

    @property
    def singleton_member_candidate_shas(self) -> list[str]:
        return self.git.singleton_member_candidate_shas

    @property
    def singleton_member_evidence_digests(self) -> list[tuple[str, ...]]:
        return self.git.singleton_member_evidence_digests

    @property
    def resume_directives(self) -> list[tuple[str, str]]:
        return self.git.resume_directives


def make_integrator(
    repository: Path,
    *,
    hosted_outcomes: tuple[Literal["passed", "code_failure", "infrastructure_failure"], ...] = (),
    publication_batch_sha: str | None = None,
    hosted_identity_mismatch: Literal["suite", "provider"] | None = None,
    target_merge_method: Literal["merge", "squash", "rebase", "unknown"] = "merge",
    target_contains_batch: bool = True,
    crash_after: str | None = None,
    delivery_failure: Literal[
        "wrong_batch_sha",
        "wrong_merge_target",
        "ambiguous_provider",
    ] | None = None,
) -> tuple[BatchIntegrator, RecordingDriverSet]:
    root = Path(repository)
    root.mkdir(parents=True, exist_ok=True)
    store_path = root / "v8.sqlite3"
    crash_hook = crash_hook_for(crash_after)
    if (root / ".git").is_dir():
        git: GitBatchDriver = GitCliBatchDriver(root, crash_hook=crash_hook)
    else:
        git = RecordingGitBatchDriver(crash_hook=crash_hook)
    local = RecordingLocalSuiteDriver()
    hosted = RecordingHostedBatchDriver(
        outcomes=hosted_outcomes or ("passed",),
        publication_batch_sha=publication_batch_sha,
        identity_mismatch=hosted_identity_mismatch,
        target_merge_method=target_merge_method,
        target_contains_batch=target_contains_batch,
        delivery_failure=delivery_failure,
    )
    drivers = RecordingDriverSet(git=git, local=local, hosted=hosted)
    integrator = BatchIntegrator(
        journal=SqliteBatchDeliveryJournal(store_path, crash_hook=crash_hook),
        git=git,
        local=local,
        hosted=hosted,
        configuration=BatchIntegratorConfiguration(),
    )
    drivers.integrator = integrator
    return integrator, drivers
```

`make_integrator` passes the same `crash_hook` to both the Git driver and
`SqliteBatchDeliveryJournal`. The journal invokes that hook only after the
hosted receipt INSERT and COMMIT have completed; `hosted_receipt_persisted`
therefore crashes before target integration while leaving a durable receipt
for restart adoption.


- [ ] **Step 2: Run RED.**

```powershell
py -3.13 -m pytest tests/test_v8_batch_integrator.py::test_local_suite_publication_pr_hosted_ci_and_target_name_one_batch_sha -q
```

Expected: FAIL during collection because Task 6 has not yet added the hosted
receipt value classes to `_batch_integrator_drivers.py`; after those imports
exist, the test remains RED because publication/PR/hosted/target validation is
not implemented.

- [ ] **Step 3: Implement private drivers and the serialized integration boundary.**

Define these exact values in `_batch_integrator_drivers.py`:

```python
@dataclass(frozen=True)
class BatchPublicationReceipt:
    repository: str
    batch_sha: str
    branch_ref: str
    evidence_manifest_digest: str
    source_ref: str
    receipt_digest: str


@dataclass(frozen=True)
class PullRequestReadback:
    number: int
    repository: str
    head_sha: str
    base_branch: str
    merge_commit_sha: str | None
    merge_method: Literal["merge", "squash", "rebase", "unknown"]
    source_ref: str
    readback_digest: str


@dataclass(frozen=True)
class HostedResultObservation:
    repository: str
    batch_sha: str
    suite_id: str
    provider_check_id: str
    outcome: Literal["pending", "passed", "infrastructure_failure", "code_failure"]
    observation_digest: str
    source_ref: str


@dataclass(frozen=True)
class TargetIntegrationReadback:
    repository: str
    target_branch: str
    target_head_sha: str
    batch_sha: str
    pull_request_number: int
    pull_request_head_sha: str
    merge_commit_sha: str
    merge_method: Literal["merge", "squash", "rebase", "unknown"]
    batch_is_ancestor: bool
    source_ref: str
    readback_digest: str
```

Task 6 supplies the one-shot delivery body that its exact-boundary tests run;
Task 7 wraps the same effect sequence in durable readback-first/CAS replay:

```python
def execute(self, action: BatchDeliveryAction) -> BatchDeliveryObservation:
    request = self._requests[action.stable_action_id]
    local_receipt = self.local.run(action.batch_sha, request.local_suite)
    if local_receipt.batch_sha != action.batch_sha or local_receipt.outcome != "passed":
        raise BatchIntegratorError("BATCH_LOCAL_CHECK_FAILED", "exact local suite did not pass")

    publication = self.hosted.read_publication(request.repository, action.batch_sha)
    if publication is None:
        publication = self.hosted.publish_once(
            request.repository, action.batch_sha, request.plan_revision_digest
        )
    if publication.batch_sha != action.batch_sha:
        raise DeliveryIdentityMismatch("publication readback named a different Batch SHA")

    pull_request = self.hosted.read_pull_request(request.repository, action.batch_sha)
    if pull_request.head_sha != action.batch_sha or pull_request.merge_method != "merge":
        raise DeliveryIdentityMismatch("PR readback rewrote the reviewed Batch identity")
    hosted_result = self.hosted.read_hosted_result(
        request.repository, action.batch_sha, request.hosted_suites[0]
    )
    if hosted_result.batch_sha != action.batch_sha or hosted_result.outcome != "passed":
        raise BatchIntegratorError("BATCH_HOSTED_CHECK_FAILED", "exact hosted check did not pass")
    hosted_receipt = self.journal.persist_hosted_result(
        HostedResultReceipt.create(
            stable_action_id=action.stable_action_id,
            batch_sha=hosted_result.batch_sha,
            suite_id=hosted_result.suite_id,
            provider_check_id=hosted_result.provider_check_id,
            outcome=hosted_result.outcome,
            observation_digest=hosted_result.observation_digest,
            source_ref=hosted_result.source_ref,
        )
    )

    lease = self.journal.acquire_integration_lease(
        request.repository,
        action.stable_action_id,
        request.writer_generation,
        request.activation_id,
    )
    try:
        target_readback = self.hosted.integrate_serially(
            request.repository, action.batch_sha, request.target, pull_request
        )
        if (
            target_readback.repository != request.repository
            or target_readback.target_branch != request.target.target_branch
            or target_readback.batch_sha != action.batch_sha
            or target_readback.pull_request_number != pull_request.number
            or target_readback.pull_request_head_sha != action.batch_sha
            or not target_readback.batch_is_ancestor
            or target_readback.merge_method != "merge"
            or target_readback.merge_commit_sha != target_readback.target_head_sha
        ):
            raise DeliveryIdentityMismatch("target readback did not prove exact Batch ancestry")
        delivery_proof = BatchDeliveryProof.create(
            delivery_stable_action_id=action.stable_action_id,
            delivery_request_digest=action.request_digest,
            batch_id=action.batch_id,
            batch_sha=action.batch_sha,
            member_ticket_keys=action.member_ticket_keys,
            local_check_receipt_digest=local_receipt.receipt_digest,
            publication_receipt_digest=publication.receipt_digest,
            pull_request_number=target_readback.pull_request_number,
            pull_request_head_sha=target_readback.pull_request_head_sha,
            hosted_result_receipt_digest=hosted_receipt.receipt_digest,
            integration_lease_digest=lease.lease_digest,
            target_branch=target_readback.target_branch,
            target_head_sha=target_readback.target_head_sha,
            target_readback_digest=target_readback.readback_digest,
            target_contains_batch_sha=target_readback.batch_is_ancestor,
            pull_request_merge_target_sha=target_readback.merge_commit_sha,
            merge_method=target_readback.merge_method,
        )
    finally:
        self.journal.release_integration_lease(
            request.repository, lease
        )
    members = tuple(
        MemberDeliveryObservation(
            ticket_key=member.ticket_key,
            work_run_key=member.work_run_key,
            candidate_sha=member.candidate_sha,
            status="integrated",
            evidence_digests=member.evidence_digests,
        )
        for member in request.accepted_candidates
    )
    observation_body = {
        "stable_action_id": action.stable_action_id,
        "batch_id": action.batch_id,
        "batch_sha": action.batch_sha,
        "phase": "complete",
        "reason": "integrated",
        "retry_count": 0,
        "fallback_generation": 0,
        "members": [member.__dict__ for member in members],
        "delivery_proofs": [delivery_proof.canonical()],
    }
    return BatchDeliveryObservation(
        stable_action_id=action.stable_action_id,
        batch_id=action.batch_id,
        batch_sha=action.batch_sha,
        phase="complete",
        reason="integrated",
        receipt_digest=digest_value(
            {"kind": "batch-observation.v1", **observation_body}
        ),
        retry_count=0,
        fallback_generation=0,
        members=members,
        delivery_proofs=(delivery_proof,),
    )


def _advance_one_delivery_step(
    self,
    record: BatchJournalRecord,
    action: BatchDeliveryAction,
    request: BatchDeliveryRequest,
) -> BatchJournalRecord:
    state = json.loads(record.state_json or "{}")
    from ._batch_integrator_drivers import (
        BatchPublicationReceipt,
        LocalCheckReceipt,
        PullRequestReadback,
    )
    from ._batch_integrator_store import HostedResultReceipt

    def transition(
        phase: str,
        reason: str,
        *,
        retry_count: int = record.retry_count,
        fallback_generation: int = record.fallback_generation,
    ) -> BatchJournalRecord:
        return replace(
            record,
            phase=phase,
            reason=reason,
            retry_count=retry_count,
            fallback_generation=fallback_generation,
            state_json=json.dumps(state, sort_keys=True, separators=(",", ":")),
            version=record.version + 1,
        )

    if record.phase == "prepared":
        local = self.local.run(action.batch_sha, request.local_suite)
        if local.batch_sha != action.batch_sha:
            raise DeliveryIdentityMismatch("local receipt named a different Batch SHA")
        if local.outcome != "passed":
            return self._classify_failure(record, action, request, local.outcome, state)
        state["local_receipt"] = asdict(local)
        return transition("local_checked", "local_suite_passed")

    if record.phase == "local_checked":
        publication = self.hosted.read_publication(request.repository, action.batch_sha)
        if publication is None:
            publication = self.hosted.publish_once(
                request.repository, action.batch_sha, request.plan_revision_digest
            )
        if publication.batch_sha != action.batch_sha:
            raise DeliveryIdentityMismatch("publication readback named a different Batch SHA")
        state["publication"] = asdict(publication)
        return transition("published", "publication_verified")

    if record.phase == "published":
        pull_request = self.hosted.read_pull_request(request.repository, action.batch_sha)
        if (
            pull_request.head_sha != action.batch_sha
            or pull_request.base_branch != request.target.target_branch
            or pull_request.merge_method != "merge"
        ):
            raise DeliveryIdentityMismatch("PR readback rewrote the reviewed Batch identity")
        state["pull_request"] = asdict(pull_request)
        suite = request.hosted_suites[0]
        receipt = self.journal.read_hosted_result(
            action.stable_action_id, action.batch_sha, suite.suite_id, "check:1"
        )
        if receipt is None:
            observed = self.hosted.read_hosted_result(
                request.repository, action.batch_sha, suite
            )
            if (
                observed.batch_sha != action.batch_sha
                or observed.suite_id != suite.suite_id
                or observed.provider_check_id != "check:1"
            ):
                raise DeliveryIdentityMismatch("hosted readback did not match suite identity")
            receipt = HostedResultReceipt.create(
                stable_action_id=action.stable_action_id,
                batch_sha=action.batch_sha,
                suite_id=observed.suite_id,
                provider_check_id=observed.provider_check_id,
                outcome=observed.outcome,
                observation_digest=observed.observation_digest,
                source_ref=observed.source_ref,
            )
            receipt = self.journal.persist_hosted_result(receipt)
        state["hosted_receipt"] = asdict(receipt)
        if receipt.outcome == "infrastructure_failure":
            if record.retry_count >= 2:
                return transition("blocked", "InfrastructureRetryLimitExceeded")
            self.hosted.retry_hosted(
                request.repository, action.batch_sha, receipt.provider_check_id
            )
            state["retry_resume_phase"] = "published"
            return transition(
                "wait",
                "InfrastructureRetryScheduled",
                retry_count=record.retry_count + 1,
            )
        if receipt.outcome == "code_failure":
            return self._classify_failure(record, action, request, receipt.outcome, state)
        return transition("hosted", "hosted_receipt_verified")

    if record.phase == "wait" and state.get("retry_resume_phase") == "published":
        state.pop("retry_resume_phase", None)
        return transition("published", "retry_readback")

    if record.phase == "wait" and state.get("singleton_queue"):
        return self._advance_singleton_queue(record, action, request, state)

    if record.phase == "hosted":
        pull_request = PullRequestReadback(**state["pull_request"])
        lease = self.journal.acquire_integration_lease(
            request.repository,
            action.stable_action_id,
            request.writer_generation,
            request.activation_id,
        )
        try:
            target_readback = self.hosted.integrate_serially(
                request.repository, action.batch_sha, request.target, pull_request
            )
            if (
                target_readback.repository != request.repository
                or target_readback.target_branch != request.target.target_branch
                or target_readback.batch_sha != action.batch_sha
                or target_readback.pull_request_number != pull_request.number
                or target_readback.pull_request_head_sha != action.batch_sha
                or target_readback.merge_method != "merge"
                or not target_readback.batch_is_ancestor
                or target_readback.merge_commit_sha
                != target_readback.target_head_sha
            ):
                raise DeliveryIdentityMismatch(
                    "target readback did not prove exact Batch ancestry"
                )
        finally:
            self.journal.release_integration_lease(
                request.repository, lease
            )
        local_receipt = LocalCheckReceipt(**state["local_receipt"])
        publication = BatchPublicationReceipt(**state["publication"])
        hosted_receipt = HostedResultReceipt(**state["hosted_receipt"])
        delivery_proof = BatchDeliveryProof.create(
            delivery_stable_action_id=action.stable_action_id,
            delivery_request_digest=action.request_digest,
            batch_id=action.batch_id,
            batch_sha=action.batch_sha,
            member_ticket_keys=action.member_ticket_keys,
            local_check_receipt_digest=local_receipt.receipt_digest,
            publication_receipt_digest=publication.receipt_digest,
            pull_request_number=target_readback.pull_request_number,
            pull_request_head_sha=target_readback.pull_request_head_sha,
            hosted_result_receipt_digest=hosted_receipt.receipt_digest,
            integration_lease_digest=lease.lease_digest,
            target_branch=target_readback.target_branch,
            target_head_sha=target_readback.target_head_sha,
            target_readback_digest=target_readback.readback_digest,
            target_contains_batch_sha=target_readback.batch_is_ancestor,
            pull_request_merge_target_sha=target_readback.merge_commit_sha,
            merge_method=target_readback.merge_method,
        )
        state["target_readback"] = asdict(target_readback)
        state["delivery_proofs"] = [delivery_proof.canonical()]
        for member in state["members"]:
            member["status"] = "integrated"
        return transition("complete", "integrated")

    raise BatchIntegratorError(
        "BATCH_PHASE_INVALID",
        f"cannot advance action from phase {record.phase}",
    )
```

The GitHub driver must push `batch_sha` exactly once to the branch ref named by `f"refs/heads/gwo/batches/{batch_id}"`, create or read one PR for that branch, and read back PR number, head SHA, base branch, merge method, and merge mapping. Hosted readback must select only the required workflow/check names and exact `headSha`; zero or multiple ambiguous provider matches raise `DeliveryAttributionAmbiguous`. Before target mutation, acquire the repository-global Integration Lease with exact writer generation and Activation ID; after integration, read the remote target and prove the repository/branch, PR number/head, Batch ancestry, merge method, merge commit, and target head all match. `BatchDeliveryProof` copies PR number/head, merge method, and merge-target SHA from that `TargetIntegrationReadback`; it never assigns those facts from the request or a constant. Any squash, rebase, changed head, wrong target, wrong merge mapping, wrong suite, wrong provider check, or wrong observation digest raises the named identity error without fallback. The wrong-publication test injects the wrong SHA only from `read_publication` before `publish_once`; `publish_once` always receives and would publish the requested SHA, so the fail-closed assertion of zero publish calls is the intended semantics, not a post-publication mismatch.

- [ ] **Step 4: Run GREEN and exact delivery verification.**

```powershell
py -3.13 -m pytest tests/test_v8_batch_integrator.py -k "publication or hosted or target_readback or one_batch_sha" -q
```

Expected: all selected tests PASS. The success test must show one publication, one PR head, one hosted suite, one Integration-Lease-protected target mutation, and one target readback for the same immutable Batch SHA.

- [ ] **Step 5: Commit the exact delivery boundary.**

```powershell
py -3.13 scripts/sync_orchestrator.py
py -3.13 scripts/sync_orchestrator.py --check
git add skills/orchestrator/scripts/gwo_v8/_batch_integrator_drivers.py skills/orchestrator/scripts/gwo_v8/_batch_integrator_store.py skills/orchestrator/scripts/gwo_v8/batch_integrator.py skills/orchestrator/.skill-package.json tests/v8_batch_test_support.py tests/test_v8_batch_integrator.py
git commit -m "feat: deliver Batch SHAs through exact hosted and target boundaries"
```

### Task 7: Complete the normal V3 BatchIntegrator action loop and quarantine the predecessor assembler

**Files:**
- Modify: skills/orchestrator/scripts/gwo_v8/batch_integrator.py
- Modify: skills/orchestrator/scripts/gwo_v8/__init__.py
- Modify: skills/orchestrator/scripts/gwo_v8/integration_batch.py
- Modify: tests/v8_batch_test_support.py
- Modify: tests/test_v8_batch_integrator.py
- Modify: tests/test_orchestrator_v8_integration_batch.py

**Interfaces:**
- Consumes: Tasks 1–6 and the future ProductionWorkRunEffects mapping of WorkRunAction to BatchDeliveryAction.
- Produces: idempotent BatchIntegrator.prepare/readback/execute, typed member Work Run/Evidence mapping, and a direct-import-only predecessor compatibility module.

- [ ] **Step 1: Write the failing action-loop tests.**

Add these exact tests:

~~~python
def test_duplicate_execute_does_not_repeat_publication_hosted_ci_or_target_mutation(tmp_path):
    integrator, drivers = make_integrator(tmp_path, hosted_outcomes=("passed",))
    action = integrator.prepare(make_batch_request(accepted_candidates=make_three_standard_receipts()))

    first = integrator.execute(action)
    second = integrator.execute(action)

    assert first == second
    assert drivers.hosted.publish_calls == 1
    assert drivers.hosted.hosted_read_calls == 1
    assert drivers.hosted.integrate_calls == 1


def test_terminal_journal_readback_returns_without_runtime_candidategate_or_provider_call(tmp_path):
    integrator, drivers = make_integrator(tmp_path, hosted_outcomes=("passed",))
    action = integrator.prepare(make_batch_request(accepted_candidates=make_three_standard_receipts()))
    expected = integrator.execute(action)
    restarted, restarted_drivers = make_integrator(tmp_path)

    actual = restarted.readback(action)

    assert actual == expected
    assert restarted_drivers.hosted.hosted_read_calls == 0
    assert restarted_drivers.forbidden_boundary_calls == 0


def test_batch_observation_preserves_each_work_run_and_evidence_identity(tmp_path):
    integrator, _drivers = make_integrator(tmp_path, hosted_outcomes=("passed",))
    action = integrator.prepare(make_batch_request(accepted_candidates=make_three_standard_receipts()))

    observation = integrator.execute(action)

    assert [(member.ticket_key, member.work_run_key) for member in observation.members] == [
        ("issue:1", "work-run:1"), ("issue:2", "work-run:2"), ("issue:3", "work-run:3")
    ]
    assert all(member.evidence_digests for member in observation.members)


def test_v3_batch_integrator_has_no_import_or_call_path_to_legacy_integration_batch():
    source = Path("skills/orchestrator/scripts/gwo_v8/batch_integrator.py").read_text(encoding="utf-8")
    assert "integration_batch" not in source
    assert "reconcile_once" not in source
~~~

- [ ] **Step 2: Run RED.**

~~~powershell
py -3.13 -m pytest tests/test_v8_batch_integrator.py::test_duplicate_execute_does_not_repeat_publication_hosted_ci_or_target_mutation -q
~~~

Expected: FAIL because BatchIntegrator.execute does not yet persist a complete terminal observation and replay it before driver calls.

- [ ] **Step 3: Implement the readback-first action loop.**

Implement the three non-Protocol methods with these concrete bodies and keep
the phase transition helper private:

```python
import json
from dataclasses import asdict, replace


def prepare(self, request: BatchDeliveryRequest) -> BatchDeliveryAction:
    self._validate_request(request)
    self.formation_calls += 1
    target = self.git.read_target(request.target)
    members = form_batch_members(
        request.accepted_candidates,
        target,
        member_limit=self.configuration.member_limit_for(request.repository),
    )
    if not members:
        raise BatchIntegratorError("BATCH_EMPTY", "no eligible accepted Candidate")
    batch_id = digest_value(
        {
            "kind": "batch-id.v1",
            "campaign_key": request.campaign_key,
            "plan_revision_digest": request.plan_revision_digest,
            "target": target.target_facts_digest,
            "members": [member.digest for member in members],
            "local_suite": request.local_suite.definition_digest,
            "hosted_suites": [suite.definition_digest for suite in request.hosted_suites],
        }
    )
    batch_sha = self.git.compose_batch(batch_id, target, members)
    action = BatchDeliveryAction(
        stable_action_id=request.stable_action_id,
        request_digest=request.request_digest,
        batch_id=batch_id,
        batch_sha=batch_sha,
        member_ticket_keys=tuple(member.ticket_key for member in members),
    )
    self._requests[action.stable_action_id] = request
    existing = self.journal.read_action(action.stable_action_id)
    if existing is None:
        self.journal.create_action(
            action,
            action.request_digest,
            phase="prepared",
            reason="prepared",
            retry_count=0,
            fallback_generation=0,
            state_json=json.dumps(
                {
                    "request": {
                        "request_digest": request.request_digest,
                        "repository": request.repository,
                        "campaign_key": request.campaign_key,
                        "plan_revision_digest": request.plan_revision_digest,
                        "target": {
                            "repository": target.repository,
                            "target_branch": target.target_branch,
                            "target_head_sha": target.target_head_sha,
                            "target_tree_oid": target.target_tree_oid,
                            "target_facts_digest": target.target_facts_digest,
                        },
                        "local_suite": {
                            "suite_id": request.local_suite.suite_id,
                            "definition_digest": request.local_suite.definition_digest,
                            "command": list(request.local_suite.command),
                        },
                        "hosted_suites": [
                            {
                                "suite_id": suite.suite_id,
                                "hosted_name": suite.hosted_name,
                                "definition_digest": suite.definition_digest,
                            }
                            for suite in request.hosted_suites
                        ],
                        "writer_generation": request.writer_generation,
                        "activation_id": request.activation_id,
                    },
                    "members": [member.canonical() for member in members],
                },
                sort_keys=True,
                separators=(",", ":"),
            ),
        )
    else:
        self._validate_action_record(existing, action)
    return action


def readback(self, action: BatchDeliveryAction) -> BatchDeliveryObservation | None:
    record = self.journal.read_action(action.stable_action_id)
    if record is None:
        return None
    self._validate_action_record(record, action)
    if record.phase not in {"complete", "decision", "blocked"}:
        return None
    return self._observation_from_record(record)


def execute(self, action: BatchDeliveryAction) -> BatchDeliveryObservation:
    existing = self.readback(action)
    if existing is not None:
        return existing
    record = self.journal.read_action(action.stable_action_id)
    if record is None:
        raise BatchIntegratorError("BATCH_ACTION_MISSING", "prepare must persist the action first")
    request = self._requests.get(action.stable_action_id) or self._request_for_action(record)
    while True:
        try:
            next_record = self._advance_one_delivery_step(record, action, request)
        except (DeliveryIdentityMismatch, DeliveryAttributionAmbiguous):
            self._preserve_identity_evidence(
                action.stable_action_id, request.accepted_candidates
            )
            raise
        committed = self.journal.compare_and_swap_action(
            action.stable_action_id,
            expected_version=record.version,
            expected_phase=record.phase,
            next_record=next_record,
        )
        if committed.phase in {"wait", "decision", "complete", "blocked"}:
            return self._observation_from_record(committed)
        record = committed


def _validate_action_record(
    self, record: BatchJournalRecord, action: BatchDeliveryAction
) -> None:
    if record.stable_action_id != action.stable_action_id:
        raise DeliveryIdentityMismatch("journal action ID differs from requested action")
    if (
        record.request_digest != action.request_digest
        or record.batch_id != action.batch_id
        or record.batch_sha != action.batch_sha
    ):
        raise DeliveryIdentityMismatch("journal action or Batch identity changed")
    state = json.loads(record.state_json or "{}")
    if tuple(item["ticket_key"] for item in state.get("members", ())) != action.member_ticket_keys:
        raise DeliveryIdentityMismatch("journal member identity changed")


def _receipt_from_canonical(body: dict[str, object]) -> AcceptedCandidateReceipt:
    values = dict(body)
    values["interaction_keys"] = tuple(
        InteractionKey(
            namespace=item["namespace"],
            value=item["value"],
            classification=InteractionClassification(item["classification"]),
        )
        for item in values["interaction_keys"]
    )
    values["protected_surfaces"] = tuple(values["protected_surfaces"])
    values["evidence_digests"] = tuple(values["evidence_digests"])
    return AcceptedCandidateReceipt(**values)


def _request_for_action(
    self, record: BatchJournalRecord
) -> BatchDeliveryRequest:
    state = json.loads(record.state_json or "{}")
    request_state = state["request"]
    target = BatchTarget(**request_state["target"])
    local_suite = LocalSuiteDefinition(
        suite_id=request_state["local_suite"]["suite_id"],
        definition_digest=request_state["local_suite"]["definition_digest"],
        command=tuple(request_state["local_suite"]["command"]),
    )
    hosted_suites = tuple(
        HostedSuiteDefinition(**suite) for suite in request_state["hosted_suites"]
    )
    candidates = tuple(_receipt_from_canonical(item) for item in state["members"])
    return BatchDeliveryRequest(
        stable_action_id=record.stable_action_id,
        repository=request_state["repository"],
        campaign_key=request_state["campaign_key"],
        plan_revision_digest=request_state["plan_revision_digest"],
        target=target,
        accepted_candidates=candidates,
        local_suite=local_suite,
        hosted_suites=hosted_suites,
        writer_generation=request_state["writer_generation"],
        activation_id=request_state["activation_id"],
    )


def _observation_from_record(
    self, record: BatchJournalRecord
) -> BatchDeliveryObservation:
    state = json.loads(record.state_json or "{}")
    phase = {
        "prepared": "running",
        "composed": "running",
        "local_checked": "running",
        "published": "running",
        "hosted": "running",
        "integrating": "running",
        "wait": "wait",
        "decision": "decision",
        "complete": "complete",
        "blocked": "blocked",
    }[record.phase]
    members = tuple(
        MemberDeliveryObservation(
            ticket_key=item["ticket_key"],
            work_run_key=item["work_run_key"],
            candidate_sha=item["candidate_sha"],
            status=item.get("status", "preserved"),
            evidence_digests=tuple(item["evidence_digests"]),
            resume_reason=item.get("resume_reason"),
        )
        for item in state.get("members", ())
    )
    delivery_proofs = (
        tuple(
            BatchDeliveryProof(
                **{
                    **item,
                    "member_ticket_keys": tuple(item["member_ticket_keys"]),
                }
            )
            for item in state.get("delivery_proofs", ())
        )
        if phase == "complete"
        else ()
    )
    body = {
        "stable_action_id": record.stable_action_id,
        "batch_id": record.batch_id,
        "batch_sha": record.batch_sha,
        "phase": phase,
        "reason": record.reason,
        "retry_count": record.retry_count,
        "fallback_generation": record.fallback_generation,
        "members": [asdict(member) for member in members],
        "delivery_proofs": [proof.canonical() for proof in delivery_proofs],
    }
    return BatchDeliveryObservation(
        stable_action_id=record.stable_action_id,
        batch_id=record.batch_id,
        batch_sha=record.batch_sha,
        phase=phase,
        reason=record.reason,
        receipt_digest=digest_value(
            {"kind": "batch-observation.v1", **body}
        ),
        retry_count=record.retry_count,
        fallback_generation=record.fallback_generation,
        members=members,
        delivery_proofs=delivery_proofs,
    )
```

`_advance_one_delivery_step` is the concrete phase table: `prepared` runs the
exact local suite and writes `local_checked`; `local_checked` reads an existing
publication and validates it before publishing, then writes `published`;
`published` reads the PR and exact hosted head and writes `hosted`; `hosted`
acquires the Integration Lease, integrates serially, validates target ancestry,
and writes `complete`. Every branch constructs a `BatchJournalRecord` with
`version + 1`, and the returned `_observation_from_record` JSON-decodes the
member Candidate/Evidence snapshot and computes
`digest_value({"kind": "batch-observation.v1", **observation_body})` for
`receipt_digest`. A direct complete record reconstructs its one
`BatchDeliveryProof` from the persisted local, publication, PR, hosted-result,
Integration-Lease, and target-readback receipts. A fallback parent reconstructs
the ordered proof tuple copied from its completed child observations; no final
delivery fact is copied from the request or inferred from `batch_sha`.
Infrastructure and fallback branches are added in Tasks 8
and 9 by replacing only the named transition, not by bypassing this
readback-first/CAS body. A terminal action is complete, decision, or blocked;
terminal readback must not call any external driver. The initial `state_json`
also stores the immutable request target, suite definitions, writer generation,
Activation ID, and canonical accepted-receipt bodies. After restart,
`_request_for_action` reconstructs those exact typed values from the journal
before advancing; it never asks CandidateGate, Runtime, or a provider to
recreate the request.

Remove the old assembler from gwo_v8.__init__ exports and change tests/test_orchestrator_v8_integration_batch.py to import GitIntegrationBatchAssembler and IntegrationBatchMember directly from gwo_v8.integration_batch. Add the predecessor-only docstring to integration_batch.py; leave its two existing tests green because Kernel remains an explicitly quarantined compatibility path until #118. No new file may import that module.

- [ ] **Step 4: Run GREEN and the V3 boundary gate.**

~~~powershell
py -3.13 -m pytest tests/test_v8_batch_integrator.py tests/test_orchestrator_v8_integration_batch.py -q
py -3.13 -m pytest tests/test_orchestrator_v8_phase3.py tests/test_orchestrator_v8_phase4a.py -q
~~~

Expected: new V3 action tests, predecessor assembler tests, and existing transitional Kernel tests all PASS. The package export list no longer presents GitIntegrationBatchAssembler as a V3 public boundary.
Run `py -3.13 scripts/sync_orchestrator.py` and then
`py -3.13 scripts/sync_orchestrator.py --check` before accepting this package
gate.

- [ ] **Step 5: Commit normal-path integration.**

~~~powershell
py -3.13 scripts/sync_orchestrator.py
py -3.13 scripts/sync_orchestrator.py --check
git add skills/orchestrator/scripts/gwo_v8/batch_integrator.py skills/orchestrator/scripts/gwo_v8/__init__.py skills/orchestrator/scripts/gwo_v8/integration_batch.py skills/orchestrator/.skill-package.json tests/v8_batch_test_support.py tests/test_v8_batch_integrator.py tests/test_orchestrator_v8_integration_batch.py
git commit -m "feat: install the idempotent V3 BatchIntegrator action loop"
~~~

### Task 8: Implement #117 unchanged-SHA infrastructure recovery and durable hosted-result adoption

**Files:**
- Modify: skills/orchestrator/scripts/gwo_v8/batch_integrator.py
- Modify: skills/orchestrator/scripts/gwo_v8/_batch_integrator_store.py
- Modify: tests/v8_batch_test_support.py
- Modify: tests/test_v8_batch_recovery.py

**Interfaces:**
- Consumes: the stable action, exact Batch SHA, hosted suite identity, provider check ID, and journal CAS from Tasks 3 and 7.
- Produces: HostedResultReceipt, bounded unchanged-SHA retry, and restart adoption without provider reread.

- [ ] **Step 1: Write the failing recovery tests.**

Add these exact tests:

~~~python
def test_infrastructure_failure_retries_same_batch_sha_at_most_twice(tmp_path):
    integrator, drivers = make_integrator(tmp_path, hosted_outcomes=("infrastructure_failure", "infrastructure_failure", "infrastructure_failure"))
    action = integrator.prepare(make_batch_request(accepted_candidates=make_three_standard_receipts()))

    first = integrator.execute(action)
    second = integrator.execute(action)
    third = integrator.execute(action)

    assert first.phase == second.phase == "wait"
    assert third.phase == "blocked"
    assert drivers.hosted.retry_shas == [action.batch_sha, action.batch_sha]
    assert drivers.hosted.hosted_read_shas == [action.batch_sha, action.batch_sha, action.batch_sha]
    assert all(sha == action.batch_sha for sha in drivers.hosted.retry_shas)


def test_terminal_hosted_receipt_is_adopted_after_restart_without_provider_reread(tmp_path):
    integrator, drivers = make_integrator(tmp_path, hosted_outcomes=("passed",), crash_after="hosted_receipt_persisted")
    action = integrator.prepare(make_batch_request(accepted_candidates=make_three_standard_receipts()))

    with pytest.raises(CrashInjected, match="hosted_receipt_persisted"):
        integrator.execute(action)

    assert drivers.hosted.hosted_read_shas == [action.batch_sha]
    assert drivers.hosted.integrated_shas == []
    assert integrator.journal.read_hosted_result(
        action.stable_action_id, action.batch_sha, "hosted", "check:1"
    ) is not None

    restarted, restarted_drivers = make_integrator(tmp_path)
    observation = restarted.execute(action)

    assert observation.phase == "complete"
    assert restarted_drivers.hosted.hosted_read_calls == 0
    assert restarted_drivers.hosted.integrated_shas == [action.batch_sha]
    assert drivers.hosted.hosted_read_shas == [action.batch_sha]


def test_restart_rejects_persisted_hosted_receipt_with_wrong_action_or_observation_digest(tmp_path):
    integrator, _drivers = make_integrator(tmp_path)
    action = integrator.prepare(make_batch_request(accepted_candidates=make_three_standard_receipts()))
    receipt = make_hosted_result_receipt(stable_action_id=action.stable_action_id, batch_sha=action.batch_sha)
    integrator.journal.persist_hosted_result(receipt)
    store_path = tmp_path / "v8.sqlite3"
    with sqlite3.connect(store_path) as connection:
        connection.execute(
            """
            UPDATE v8_batch_hosted_receipts
            SET observation_digest = ?
            WHERE stable_action_id = ?
              AND batch_sha = ?
              AND suite_id = ?
              AND provider_check_id = ?
            """,
            (
                "f" * 64,
                receipt.stable_action_id,
                receipt.batch_sha,
                receipt.suite_id,
                receipt.provider_check_id,
            ),
        )
        connection.commit()

    restarted, _restarted_drivers = make_integrator(tmp_path)
    with pytest.raises(DeliveryIdentityMismatch):
        restarted.execute(action)
~~~

The `sqlite3.connect` update above is test-only deep-module corruption of the
existing `v8_batch_hosted_receipts` row. Do not add a production journal
mutation hook or any other test-only journal method.

- [ ] **Step 2: Run RED.**

~~~powershell
py -3.13 -m pytest tests/test_v8_batch_recovery.py::test_infrastructure_failure_retries_same_batch_sha_at_most_twice -q
~~~

Expected: FAIL because hosted failures are not classified into an unchanged-SHA retry episode.

- [ ] **Step 3: Implement bounded retry and receipt adoption.**

Before every infrastructure retry, execute must call readback(action), validate the action, Batch SHA, hosted suite, provider check ID, and current target facts, then call HostedBatchDriver.retry_hosted(repository, batch_sha, provider_check_id) with the unchanged SHA. Persist retry_count + 1 by CAS before returning wait; when retry_count == 2, return blocked with reason InfrastructureRetryLimitExceeded and never retry again.

Use the exact `HostedResultReceipt` value and `body()`/`create()` implementation
introduced in Task 3; Task 8 consumes that store-owned type and does not
redeclare it. Persist it immediately after validating the terminal provider
observation and before target integration.

BatchDeliveryJournal.persist_hosted_result must validate the receipt digest and exact key. BatchIntegrator.readback must return a valid terminal receipt directly from SQLite; it must not call the provider for a terminal hosted result. A changed receipt is an integrity failure, not an infrastructure or code failure.

Keep `HostedResultReceipt` in `_batch_integrator_store.py` and add these journal
methods there. Infrastructure observations remain retry facts in the action
CAS but are not inserted as adoptable terminal rows; only `passed` and
`code_failure` are persisted and can suppress a provider reread after restart:

```python
from .batch_integrator import DeliveryAttributionAmbiguous, DeliveryIdentityMismatch


def persist_hosted_result(
    self, receipt: HostedResultReceipt
) -> HostedResultReceipt:
    self._validate_hosted_receipt_digest(receipt)
    existing = self.read_hosted_result(
        receipt.stable_action_id,
        receipt.batch_sha,
        receipt.suite_id,
        receipt.provider_check_id,
    )
    if existing is not None:
        if existing != receipt:
            raise DeliveryIdentityMismatch("hosted receipt identity changed")
        return existing
    with self._connect() as connection:
        connection.execute(
            """
            INSERT INTO v8_batch_hosted_receipts
                (stable_action_id, batch_sha, suite_id, provider_check_id,
                 outcome, observation_digest, source_ref, receipt_digest)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                receipt.stable_action_id,
                receipt.batch_sha,
                receipt.suite_id,
                receipt.provider_check_id,
                receipt.outcome,
                receipt.observation_digest,
                receipt.source_ref,
                receipt.receipt_digest,
            ),
        )
        connection.commit()
    self.crash_hook("hosted_receipt_persisted")
    return receipt


def read_terminal_hosted_result(
    self,
    stable_action_id: str,
    batch_sha: str,
    suite_id: str,
) -> HostedResultReceipt | None:
    with self._connect() as connection:
        rows = connection.execute(
            """
            SELECT *
              FROM v8_batch_hosted_receipts
             WHERE stable_action_id=?
               AND batch_sha=?
               AND suite_id=?
               AND outcome IN ('passed', 'code_failure')
             ORDER BY provider_check_id
            """,
            (stable_action_id, batch_sha, suite_id),
        ).fetchall()
    if len(rows) > 1:
        raise DeliveryAttributionAmbiguous(
            "multiple terminal hosted receipts matched one action, Batch SHA, and suite"
        )
    if not rows:
        return None
    receipt = HostedResultReceipt(**dict(rows[0]))
    self._validate_hosted_receipt_digest(receipt)
    return receipt
```

Replace Task 7's inline hosted-result branch with these concrete
`BatchIntegrator` methods. The existing `execute` loop performs the single
`compare_and_swap_action` for the returned record before exposing the `wait`,
`hosted`, `decision`, or `blocked` observation:

```python
def _validate_hosted_receipt_identity(
    receipt: HostedResultReceipt,
    action: BatchDeliveryAction,
    suite: HostedSuiteDefinition,
) -> None:
    if (
        receipt.stable_action_id != action.stable_action_id
        or receipt.batch_sha != action.batch_sha
        or receipt.suite_id != suite.suite_id
        or not receipt.provider_check_id
    ):
        raise DeliveryIdentityMismatch(
            "hosted receipt did not match action, Batch SHA, suite, and provider check"
        )


def _read_or_persist_hosted_result(
    self,
    action: BatchDeliveryAction,
    request: BatchDeliveryRequest,
    suite: HostedSuiteDefinition,
) -> HostedResultReceipt | None:
    from ._batch_integrator_store import HostedResultReceipt

    persisted = self.journal.read_terminal_hosted_result(
        action.stable_action_id,
        action.batch_sha,
        suite.suite_id,
    )
    if persisted is not None:
        _validate_hosted_receipt_identity(persisted, action, suite)
        return persisted

    observed = self.hosted.read_hosted_result(
        request.repository,
        action.batch_sha,
        suite,
    )
    if observed.outcome == "pending":
        return None
    if (
        observed.repository != request.repository
        or observed.batch_sha != action.batch_sha
        or observed.suite_id != suite.suite_id
        or not observed.provider_check_id
    ):
        raise DeliveryIdentityMismatch(
            "provider observation did not match repository, Batch SHA, suite, and check"
        )
    receipt = HostedResultReceipt.create(
        stable_action_id=action.stable_action_id,
        batch_sha=action.batch_sha,
        suite_id=observed.suite_id,
        provider_check_id=observed.provider_check_id,
        outcome=observed.outcome,
        observation_digest=observed.observation_digest,
        source_ref=observed.source_ref,
    )
    if receipt.outcome == "infrastructure_failure":
        return receipt
    return self.journal.persist_hosted_result(receipt)


def _advance_hosted_result(
    self,
    record: BatchJournalRecord,
    action: BatchDeliveryAction,
    request: BatchDeliveryRequest,
    state: dict[str, object],
) -> BatchJournalRecord:
    suite = request.hosted_suites[0]
    receipt = self._read_or_persist_hosted_result(action, request, suite)
    if receipt is None:
        return replace(
            record,
            phase="wait",
            reason="HostedResultPending",
            state_json=json.dumps(state, sort_keys=True, separators=(",", ":")),
            version=record.version + 1,
        )

    _validate_hosted_receipt_identity(receipt, action, suite)
    state["hosted_receipt"] = {
        **receipt.body(),
        "receipt_digest": receipt.receipt_digest,
    }
    if receipt.outcome == "infrastructure_failure":
        self.readback(action)
        current = self.journal.read_action(action.stable_action_id)
        if current is None:
            raise BatchIntegratorError("BATCH_ACTION_MISSING", "retry action is not durable")
        self._validate_action_record(current, action)
        target = self.git.read_target(request.target)
        if target != request.target:
            raise DeliveryIdentityMismatch(
                "target facts changed before unchanged-SHA infrastructure retry"
            )
        if record.retry_count >= self.configuration.infrastructure_retry_limit:
            return replace(
                record,
                phase="blocked",
                reason="InfrastructureRetryLimitExceeded",
                state_json=json.dumps(state, sort_keys=True, separators=(",", ":")),
                version=record.version + 1,
            )
        self.hosted.retry_hosted(
            request.repository,
            action.batch_sha,
            receipt.provider_check_id,
        )
        return replace(
            record,
            phase="wait",
            reason="InfrastructureRetryScheduled",
            retry_count=record.retry_count + 1,
            state_json=json.dumps(state, sort_keys=True, separators=(",", ":")),
            version=record.version + 1,
        )

    if receipt.outcome == "code_failure":
        return self._classify_failure(
            record,
            action,
            request,
            receipt.outcome,
            state,
        )
    return replace(
        record,
        phase="hosted",
        reason="hosted_receipt_verified",
        state_json=json.dumps(state, sort_keys=True, separators=(",", ":")),
        version=record.version + 1,
    )
```

- [ ] **Step 4: Run GREEN and restart verification.**

~~~powershell
py -3.13 -m pytest tests/test_v8_batch_recovery.py -k "infrastructure or terminal_hosted or restart_rejects" -q
~~~

Expected: all selected tests PASS, with exactly two retry calls, one hosted read before durable receipt, and zero hosted reads after restart.

- [ ] **Step 5: Commit bounded infrastructure recovery.**

~~~powershell
py -3.13 scripts/sync_orchestrator.py
py -3.13 scripts/sync_orchestrator.py --check
git add skills/orchestrator/scripts/gwo_v8/batch_integrator.py skills/orchestrator/scripts/gwo_v8/_batch_integrator_store.py skills/orchestrator/.skill-package.json tests/v8_batch_test_support.py tests/test_v8_batch_recovery.py
git commit -m "feat: adopt terminal hosted receipts and bound infrastructure retry"
~~~

### Task 9: Implement one deterministic Singleton fallback and Strict Singleton recovery

**Files:**
- Modify: skills/orchestrator/scripts/gwo_v8/batch_integrator.py
- Modify: skills/orchestrator/scripts/gwo_v8/_batch_integrator_store.py
- Modify: tests/v8_batch_test_support.py
- Modify: tests/test_v8_batch_recovery.py

**Interfaces:**
- Consumes: #115 review_finding_ledger_digest in each accepted receipt, exact per-member Candidate/Evidence facts, and the multi-member delivery failure classification.
- Produces: one fallback_generation == 1 Singleton queue, an ordered one-proof-per-member `delivery_proofs` partition on successful parent completion, preserved unaffected member Evidence, and resume_required output for only a failing Singleton.

- [ ] **Step 1: Write the failing fallback and integrity tests.**

Add these exact tests:

~~~python
from dataclasses import replace


def test_multi_member_code_failure_dissolves_once_into_singletons(tmp_path):
    integrator, drivers = make_integrator(tmp_path, hosted_outcomes=("code_failure", "passed", "passed", "passed"))
    action = integrator.prepare(make_batch_request(accepted_candidates=make_three_standard_receipts()))

    first = integrator.execute(action)
    second = integrator.execute(action)
    third = integrator.execute(action)
    fourth = integrator.execute(action)

    assert first.fallback_generation == 1
    assert [item for item in drivers.created_batch_member_sets if len(item) == 1] == [
        ("issue:1",), ("issue:2",), ("issue:3",)
    ]
    assert fourth.phase == "complete"
    assert tuple(proof.member_ticket_keys for proof in fourth.delivery_proofs) == (
        ("issue:1",),
        ("issue:2",),
        ("issue:3",),
    )
    assert all(
        proof.delivery_stable_action_id != action.stable_action_id
        for proof in fourth.delivery_proofs
    )
    assert [proof.batch_sha for proof in fourth.delivery_proofs] == (
        drivers.hosted.integrated_shas
    )
    assert drivers.formation_calls == 1
    assert drivers.composition_calls == 4

    with pytest.raises(DeliveryIdentityMismatch):
        replace(fourth, delivery_proofs=fourth.delivery_proofs[:-1])


def test_singleton_fallback_reuses_member_candidate_and_evidence_without_review(tmp_path):
    integrator, drivers = make_integrator(tmp_path, hosted_outcomes=("code_failure", "passed", "passed", "passed"))
    candidates = make_three_standard_receipts()
    action = integrator.prepare(make_batch_request(accepted_candidates=candidates))

    for _ in range(4):
        integrator.execute(action)

    assert drivers.candidategate_calls == 0
    assert drivers.review_calls == 0
    assert drivers.singleton_member_candidate_shas == [item.candidate_sha for item in candidates]
    assert drivers.singleton_member_evidence_digests == [item.evidence_digests for item in candidates]


def test_only_failing_singleton_requests_worker_resume_with_review_ledger_context(tmp_path):
    integrator, drivers = make_integrator(tmp_path, hosted_outcomes=("code_failure", "code_failure", "passed", "passed"))
    candidates = make_three_standard_receipts()
    action = integrator.prepare(make_batch_request(accepted_candidates=candidates))

    observations = [integrator.execute(action) for _ in range(4)]
    resume_members = [member for observation in observations for member in observation.members if member.status == "resume_required"]

    assert [(member.ticket_key, member.work_run_key) for member in resume_members] == [("issue:1", "work-run:1")]
    assert drivers.resume_directives == [("work-run:1", candidates[0].review_finding_ledger_digest)]


def test_singleton_failure_never_recursively_splits_or_regroups(tmp_path):
    integrator, drivers = make_integrator(tmp_path, hosted_outcomes=("code_failure", "code_failure", "code_failure", "code_failure"))
    action = integrator.prepare(make_batch_request(accepted_candidates=make_three_standard_receipts()))

    for _ in range(8):
        integrator.execute(action)

    assert drivers.created_batch_member_sets.count(("issue:1",)) == 1
    assert all(len(member_set) <= 1 for member_set in drivers.created_batch_member_sets[1:])
    assert drivers.formation_calls == 1
    assert drivers.composition_calls == 4


def test_strict_candidate_is_singleton_on_initial_delivery_and_recovery(tmp_path):
    strict = make_accepted_candidate_receipt(ticket_key="issue:strict", assurance="strict")
    integrator, drivers = make_integrator(tmp_path, hosted_outcomes=("code_failure", "passed"))
    action = integrator.prepare(make_batch_request(accepted_candidates=(strict,)))

    failed = integrator.execute(action)
    passed = integrator.execute(action)

    assert failed.fallback_generation == 0
    assert passed.phase == "complete"
    assert drivers.created_batch_member_sets == [("issue:strict",)]


@pytest.mark.parametrize(
    ("failure_mode", "expected_error"),
    [
        ("wrong_batch_sha", DeliveryIdentityMismatch),
        ("ambiguous_provider", DeliveryAttributionAmbiguous),
    ],
)
def test_identity_mismatch_and_ambiguous_attribution_preserve_evidence_and_forbid_fallback_or_resume(
    tmp_path, failure_mode, expected_error
):
    integrator, drivers = make_integrator(
        tmp_path / failure_mode, delivery_failure=failure_mode
    )
    action = integrator.prepare(
        make_batch_request(accepted_candidates=make_three_standard_receipts())
    )

    with pytest.raises(expected_error):
        integrator.execute(action)

    assert drivers.created_batch_member_sets == [("issue:1", "issue:2", "issue:3")]
    assert drivers.resume_directives == []
    assert drivers.preserved_evidence_digests == [
        item.evidence_digests for item in make_three_standard_receipts()
    ]
~~~

- [ ] **Step 2: Run RED.**

~~~powershell
py -3.13 -m pytest tests/test_v8_batch_recovery.py::test_multi_member_code_failure_dissolves_once_into_singletons -q
~~~

Expected: FAIL because multi-member code failures do not yet create deterministic Singleton child actions.

- [ ] **Step 3: Implement the one-fallback state machine.**

For a multi-member composition, exact-local, or code-class hosted failure, persist the parent action's complete member/Evidence snapshot, set fallback_generation=1, and derive child action IDs and Batch IDs from (parent stable action, parent batch ID, member ticket key, member receipt digest). Create one Singleton per original member in accepted_sequence order. Each child must compose its own exact Batch SHA on the exact current target, run its own local suite, publish its own branch/PR, obtain its own hosted receipt, acquire the same repository-global Integration Lease serially, and prove its own target readback. Never reuse the parent's publication, hosted, PR, target, or Batch Evidence.

The persistence and recording path is concrete and happens before any child
effect:

```python
import json
from dataclasses import replace


def make_singleton_action(
    parent_action: BatchDeliveryAction,
    member: AcceptedCandidateReceipt,
    child_batch_id: str,
    child_batch_sha: str,
) -> BatchDeliveryAction:
    child_action_id = digest_value(
        {
            "kind": "singleton-action.v1",
            "parent_action_id": parent_action.stable_action_id,
            "parent_batch_id": parent_action.batch_id,
            "ticket_key": member.ticket_key,
            "candidate_receipt_digest": member.digest,
        }
    )
    child_request_digest = digest_value(
        {
            "kind": "singleton-request.v1",
            "parent_request_digest": parent_action.request_digest,
            "ticket_key": member.ticket_key,
            "candidate_receipt_digest": member.digest,
        }
    )
    return BatchDeliveryAction(
        stable_action_id=child_action_id,
        request_digest=child_request_digest,
        batch_id=child_batch_id,
        batch_sha=child_batch_sha,
        member_ticket_keys=(member.ticket_key,),
    )


snapshot = [
    {
        "ticket_key": member.ticket_key,
        "work_run_key": member.work_run_key,
        "candidate_sha": member.candidate_sha,
        "candidate_receipt_digest": member.candidate_receipt_digest,
        "evidence_digests": list(member.evidence_digests),
        "review_finding_ledger_digest": member.review_finding_ledger_digest,
        "status": "preserved",
    }
    for member in parent_members
]
next_state = json.dumps(
    {
        "fallback_generation": 1,
        "members": snapshot,
        "delivery_proofs": [],
    },
    sort_keys=True,
    separators=(",", ":"),
)
journal.compare_and_swap_action(
    parent_action.stable_action_id,
    expected_version=parent_record.version,
    expected_phase=parent_record.phase,
    next_record=replace(
        parent_record,
        phase="wait",
        reason="SingletonFallbackQueued",
        fallback_generation=1,
        state_json=next_state,
        version=parent_record.version + 1,
    ),
)
for member in parent_members:
    journal.persist_member_evidence(
        parent_action.stable_action_id,
        member.ticket_key,
        member.candidate_sha,
        member.evidence_digests,
        member.review_finding_ledger_digest,
    )
    child_batch_id = digest_value(
        {
            "kind": "singleton-batch.v1",
            "parent_batch_id": parent_action.batch_id,
            "ticket_key": member.ticket_key,
            "candidate_receipt_digest": member.digest,
        }
    )
    child_batch_sha = git.compose_batch(
        child_batch_id, current_target, (member,)
    )
    child_action = make_singleton_action(
        parent_action,
        member,
        child_batch_id,
        child_batch_sha,
    )
    singleton_queue.append((child_action, member))
```

`persist_member_evidence` writes the immutable Candidate SHA, Evidence digest
tuple, and Review Finding ledger digest into the parent's canonical
`state_json`; it never calls CandidateGate. The recording Git double writes
`singleton_member_candidate_shas` and `singleton_member_evidence_digests` from
the exact receipt passed to each singleton `compose_batch` call, and writes
each parent member's `evidence_digests` to `preserved_evidence_digests` before
the failure is classified. Formation and composition counters remain separate:
the parent `prepare` increments `formation_calls` once, while the parent plus
three child `compose_batch` calls increment `composition_calls` to four. Child
creation does not call `form_batch_members`, regroup, or increment formation.

The journal method used above has this exact compare-and-swap body:

```python
def persist_member_evidence(
    self,
    stable_action_id: str,
    ticket_key: str,
    candidate_sha: str,
    evidence_digests: tuple[str, ...],
    review_finding_ledger_digest: str,
) -> BatchJournalRecord:
    record = self.read_action(stable_action_id)
    state = json.loads(record.state_json or "{}")
    state.setdefault("member_evidence", {})[ticket_key] = {
        "candidate_sha": candidate_sha,
        "evidence_digests": list(evidence_digests),
        "review_finding_ledger_digest": review_finding_ledger_digest,
    }
    next_record = replace(
        record,
        state_json=json.dumps(state, sort_keys=True, separators=(",", ":")),
        version=record.version + 1,
    )
    return self.compare_and_swap_action(
        stable_action_id,
        expected_version=record.version,
        expected_phase=record.phase,
        next_record=next_record,
    )
```

The named failure and queue helpers are also concrete methods; they are not
left as protocol stubs:

```python
def _classify_failure(
    self,
    record: BatchJournalRecord,
    action: BatchDeliveryAction,
    request: BatchDeliveryRequest,
    outcome: str,
    state: dict[str, object],
) -> BatchJournalRecord:
    if len(request.accepted_candidates) > 1 and record.fallback_generation == 0:
        return self._queue_singletons(record, action, request, state)
    if len(request.accepted_candidates) != 1:
        return replace(
            record,
            phase="blocked",
            reason="BatchFailureAttributionRequired",
            state_json=json.dumps(state, sort_keys=True, separators=(",", ":")),
            version=record.version + 1,
        )
    member = request.accepted_candidates[0]
    state["members"][0]["status"] = "resume_required"
    state["members"][0]["resume_reason"] = outcome
    directive = (member.work_run_key, member.review_finding_ledger_digest)
    if hasattr(self.git, "resume_directives"):
        self.git.resume_directives.append(directive)
    return replace(
        record,
        phase="decision",
        reason="WorkerResumeRequired",
        state_json=json.dumps(state, sort_keys=True, separators=(",", ":")),
        version=record.version + 1,
    )


def _preserve_identity_evidence(
    self,
    stable_action_id: str,
    members: tuple[AcceptedCandidateReceipt, ...],
) -> None:
    for member in members:
        self.journal.persist_member_evidence(
            stable_action_id,
            member.ticket_key,
            member.candidate_sha,
            member.evidence_digests,
            member.review_finding_ledger_digest,
        )


def _state_json_for_request(
    self,
    request: BatchDeliveryRequest,
    members: tuple[AcceptedCandidateReceipt, ...],
) -> str:
    return json.dumps(
        {
            "request": {
                "request_digest": request.request_digest,
                "repository": request.repository,
                "campaign_key": request.campaign_key,
                "plan_revision_digest": request.plan_revision_digest,
                "target": asdict(request.target),
                "local_suite": asdict(request.local_suite),
                "hosted_suites": [asdict(suite) for suite in request.hosted_suites],
                "writer_generation": request.writer_generation,
                "activation_id": request.activation_id,
            },
            "members": [member.canonical() for member in members],
        },
        sort_keys=True,
        separators=(",", ":"),
    )


def _queue_singletons(
    self,
    record: BatchJournalRecord,
    parent_action: BatchDeliveryAction,
    request: BatchDeliveryRequest,
    state: dict[str, object],
) -> BatchJournalRecord:
    parent_members = tuple(request.accepted_candidates)
    state["members"] = [
        {
            "ticket_key": member.ticket_key,
            "work_run_key": member.work_run_key,
            "candidate_sha": member.candidate_sha,
            "candidate_receipt_digest": member.digest,
            "evidence_digests": list(member.evidence_digests),
            "review_finding_ledger_digest": member.review_finding_ledger_digest,
            "status": "preserved",
        }
        for member in parent_members
    ]
    state["singleton_queue"] = []
    state["next_singleton_index"] = 0
    state["delivery_proofs"] = []
    queued = self.journal.compare_and_swap_action(
        parent_action.stable_action_id,
        expected_version=record.version,
        expected_phase=record.phase,
        next_record=replace(
            record,
            phase="wait",
            reason="SingletonFallbackQueued",
            fallback_generation=1,
            state_json=json.dumps(state, sort_keys=True, separators=(",", ":")),
            version=record.version + 1,
        ),
    )
    queued_entries: list[dict[str, object]] = []
    for member in parent_members:
        self.journal.persist_member_evidence(
            parent_action.stable_action_id,
            member.ticket_key,
            member.candidate_sha,
            member.evidence_digests,
            member.review_finding_ledger_digest,
        )
        child_batch_id = digest_value(
            {
                "kind": "singleton-batch.v1",
                "parent_batch_id": parent_action.batch_id,
                "ticket_key": member.ticket_key,
                "candidate_receipt_digest": member.digest,
            }
        )
        current_target = self.git.read_target(request.target)
        child_batch_sha = self.git.compose_batch(
            child_batch_id, current_target, (member,)
        )
        child_action = make_singleton_action(
            parent_action, member, child_batch_id, child_batch_sha
        )
        child_request = replace(
            request,
            stable_action_id=child_action.stable_action_id,
            target=current_target,
            accepted_candidates=(member,),
        )
        self._requests[child_action.stable_action_id] = child_request
        self.journal.create_action(
            child_action,
            child_request.request_digest,
            state_json=self._state_json_for_request(child_request, (member,)),
        )
        queued_entries.append(
            {
                "action": asdict(child_action),
                "member": member.canonical(),
            }
        )
    current = self.journal.read_action(parent_action.stable_action_id)
    state = json.loads(current.state_json or "{}")
    state["singleton_queue"] = queued_entries
    state["next_singleton_index"] = 0
    state["delivery_proofs"] = []
    return self.journal.compare_and_swap_action(
        parent_action.stable_action_id,
        expected_version=current.version,
        expected_phase=current.phase,
        next_record=replace(
            current,
            state_json=json.dumps(state, sort_keys=True, separators=(",", ":")),
            version=current.version + 1,
        ),
    )


def _execute_child(
    self,
    child_action: BatchDeliveryAction,
    child_request: BatchDeliveryRequest,
) -> BatchDeliveryObservation:
    record = self.journal.read_action(child_action.stable_action_id)
    while record.phase not in {"complete", "decision", "blocked"}:
        next_record = self._advance_one_delivery_step(
            record, child_action, child_request
        )
        record = self.journal.compare_and_swap_action(
            child_action.stable_action_id,
            expected_version=record.version,
            expected_phase=record.phase,
            next_record=next_record,
        )
        if record.phase == "wait":
            break
    return self._observation_from_record(record)


def _advance_singleton_queue(
    self,
    record: BatchJournalRecord,
    parent_action: BatchDeliveryAction,
    request: BatchDeliveryRequest,
    state: dict[str, object],
) -> BatchJournalRecord:
    queue = state["singleton_queue"]
    index = int(state.get("next_singleton_index", 0))
    if index >= len(queue):
        return replace(
            record,
            phase="complete",
            reason="SingletonFallbackComplete",
            state_json=json.dumps(state, sort_keys=True, separators=(",", ":")),
            version=record.version + 1,
        )
    child_action = BatchDeliveryAction(**queue[index]["action"])
    member = _receipt_from_canonical(queue[index]["member"])
    child_request = self._requests.get(child_action.stable_action_id)
    if child_request is None:
        child_request = replace(
            request,
            stable_action_id=child_action.stable_action_id,
            accepted_candidates=(member,),
        )
        self._requests[child_action.stable_action_id] = child_request
    child_observation = self._execute_child(child_action, child_request)
    if child_observation.phase == "complete":
        if (
            child_observation.stable_action_id != child_action.stable_action_id
            or child_observation.batch_id != child_action.batch_id
            or child_observation.batch_sha != child_action.batch_sha
            or len(child_observation.delivery_proofs) != 1
            or child_observation.delivery_proofs[0].member_ticket_keys
            != (member.ticket_key,)
        ):
            raise DeliveryIdentityMismatch(
                "completed Singleton observation changed child delivery identity"
            )
        state["members"][index]["status"] = "integrated"
        state.setdefault("delivery_proofs", []).append(
            child_observation.delivery_proofs[0].canonical()
        )
        state["next_singleton_index"] = index + 1
        phase = "complete" if index + 1 == len(queue) else "wait"
        reason = "SingletonFallbackComplete" if phase == "complete" else "SingletonCompleted"
        return replace(
            record,
            phase=phase,
            reason=reason,
            state_json=json.dumps(state, sort_keys=True, separators=(",", ":")),
            version=record.version + 1,
        )
    if child_observation.phase == "decision":
        state["members"][index]["status"] = "resume_required"
        state["members"][index]["resume_reason"] = child_observation.reason
        return replace(
            record,
            phase="decision",
            reason="WorkerResumeRequired",
            state_json=json.dumps(state, sort_keys=True, separators=(",", ":")),
            version=record.version + 1,
        )
    return replace(
        record,
        phase="wait",
        reason="SingletonWaiting",
        state_json=json.dumps(state, sort_keys=True, separators=(",", ":")),
        version=record.version + 1,
    )
```

Passing children mark only their member integrated, append that child's exact
`BatchDeliveryProof` to the parent's ordered `delivery_proofs`, and retain the
original Candidate receipt, Candidate Check Evidence, Review Evidence, and
Review Finding ledger. Parent completion is valid only when those Singleton
proofs partition all parent members in accepted order. A failing child returns
resume_required with:

~~~python
@dataclass(frozen=True)
class WorkerResumeDirective:
    work_run_key: str
    ticket_key: str
    candidate_sha: str
    evidence_digests: tuple[str, ...]
    review_finding_ledger_digest: str
    reason: str
~~~

The future ExecutionKernel composition reacquires a Worker Slot and resumes only this Work Run. BatchIntegrator does not invoke the Worker. If the resumed Worker changes code, the new Candidate reference returns to CandidateGate and receives a new Review Subject; unchanged Candidates never repeat Review. A Singleton has fallback_generation == 0 when it was selected initially and may not create another child action. An identity mismatch or attribution ambiguity stores all observations, returns its typed error, and leaves fallback_generation unchanged.

- [ ] **Step 4: Run GREEN and recovery convergence verification.**

~~~powershell
py -3.13 -m pytest tests/test_v8_batch_recovery.py -k "singleton or strict_candidate or identity_mismatch" -q
~~~

Expected: all selected tests PASS. Verify that one multi-member failure creates exactly three Singleton delivery boundaries, no child contains more than one member, no review or CandidateGate call occurs, and only the failing Singleton produces a resume directive.

- [ ] **Step 5: Commit #117 recovery.**

~~~powershell
py -3.13 scripts/sync_orchestrator.py
py -3.13 scripts/sync_orchestrator.py --check
git add skills/orchestrator/scripts/gwo_v8/batch_integrator.py skills/orchestrator/scripts/gwo_v8/_batch_integrator_store.py skills/orchestrator/.skill-package.json tests/v8_batch_test_support.py tests/test_v8_batch_recovery.py
git commit -m "feat: recover Batch failures with one Singleton fallback"
~~~

### Task 10: Complete Beta2 evidence and the full verification gate

**Files:**
- Create: tests/test_v8_batch_beta2.py
- Create: scripts/write_v8_batch_evidence.py
- Create: docs/e2e/gwo-v8-batch-integrator.md
- Modify: tests/v8_batch_test_support.py

**Interfaces:**
- Consumes: merged #115 CandidateGate receipts, merged #116/#117 BatchIntegrator, and the future production-composition test harness.
- Produces: exact Beta2 exit evidence for #116 and #117, including direct and successful-fallback delivery-proof partitions; it does not publish a release, activate V3, or close a GitHub Issue from this worktree.

- [ ] **Step 1: Write the failing Beta2 boundary test.**

Add these exact tests:

~~~python
from gwo_v8._canonical import digest_value


def test_beta2_batch_boundary_has_three_standard_members_and_one_strict_singleton(tmp_path):
    standard = make_three_standard_receipts()
    strict = make_accepted_candidate_receipt(ticket_key="issue:4", assurance="strict", accepted_sequence=4)
    integrator, drivers = make_integrator(tmp_path, hosted_outcomes=("passed", "passed"))

    standard_action = integrator.prepare(make_batch_request(accepted_candidates=standard))
    strict_action = integrator.prepare(make_batch_request(
        stable_action_id="delivery-action:strict",
        accepted_candidates=(strict,),
    ))
    standard_observation = integrator.execute(standard_action)
    strict_observation = integrator.execute(strict_action)

    assert standard_observation.phase == strict_observation.phase == "complete"
    assert len(standard_observation.members) == 3
    assert len(strict_observation.members) == 1
    assert standard_action.batch_sha != strict_action.batch_sha
    assert {item.candidate_sha for item in standard_observation.members} == {item.candidate_sha for item in standard}
    assert strict_observation.members[0].candidate_sha == strict.candidate_sha
    assert drivers.target_mutations == [standard_action.batch_sha, strict_action.batch_sha]
    assert len(standard_observation.delivery_proofs) == 1
    assert len(strict_observation.delivery_proofs) == 1
    assert standard_observation.delivery_proofs[0].batch_sha == standard_action.batch_sha
    assert strict_observation.delivery_proofs[0].batch_sha == strict_action.batch_sha


def test_beta2_successful_fallback_exports_exact_singleton_proof_partition(tmp_path):
    candidates = make_three_standard_receipts()
    integrator, drivers = make_integrator(
        tmp_path,
        hosted_outcomes=("code_failure", "passed", "passed", "passed"),
    )
    action = integrator.prepare(
        make_batch_request(accepted_candidates=candidates)
    )

    observations = [integrator.execute(action) for _ in range(4)]
    final = observations[-1]

    assert final.phase == "complete"
    assert final.fallback_generation == 1
    assert tuple(proof.member_ticket_keys for proof in final.delivery_proofs) == tuple(
        (candidate.ticket_key,) for candidate in candidates
    )
    assert [proof.batch_sha for proof in final.delivery_proofs] == (
        drivers.target_mutations
    )
    assert all(
        proof.delivery_stable_action_id != action.stable_action_id
        for proof in final.delivery_proofs
    )
    assert final.receipt_digest == digest_value(
        {"kind": "batch-observation.v1", **final.body()}
    )


def test_beta2_restart_and_failure_evidence_preserve_unaffected_member_facts(tmp_path):
    integrator, drivers = make_integrator(tmp_path, hosted_outcomes=("code_failure", "code_failure", "passed", "passed"))
    candidates = make_three_standard_receipts()
    action = integrator.prepare(make_batch_request(accepted_candidates=candidates))

    [integrator.execute(action) for _ in range(4)]
    restarted, _restarted_drivers = make_integrator(tmp_path)
    final = restarted.readback(action)

    assert final is not None
    assert {member.candidate_sha for member in final.members} == {candidate.candidate_sha for candidate in candidates}
    assert drivers.resume_directives == [("work-run:1", candidates[0].review_finding_ledger_digest)]
    unaffected = {
        member.ticket_key: member.evidence_digests
        for observation in [final]
        for member in observation.members
        if member.ticket_key in {"issue:2", "issue:3"}
    }
    assert unaffected == {
        "issue:2": candidates[1].evidence_digests,
        "issue:3": candidates[2].evidence_digests,
    }
~~~

- [ ] **Step 2: Run RED.**

~~~powershell
py -3.13 -m pytest tests/test_v8_batch_beta2.py::test_beta2_batch_boundary_has_three_standard_members_and_one_strict_singleton -q
~~~

Expected: FAIL because the Beta2 evidence test file and its completed V3 BatchIntegrator path do not yet exist in the implementation branch.

- [ ] **Step 3: Add the evidence record after the implementation Result is merged.**

docs/e2e/gwo-v8-batch-integrator.md must record, with exact values rather than prose-only claims:

- the merged commit SHAs for #115, #116, and #117;
- the focused test commands and pass counts for test_v8_batch_integrator.py, test_v8_batch_recovery.py, and test_v8_batch_beta2.py;
- the full py -3.13 -m pytest -q, py -3.13 scripts/quick_validate.py, the package synchronization command `py -3.13 scripts/sync_orchestrator.py` followed by `py -3.13 scripts/sync_orchestrator.py --check`, and git diff --check results;
- one isolated repository readback showing three Standard accepted Candidate SHAs in one Batch and one Strict accepted Candidate SHA in a separate Singleton Batch;
- the exact Batch SHA at local suite, publication branch, PR head, hosted check head, and target readback for each Batch;
- every direct `BatchDeliveryProof` field and proof digest, plus one successful fallback parent's ordered one-Singleton-proof-per-member partition;
- one unchanged-SHA infrastructure retry episode with retry count at most two;
- one multi-member code-class failure with one Singleton fallback and preserved unaffected Candidate/Evidence identities;
- one restart that adopts a terminal hosted-result receipt with zero provider rereads;
- explicit negative evidence for wrong SHA/receipt, ambiguous attribution, wrong merge-target mapping, squash, and rebase paths; and
- the release-train statement: “Beta2 feature-complete preview; no V3 writer cutover and no GA admission.”

The merged CI/isolated-delivery harness must export its exact Git, CI, target,
fallback, and journal readbacks as JSON and set
`GWO_BATCH_READBACK_JSON` to that artifact. The generator validates the
readback relationships and refuses to invent missing values. Implement
`scripts/write_v8_batch_evidence.py` with this executable body; exact merged
SHAs are verified as Git commits and ancestors of `HEAD`, and all test counts
come from fresh JUnit XML:

```python
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shlex
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "docs/e2e/gwo-v8-batch-integrator.md"
FOCUSED_TESTS = {
    "BatchIntegrator": ("tests/test_v8_batch_integrator.py",),
    "Batch recovery": ("tests/test_v8_batch_recovery.py",),
    "Beta2 boundary": ("tests/test_v8_batch_beta2.py",),
}
RELEASE_STATEMENT = (
    "Beta2 feature-complete preview; no V3 writer cutover and no GA admission."
)


def _run(arguments: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        arguments,
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )


def _display(arguments: list[str]) -> str:
    return shlex.join(arguments)


def _resolve_commit(reference: str) -> str:
    return _run(
        ["git", "rev-parse", "--verify", f"{reference}^{{commit}}"]
    ).stdout.strip()


def _require_merged(commit_sha: str) -> None:
    _run(["git", "merge-base", "--is-ancestor", commit_sha, "HEAD"])


def _object_id_length() -> int:
    object_format = _run(["git", "rev-parse", "--show-object-format"]).stdout.strip()
    if object_format == "sha1":
        return 40
    if object_format == "sha256":
        return 64
    raise SystemExit(f"unsupported Git object format: {object_format}")


def _require_object_id(name: str, value: Any, length: int) -> str:
    if (
        not isinstance(value, str)
        or len(value) != length
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise SystemExit(f"{name} is not an exact lowercase Git object ID")
    return value


def _require_digest(name: str, value: Any) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise SystemExit(f"{name} is not an exact lowercase SHA-256 digest")
    return value


def _digest_value(value: Any) -> str:
    payload = json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _validate_delivery_proof(
    name: str,
    value: Any,
    expected_member_ticket_keys: list[str],
    object_id_length: int,
    expected_batch_sha: str | None,
) -> str:
    required = {
        "delivery_stable_action_id",
        "delivery_request_digest",
        "batch_id",
        "batch_sha",
        "member_ticket_keys",
        "local_check_receipt_digest",
        "publication_receipt_digest",
        "pull_request_number",
        "pull_request_head_sha",
        "hosted_result_receipt_digest",
        "integration_lease_digest",
        "target_branch",
        "target_head_sha",
        "target_readback_digest",
        "target_contains_batch_sha",
        "pull_request_merge_target_sha",
        "merge_method",
        "proof_digest",
    }
    if not isinstance(value, dict) or set(value) != required:
        raise SystemExit(f"{name} fields differ from BatchDeliveryProof.v1")
    if (
        not isinstance(value["delivery_stable_action_id"], str)
        or not value["delivery_stable_action_id"]
        or not isinstance(value["batch_id"], str)
        or not value["batch_id"]
        or value["member_ticket_keys"] != expected_member_ticket_keys
    ):
        raise SystemExit(f"{name} delivery action, Batch, or member identity changed")
    _require_digest(
        f"{name}.delivery_request_digest",
        value["delivery_request_digest"],
    )
    batch_sha = _require_object_id(
        f"{name}.batch_sha", value["batch_sha"], object_id_length
    )
    if expected_batch_sha is not None and batch_sha != expected_batch_sha:
        raise SystemExit(f"{name}.batch_sha differs from the direct Batch")
    if _require_object_id(
        f"{name}.pull_request_head_sha",
        value["pull_request_head_sha"],
        object_id_length,
    ) != batch_sha:
        raise SystemExit(f"{name} PR head differs from the exact Batch SHA")
    if type(value["pull_request_number"]) is not int or value["pull_request_number"] <= 0:
        raise SystemExit(f"{name} pull-request number is not positive")
    for field in (
        "local_check_receipt_digest",
        "publication_receipt_digest",
        "hosted_result_receipt_digest",
        "integration_lease_digest",
        "target_readback_digest",
    ):
        _require_digest(f"{name}.{field}", value[field])
    target_head_sha = _require_object_id(
        f"{name}.target_head_sha",
        value["target_head_sha"],
        object_id_length,
    )
    if (
        not isinstance(value["target_branch"], str)
        or not value["target_branch"]
        or value["target_contains_batch_sha"] is not True
        or value["merge_method"] != "merge"
        or _require_object_id(
            f"{name}.pull_request_merge_target_sha",
            value["pull_request_merge_target_sha"],
            object_id_length,
        )
        != target_head_sha
    ):
        raise SystemExit(f"{name} target or merge readback is not exact")
    proof_digest = _require_digest(f"{name}.proof_digest", value["proof_digest"])
    body = dict(value)
    body.pop("proof_digest")
    if proof_digest != _digest_value(
        {"kind": "batch-delivery-proof.v1", **body}
    ):
        raise SystemExit(f"{name} proof digest does not cover exact readbacks")
    return batch_sha


def _pytest_receipt(
    label: str,
    targets: tuple[str, ...],
    junit_directory: Path,
) -> dict[str, Any]:
    junit_path = junit_directory / f"{label.lower().replace(' ', '-')}.xml"
    execution = [
        sys.executable,
        "-m",
        "pytest",
        *targets,
        "-q",
        f"--junitxml={junit_path}",
    ]
    _run(execution)
    root = ET.parse(junit_path).getroot()
    suites = [root] if root.tag == "testsuite" else list(root.findall("testsuite"))
    counts = {
        key: sum(int(suite.attrib.get(key, "0")) for suite in suites)
        for key in ("tests", "failures", "errors", "skipped")
    }
    if counts["failures"] or counts["errors"]:
        raise SystemExit(f"{label} did not pass")
    display = ["py", "-3.13", "-m", "pytest", *targets, "-q"]
    return {"command": _display(display), **counts}


def _gate_receipt(display: list[str], execution: list[str]) -> dict[str, Any]:
    _run(execution)
    return {"command": _display(display), "exit_code": 0}


def _validate_batch_readback(
    name: str,
    value: Any,
    expected_member_count: int,
    object_id_length: int,
) -> None:
    required = {
        "candidate_shas",
        "member_ticket_keys",
        "batch_sha",
        "local_sha",
        "publication_sha",
        "pr_head_sha",
        "hosted_sha",
        "target_readback_batch_sha",
        "delivery_proofs",
    }
    if not isinstance(value, dict) or set(value) != required:
        raise SystemExit(f"{name} readback fields differ from the exact schema")
    candidate_shas = value.get("candidate_shas")
    if not isinstance(candidate_shas, list) or len(candidate_shas) != expected_member_count:
        raise SystemExit(f"{name} must contain {expected_member_count} Candidate SHAs")
    for index, candidate_sha in enumerate(candidate_shas):
        _require_object_id(
            f"{name}.candidate_shas[{index}]", candidate_sha, object_id_length
        )
    member_ticket_keys = value.get("member_ticket_keys")
    if (
        not isinstance(member_ticket_keys, list)
        or len(member_ticket_keys) != expected_member_count
        or any(not isinstance(ticket_key, str) or not ticket_key for ticket_key in member_ticket_keys)
        or len(set(member_ticket_keys)) != len(member_ticket_keys)
    ):
        raise SystemExit(f"{name} member Ticket partition is not exact")
    batch_sha = _require_object_id(
        f"{name}.batch_sha", value.get("batch_sha"), object_id_length
    )
    for field in (
        "local_sha",
        "publication_sha",
        "pr_head_sha",
        "hosted_sha",
        "target_readback_batch_sha",
    ):
        observed = _require_object_id(
            f"{name}.{field}", value.get(field), object_id_length
        )
        if observed != batch_sha:
            raise SystemExit(f"{name}.{field} does not equal the exact Batch SHA")
    delivery_proofs = value.get("delivery_proofs")
    if not isinstance(delivery_proofs, list) or len(delivery_proofs) != 1:
        raise SystemExit(f"{name} direct completion must expose exactly one proof")
    _validate_delivery_proof(
        f"{name}.delivery_proofs[0]",
        delivery_proofs[0],
        member_ticket_keys,
        object_id_length,
        batch_sha,
    )


def _validate_readbacks(readbacks: Any, object_id_length: int) -> dict[str, Any]:
    if not isinstance(readbacks, dict):
        raise SystemExit("readback artifact must be a JSON object")
    required = {
        "standard_batch",
        "strict_batch",
        "infrastructure_retry",
        "successful_fallback",
        "singleton_fallback",
        "restart_adoption",
        "negative_paths",
    }
    if set(readbacks) != required:
        raise SystemExit(
            "readback artifact keys differ from the exact Beta2 evidence schema"
        )
    _validate_batch_readback(
        "standard_batch", readbacks["standard_batch"], 3, object_id_length
    )
    _validate_batch_readback(
        "strict_batch", readbacks["strict_batch"], 1, object_id_length
    )

    retry = readbacks["infrastructure_retry"]
    retry_sha = _require_object_id(
        "infrastructure_retry.batch_sha",
        retry.get("batch_sha"),
        object_id_length,
    )
    retry_shas = retry.get("retry_shas")
    retry_count = retry.get("retry_count")
    if (
        not isinstance(retry_count, int)
        or not 0 <= retry_count <= 2
        or not isinstance(retry_shas, list)
        or len(retry_shas) != retry_count
    ):
        raise SystemExit("infrastructure retry count and readbacks are inconsistent")
    for observed in retry_shas:
        if _require_object_id(
            "infrastructure_retry.retry_sha", observed, object_id_length
        ) != retry_sha:
            raise SystemExit("infrastructure retry changed the Batch SHA")

    successful = readbacks["successful_fallback"]
    successful_required = {
        "parent_phase",
        "parent_fallback_generation",
        "parent_receipt_digest",
        "member_ticket_keys",
        "delivery_proofs",
    }
    if not isinstance(successful, dict) or set(successful) != successful_required:
        raise SystemExit("successful fallback fields differ from the exact schema")
    successful_ticket_keys = successful["member_ticket_keys"]
    successful_proofs = successful["delivery_proofs"]
    if (
        successful["parent_phase"] != "complete"
        or successful["parent_fallback_generation"] != 1
        or not isinstance(successful_ticket_keys, list)
        or len(successful_ticket_keys) != 3
        or any(
            not isinstance(ticket_key, str) or not ticket_key
            for ticket_key in successful_ticket_keys
        )
        or len(set(successful_ticket_keys)) != 3
        or not isinstance(successful_proofs, list)
        or len(successful_proofs) != 3
    ):
        raise SystemExit("successful fallback is not a three-Singleton completion")
    _require_digest(
        "successful_fallback.parent_receipt_digest",
        successful["parent_receipt_digest"],
    )
    successful_batch_shas = [
        _validate_delivery_proof(
            f"successful_fallback.delivery_proofs[{index}]",
            proof,
            [successful_ticket_keys[index]],
            object_id_length,
            None,
        )
        for index, proof in enumerate(successful_proofs)
    ]
    if (
        len(set(successful_batch_shas)) != 3
        or len(
            {
                proof["delivery_stable_action_id"]
                for proof in successful_proofs
            }
        )
        != 3
    ):
        raise SystemExit("successful fallback reused a child action or Batch SHA")

    fallback = readbacks["singleton_fallback"]
    singleton_shas = fallback.get("singleton_candidate_shas")
    if not isinstance(singleton_shas, list) or len(singleton_shas) != 3:
        raise SystemExit("Singleton fallback must preserve exactly three Candidate SHAs")
    for observed in singleton_shas:
        _require_object_id(
            "singleton_fallback.singleton_candidate_sha",
            observed,
            object_id_length,
        )
    unaffected = fallback.get("unaffected_evidence")
    if not isinstance(unaffected, dict) or set(unaffected) != {"issue:2", "issue:3"}:
        raise SystemExit("Singleton fallback unaffected Evidence mapping is not exact")
    for ticket_key, digests in unaffected.items():
        if not isinstance(digests, list) or not digests:
            raise SystemExit(f"{ticket_key} has no preserved Evidence digests")
        for digest in digests:
            _require_digest(f"{ticket_key} Evidence digest", digest)
    directives = fallback.get("resume_directives")
    if not isinstance(directives, list) or len(directives) != 1:
        raise SystemExit("Singleton fallback must contain one fixed resume directive")
    directive = directives[0]
    if (
        not isinstance(directive, list)
        or len(directive) != 2
        or directive[0] != "work-run:1"
    ):
        raise SystemExit("Singleton fallback resume directive named the wrong Work Run")
    _require_digest("Singleton fallback Review Finding ledger", directive[1])

    adoption = readbacks["restart_adoption"]
    _require_object_id(
        "restart_adoption.batch_sha",
        adoption.get("batch_sha"),
        object_id_length,
    )
    _require_digest("restart_adoption.receipt_digest", adoption.get("receipt_digest"))
    if adoption.get("provider_rereads") != 0:
        raise SystemExit("restart adoption performed a provider reread")

    expected_negative = {
        "wrong_sha": "DeliveryIdentityMismatch",
        "wrong_receipt": "DeliveryIdentityMismatch",
        "ambiguous_attribution": "DeliveryAttributionAmbiguous",
        "wrong_merge_target": "DeliveryIdentityMismatch",
        "squash": "DeliveryIdentityMismatch",
        "rebase": "DeliveryIdentityMismatch",
    }
    if readbacks["negative_paths"] != expected_negative:
        raise SystemExit("negative-path evidence differs from the exact expected errors")
    return readbacks


def _render(
    merged_shas: dict[str, str],
    focused: dict[str, dict[str, Any]],
    full: dict[str, Any],
    gates: list[dict[str, Any]],
    readbacks: dict[str, Any],
) -> str:
    lines = [
        "# GWO V8 BatchIntegrator Beta2 Evidence",
        "",
        "## Merged Results",
        "",
        "| Issue | Merged commit |",
        "| --- | --- |",
    ]
    lines.extend(
        f"| {issue} | `{sha}` |" for issue, sha in merged_shas.items()
    )
    lines.extend(
        [
            "",
            "## Focused pytest Receipts",
            "",
            "| Suite | Command | Tests | Failures | Errors | Skipped |",
            "| --- | --- | ---: | ---: | ---: | ---: |",
        ]
    )
    for label, receipt in focused.items():
        lines.append(
            f"| {label} | `{receipt['command']}` | {receipt['tests']} | "
            f"{receipt['failures']} | {receipt['errors']} | {receipt['skipped']} |"
        )
    lines.extend(
        [
            "",
            "## Full Verification",
            "",
            f"- `{full['command']}`: {full['tests']} tests, "
            f"{full['failures']} failures, {full['errors']} errors, "
            f"{full['skipped']} skipped.",
        ]
    )
    lines.extend(f"- `{gate['command']}`: exit {gate['exit_code']}." for gate in gates)
    lines.extend(
        [
            "",
            "## Exact Git, CI, Target, Recovery, and Receipt Readbacks",
            "",
            "```json",
            json.dumps(readbacks, indent=2, sort_keys=True),
            "```",
            "",
            "## Release Train Decision",
            "",
            RELEASE_STATEMENT,
            "",
        ]
    )
    return "\n".join(lines)


def _required(name: str, value: str | None) -> str:
    if not value:
        raise SystemExit(f"{name} is required")
    return value


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--issue-115-sha", default=os.environ.get("GWO_ISSUE_115_MERGED_SHA")
    )
    parser.add_argument(
        "--issue-116-sha", default=os.environ.get("GWO_ISSUE_116_MERGED_SHA")
    )
    parser.add_argument(
        "--issue-117-sha", default=os.environ.get("GWO_ISSUE_117_MERGED_SHA")
    )
    parser.add_argument(
        "--readbacks", default=os.environ.get("GWO_BATCH_READBACK_JSON")
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()

    object_id_length = _object_id_length()
    merged_shas = {
        "#115": _require_object_id(
            "#115 merged commit",
            _required("issue 115 merged SHA", args.issue_115_sha),
            object_id_length,
        ),
        "#116": _require_object_id(
            "#116 merged commit",
            _required("issue 116 merged SHA", args.issue_116_sha),
            object_id_length,
        ),
        "#117": _require_object_id(
            "#117 merged commit",
            _required("issue 117 merged SHA", args.issue_117_sha),
            object_id_length,
        ),
    }
    for issue, sha in merged_shas.items():
        if _resolve_commit(sha) != sha:
            raise SystemExit(f"{issue} merged SHA did not resolve to itself")
        _require_merged(sha)

    readback_path = Path(_required("readback artifact", args.readbacks)).resolve()
    readbacks = _validate_readbacks(
        json.loads(readback_path.read_text(encoding="utf-8")),
        object_id_length,
    )
    with tempfile.TemporaryDirectory(prefix="gwo-v8-batch-evidence-") as directory:
        junit_directory = Path(directory)
        focused = {
            label: _pytest_receipt(label, targets, junit_directory)
            for label, targets in FOCUSED_TESTS.items()
        }
        full = _pytest_receipt("Full pytest", (), junit_directory)

    gates = [
        _gate_receipt(
            ["py", "-3.13", "scripts/quick_validate.py"],
            [sys.executable, "scripts/quick_validate.py"],
        ),
        _gate_receipt(
            ["py", "-3.13", "scripts/sync_orchestrator.py"],
            [sys.executable, "scripts/sync_orchestrator.py"],
        ),
        _gate_receipt(
            ["py", "-3.13", "scripts/sync_orchestrator.py", "--check"],
            [sys.executable, "scripts/sync_orchestrator.py", "--check"],
        ),
        _gate_receipt(["git", "diff", "--check"], ["git", "diff", "--check"]),
    ]
    rendered = _render(merged_shas, focused, full, gates, readbacks)
    output = args.output.resolve()
    if args.check:
        if not output.is_file() or output.read_text(encoding="utf-8") != rendered:
            raise SystemExit(f"evidence document is stale: {output}")
        return 0
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(rendered, encoding="utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

Generate the document only with exact merged SHAs and the exported
readback artifact supplied through the four required environment variables:

```powershell
py -3.13 scripts/write_v8_batch_evidence.py --output docs/e2e/gwo-v8-batch-integrator.md
```

Do not record a Beta2 document as a GA release note, do not create a tag, and do not mutate GitHub state from this worktree.

- [ ] **Step 4: Run all gates from the clean merged Result.**

~~~powershell
py -3.13 -m pytest tests/test_v8_batch_integrator.py tests/test_v8_batch_recovery.py tests/test_v8_batch_beta2.py -q
py -3.13 -m pytest -q
py -3.13 scripts/quick_validate.py
py -3.13 scripts/sync_orchestrator.py
py -3.13 scripts/sync_orchestrator.py --check
py -3.13 scripts/write_v8_batch_evidence.py --output docs/e2e/gwo-v8-batch-integrator.md --check
git diff --check
~~~

Expected: every command exits zero, and the generator's `--check` proves the
committed document still equals freshly resolved merged SHAs, JUnit counts,
gate receipts, and exact Git/CI/journal readbacks. A failure in any identity,
stale-write, crash-boundary, duplicate-effect, evidence-generation, or
restart-convergence check blocks the #116/#117 Result.

- [ ] **Step 5: Commit the Beta2 evidence document.**

~~~powershell
py -3.13 scripts/sync_orchestrator.py
py -3.13 scripts/sync_orchestrator.py --check
git add skills/orchestrator/.skill-package.json scripts/write_v8_batch_evidence.py tests/test_v8_batch_beta2.py tests/v8_batch_test_support.py docs/e2e/gwo-v8-batch-integrator.md
git commit -m "test: record BatchIntegrator Beta2 evidence"
~~~

## Acceptance Coverage Matrix

| Issue acceptance requirement | Plan coverage |
| --- | --- |
| #116 oldest seed, oldest scan, one-to-four limit, same Campaign, no waiting/LLM | Tasks 1 and 4; test_forms_oldest_pairwise_compatible_candidates_up_to_four_without_waiting |
| #116 target/base, Policy Witness, delivery identity, Assurance, check environment, protected surface, Interaction Key compatibility | Tasks 1 and 4; test_policy_classified_interaction_key_forces_singleton and receipt digest tests |
| #116 Strict, gitlink, protected/high-coupling/non-decomposable Singleton rules | Tasks 2 and 4; strict/gitlink/policy tests |
| #116 Clean Base Advance and per-member PatchIdentityV1 reproduction | Tasks 2 and 5; authoritative ancestor, protected target-delta, PatchIdentity mismatch, and per-member reproduction tests |
| #116 deterministic multi-member commit and local-ref recovery | Task 5; fixed author/committer/date values and lost-ref recomposition SHA test |
| #116 exact SHA through local suite, push, PR, hosted CI, serial target integration and readback; squash/rebase rejection | Task 6; exact-boundary tests |
| #116/#117 complete observation exposes only exact delivery readbacks | Tasks 1, 6, 9, and 10; direct one-proof boundary, successful fallback proof partition, proof-digest tamper rejection, and evidence-schema validation |
| #116 wrong publication SHA fails closed before publication | Task 6; pre-publication readback injection asserts zero publish calls |
| #116 repository-global Integration Lease and private drivers | Tasks 3 and 6; lease CAS and target serialization tests |
| #117 unchanged-SHA infrastructure retry at most twice after authoritative readback | Task 8; test_infrastructure_failure_retries_same_batch_sha_at_most_twice |
| #117 terminal hosted receipt crash boundary and restart adoption | Tasks 3 and 8; post-commit `hosted_receipt_persisted` hook and zero provider reread |
| #117 one multi-member split into Singletons for composition/local/code-class failure | Task 9; test_multi_member_code_failure_dissolves_once_into_singletons |
| #117 per-member Candidate/Check/Review Evidence retained and no repeat Review | Task 9; evidence-preservation test |
| #117 formation/composition counters and singleton Candidate/Evidence writes | Task 9; separate formation/composition counters and singleton receipt facts |
| #117 only failing Singleton resumes its parked Work Run; changed code re-enters CandidateGate | Task 9; WorkerResumeDirective and resume test |
| #117 durable valid terminal hosted receipt adopted without provider reread | Tasks 3 and 8; restart adoption test |
| #117 DeliveryIdentityMismatch and DeliveryAttributionAmbiguous preserve facts and prohibit fallback/resume | Tasks 6 and 9; identity/attribution test |
| Beta2 exit evidence with no cutover or GA claim | Task 10 and docs/e2e/gwo-v8-batch-integrator.md |

## Self-Review Checklist Before Handing Off

- [ ] Re-read all #116 and #117 acceptance text and the complete Issue comments; the current Issues have no comments, so the body is the complete tracker contract.
- [ ] Verify every interface name in Tasks 1–10 matches the exact boundary block and that AcceptedCandidateReceipt never gains a Result field.
- [ ] Verify Task 1 support imports only CandidateGate/canonical/boundary values; `_batch_integrator_store.py`, `_batch_integrator_drivers.py`, `batch_patch_identity.py`, and `HostedResultReceipt` imports appear only after their creating task.
- [ ] Verify CandidateGate alone defines InteractionClassification/InteractionKey and ordinary same-key collision data selects exactly issue:1, issue:3, issue:4, and issue:5.
- [ ] Verify no new module imports kernel.py, integration_batch.py, runtime_gateway.py, or a provider/model/CLI fact.
- [ ] Verify the only write set for this plan is the file map above; V3 wiring in execution_kernel.py belongs to the production-composition plan and legacy deletion belongs to #118.
- [ ] Verify every crash boundary has a stable action/ref/receipt readback test and every duplicate external effect has a counter assertion.
- [ ] Verify the same Batch SHA is asserted at local, publication, PR, hosted, integration, and target readback boundaries.
- [ ] Verify Clean Base Advance proves authoritative base ancestry and target-delta protected Interaction Key facts before composition, and the lost-ref test reproduces the same deterministic multi-member commit SHA.
- [ ] Verify all Singleton paths have fallback_generation bounds, preserve Candidate/Evidence, and return only the failing member's resume directive.
- [ ] Verify every direct completion has one exact `BatchDeliveryProof`, every successful fallback parent has one ordered child proof per member, and no failed parent Batch receives a fabricated proof.
- [ ] Verify Beta2 failure evidence has one fixed resume directive and an exact unaffected-evidence mapping, never an ambiguous alternative assertion.
- [ ] Verify #115's package Result commit lands before Task 1 and that no #115/#116 package-changing work was parallelized; all package commits run `sync_orchestrator.py`, `sync_orchestrator.py --check`, then stage `skills/orchestrator/.skill-package.json` in the same commit.
- [ ] Verify Beta2 is explicitly not Beta1, Beta3, or GA and that no step creates a release tag or changes GitHub state.
