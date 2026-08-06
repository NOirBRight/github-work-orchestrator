# GWO V8 Candidate Assurance and Watchdog Handoff Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver Issues #114 and #115 through authoritative Candidate assurance, Standard/Strict Review, and bounded Repair, beginning with a small shared Candidate-receipt/ExecutionKernel foundation that #113 can consume without waiting for the rest of #114.

**Architecture:** CandidateGate owns exact Git Candidate readback, one immutable CandidateDiffRecordV1, deterministic checks, Assurance, Formal Review, Finding-ledger Repair, and the existing Plan Invalidation seam. ExecutionKernel remains the only lifecycle writer: it receives CandidateReceipt in WorkRunObservation, persists the exact canonical receipt before the Work Run phase transition, and exposes only read-only receipt accessors to later Watchdog code. The foundation is the shared prefix; after it merges, #113 takes the package-manifest lane first, remaining #114 follows after #113 merges, and the #115 Kernel budget adapter follows both.

**Tech Stack:** Python 3.13, pytest, frozen dataclasses, canonical JSON, SHA-256, SQLite, raw Git tree readback, RuntimeGateway typed readback, Artifact-backed Evidence, GitHub Issues/PRs/Checks.

## Global Constraints

- Follow the repository hierarchy: CONTEXT.md, accepted ADRs, lean architecture, stabilization spec, then roadmap.
- Governing accepted decisions are ADR-0039, ADR-0041, ADR-0042, ADR-0043, ADR-0047, ADR-0048, ADR-0049, ADR-0057, ADR-0059, ADR-0061, and ADR-0062. CandidateGate remains the sole Formal Review entry; #114/#115 do not choose Campaign ownership or construct successor Plan Revisions.
- Public workflow operations remain exactly start, advance, and inspect. No Candidate, Review, Repair, Watchdog, or Kernel helper becomes a public workflow operation.
- Every implementation behavior follows RED, prove RED, minimal GREEN, prove GREEN, refactor while green, then a small commit. Every Python command in this plan uses py -3.13.
- Beta1 is metadata/tracker repair only and is not production admission. Beta2 is the feature-complete preview. Beta3 is the cutover candidate. GA requires a real public-API root Canary plus exact target, Activation, and default-writer read-back.
- Task 1 is the required foundation prefix and must merge before #113 or remaining #114 implementation starts. It must not close #114 by itself.
- After Task 1, #113 owns Campaign Watchdog, stale/liveness types, Watchdog host files, Watchdog tests, and any liveness portions of execution_kernel.py. Remaining #114 work and ordinary #115 work must not modify execution_kernel.py.
- `skills/orchestrator/.skill-package.json` is a shared write even when Python source write sets are otherwise disjoint. Therefore Task 1 merges first, then #113 completes and merges its package-changing commits, then Tasks 2–7 run and merge, then Tasks 8–9 run. #113 and remaining #114 must never be implemented concurrently. The only work allowed in parallel with an active package-manifest lane is read-only review or documentation work whose commit does not touch any `skills/orchestrator` package file or `skills/orchestrator/.skill-package.json`.
- The only later Candidate-assurance task allowed to modify execution_kernel.py is Task 8, the serialized #115 Candidate-budget adapter, and it starts only after the #113 PR has merged.
- No foundation file defines WatchdogCampaignSnapshot, KernelWatchdogReadback, stale-binding types, or liveness projection types. Those types and their implementation belong to #113.
- Tasks 1–9 modify the `skills/orchestrator` package. After each such task's GREEN/refactor gate, run `py -3.13 scripts/sync_orchestrator.py`, then `py -3.13 scripts/sync_orchestrator.py --check`; the first command regenerates `skills/orchestrator/.skill-package.json`, and that generated manifest must be included in the same task commit. Never defer the manifest to Task 11, never hand-edit it, and do not stage `skills/implement-gwo/.skill-package.json` unless the sync command reports a real content change there.
- Candidate paths are unpadded base64url encodings of raw Git path bytes. Rename/copy inference is disabled and represented as delete plus add.
- A Candidate is neither Evidence nor a Result. Delivery eligibility requires an accepted-Candidate receipt; a code Result additionally requires exact integration and target read-back.
- Frozen Authority Grants, Policy Witness identity, read-only/no-delegation capability proof, Candidate bounds, and binding bounds cannot be widened by Worker text, model output, repair instructions, or Runtime options.
- One Work Run permits at most three distinct Candidate commit OIDs, one initial Worker binding, and at most one terminal-binding-Evidence-authorized replacement. Repair and replacement do not reset those bounds.
- Use no more than five parallel subagents, but only across non-overlapping write sets. The package-manifest lane is strictly serial: Task 1, #113, Tasks 2–7, then Tasks 8–9. Parallel subagents may only perform read-only review or documentation-only work that does not regenerate or stage the orchestrator manifest; never defer a required manifest update to manufacture concurrency.

## Existing Context and Ownership

Read these before implementation:

- CONTEXT.md and docs/agents/domain.md.
- docs/design/gwo-v8-lean-architecture.md, docs/design/gwo-v8-lean-stabilization-spec.md, docs/design/gwo-v8-lean-roadmap.md.
- Accepted ADRs 0039, 0041, 0042, 0043, 0047, 0048, 0049, 0057, 0059, 0061, and 0062.
- skills/orchestrator/scripts/gwo_v8/candidate_gate.py, execution_kernel.py, runtime_gateway.py.
- tests/test_v8_candidate_gate.py, tests/test_v8_candidate_gate_public.py, tests/test_v8_execution_kernel.py, tests/v8_successor_test_support.py.
- The complete Issue #113, #114, and #115 bodies/comments.

Current real seams to preserve:

- CandidateReadbackPort.read_candidate(repository: str, reported_reference: str) -> CandidateReadback.
- CandidateGate.audit_candidate(parent: CandidateGateParent, audit: CandidateAuditReport) -> CandidateGateResult.
- CandidateGate.verify_repair(parent: CandidateGateParent, packet: RepairPacket, candidate: CandidateIdentity) -> CandidateGateResult.
- WorkRunAction and WorkRunObservation in execution_kernel.py.
- RuntimeGateway.WorkRunSubject and WorkRunPurpose, including formal_review(), invalid_review_payload_retry(), and specialist_review(policy_id).

The existing CandidateDiffEntryV1 side/path/mode/object_oid shape is superseded by the architecture's old/new entry identity. The existing CandidateIdentity.changed_paths spelling is migrated to changed_path_tokens; a read-only changed_paths property may remain temporarily for old tests, but all new canonical payloads use changed_path_tokens.

## Cross-Plan Foundation Contract

### Exact foundation write set

Task 1 is one small mergeable PR and may stage only:

- Modify skills/orchestrator/scripts/gwo_v8/candidate_gate.py:
  CandidateReceipt, exact CandidateDiffRecordV1, exact CandidateDiffEntryV1, and canonical readback validation.
- Modify skills/orchestrator/scripts/gwo_v8/execution_kernel.py:
  WorkRunObservation.candidate_receipt, direct run-state persistence/readback, and WorkRunSummary.candidate_receipt_digest only if needed by existing diagnostics.
- Modify (generated by package sync): skills/orchestrator/.skill-package.json:
  the content digest for the foundation package; do not hand-edit this file.
- Create tests/v8_candidate_assurance_test_support.py:
  reusable fixture and inspectable effect support.
- Create tests/test_v8_candidate_receipt_foundation.py:
  receipt/diff schema tests.
- Create tests/test_v8_candidate_receipt_kernel.py:
  exact Kernel receipt baseline; #113 Watchdog baseline runs this filename.

The foundation must not stage campaign_watchdog.py, runtime_gateway.py, plan_control_host.py, any Watchdog test, any stale/liveness type, any public API wrapper, or any #115 documentation.

### Exact CandidateReceipt

Define this exact frozen type in candidate_gate.py:

~~~python
@dataclass(frozen=True, slots=True)
class CandidateReceipt:
    parent_digest: str
    repository: str
    campaign_key: str
    campaign_handle: str
    plan_revision_digest: str
    work_run_key: str
    ticket_key: str
    reported_reference: str
    base_commit_oid: str
    base_tree_oid: str
    candidate_commit_oid: str
    candidate_tree_oid: str
    diff_schema_version: str
    diff_record_digest: str
    authority_subtree_digest: str
    runtime_subject_digest: str
    receipt_digest: str | None = None

    def __post_init__(self) -> None:
        for field_name in (
            "parent_digest",
            "plan_revision_digest",
            "authority_subtree_digest",
            "runtime_subject_digest",
        ):
            _require_digest(getattr(self, field_name), field_name)
        for field_name in (
            "repository",
            "campaign_key",
            "campaign_handle",
            "work_run_key",
            "ticket_key",
            "reported_reference",
            "diff_schema_version",
        ):
            _require_text(getattr(self, field_name), field_name)
        for field_name in (
            "base_commit_oid",
            "base_tree_oid",
            "candidate_commit_oid",
            "candidate_tree_oid",
        ):
            _require_object_id(getattr(self, field_name), field_name)
        _require_digest(self.diff_record_digest, "diff_record_digest")
        if self.diff_schema_version != "CandidateDiffRecordV1":
            raise CandidateGateError(
                "CANDIDATE_RECEIPT_INVALID",
                "CandidateReceipt diff schema is not CandidateDiffRecordV1",
            )
        expected = digest_value(self._body())
        if self.receipt_digest is None:
            object.__setattr__(self, "receipt_digest", expected)
        else:
            _validate_stored_digest(
                self.receipt_digest,
                self._body(),
                code="CANDIDATE_RECEIPT_INVALID",
                detail="CandidateReceipt digest changed",
            )

    def _body(self) -> dict[str, object]:
        return {
            "kind": "candidate_receipt.v1",
            "parent_digest": self.parent_digest,
            "repository": self.repository,
            "campaign_key": self.campaign_key,
            "campaign_handle": self.campaign_handle,
            "plan_revision_digest": self.plan_revision_digest,
            "work_run_key": self.work_run_key,
            "ticket_key": self.ticket_key,
            "reported_reference": self.reported_reference,
            "base_commit_oid": self.base_commit_oid,
            "base_tree_oid": self.base_tree_oid,
            "candidate_commit_oid": self.candidate_commit_oid,
            "candidate_tree_oid": self.candidate_tree_oid,
            "diff_schema_version": self.diff_schema_version,
            "diff_record_digest": self.diff_record_digest,
            "authority_subtree_digest": self.authority_subtree_digest,
            "runtime_subject_digest": self.runtime_subject_digest,
        }

    @property
    def digest(self) -> str:
        assert self.receipt_digest is not None
        return self.receipt_digest

    def canonical(self) -> dict[str, object]:
        return {**self._body(), "receipt_digest": self.digest}
~~~

The exact canonical root keys are kind, parent_digest, repository, campaign_key, campaign_handle, plan_revision_digest, work_run_key, ticket_key, reported_reference, base_commit_oid, base_tree_oid, candidate_commit_oid, candidate_tree_oid, diff_schema_version, diff_record_digest, authority_subtree_digest, runtime_subject_digest, and receipt_digest. The kind is candidate_receipt.v1. candidate_tree_oid must be a root key; it must not be hidden under candidate or identity.

Add exact constructors:

~~~python
@classmethod
def from_readback(
    cls,
    *,
    parent: CandidateGateParent,
    reported_reference: str,
    readback: CandidateReadback,
) -> "CandidateReceipt":
    subject = parent.runtime_subject
    candidate = readback.candidate
    return cls(
        parent_digest=parent.digest,
        repository=readback.repository,
        campaign_key=subject.campaign_key,
        campaign_handle=subject.campaign_handle,
        plan_revision_digest=subject.plan_revision_digest,
        work_run_key=subject.work_run_key,
        ticket_key=subject.ticket_key,
        reported_reference=reported_reference,
        base_commit_oid=candidate.base_commit_oid,
        base_tree_oid=candidate.base_tree_oid,
        candidate_commit_oid=candidate.candidate_commit_oid,
        candidate_tree_oid=candidate.candidate_tree_oid,
        diff_schema_version=readback.diff_record.schema_version,
        diff_record_digest=readback.diff_record.digest,
        authority_subtree_digest=subject.authority_subtree_digest,
        runtime_subject_digest=subject.digest,
    )

@classmethod
def from_canonical(cls, value: Mapping[str, Any]) -> "CandidateReceipt":
    expected_keys = frozenset(
        {
            "kind",
            "parent_digest",
            "repository",
            "campaign_key",
            "campaign_handle",
            "plan_revision_digest",
            "work_run_key",
            "ticket_key",
            "reported_reference",
            "base_commit_oid",
            "base_tree_oid",
            "candidate_commit_oid",
            "candidate_tree_oid",
            "diff_schema_version",
            "diff_record_digest",
            "authority_subtree_digest",
            "runtime_subject_digest",
            "receipt_digest",
        }
    )
    if not isinstance(value, Mapping) or frozenset(value) != expected_keys:
        raise CandidateGateError(
            "CANDIDATE_RECEIPT_INVALID",
            "CandidateReceipt canonical keys are not exact",
        )
    if value["kind"] != "candidate_receipt.v1":
        raise CandidateGateError(
            "CANDIDATE_RECEIPT_INVALID",
            "CandidateReceipt kind is invalid",
        )
    try:
        return cls(
            parent_digest=value["parent_digest"],
            repository=value["repository"],
            campaign_key=value["campaign_key"],
            campaign_handle=value["campaign_handle"],
            plan_revision_digest=value["plan_revision_digest"],
            work_run_key=value["work_run_key"],
            ticket_key=value["ticket_key"],
            reported_reference=value["reported_reference"],
            base_commit_oid=value["base_commit_oid"],
            base_tree_oid=value["base_tree_oid"],
            candidate_commit_oid=value["candidate_commit_oid"],
            candidate_tree_oid=value["candidate_tree_oid"],
            diff_schema_version=value["diff_schema_version"],
            diff_record_digest=value["diff_record_digest"],
            authority_subtree_digest=value["authority_subtree_digest"],
            runtime_subject_digest=value["runtime_subject_digest"],
            receipt_digest=value["receipt_digest"],
        )
    except CandidateGateError as error:
        raise CandidateGateError("CANDIDATE_RECEIPT_INVALID", error.detail) from error
~~~

from_canonical must require the exact key set, validate all text/digest/OID fields, recompute digest, and fail with CandidateGateError(code="CANDIDATE_RECEIPT_INVALID") on any alteration. digest is computed from the canonical body without receipt_digest; canonical adds the recomputed receipt_digest and rejects a stored digest that differs.

### Exact CandidateDiffRecordV1

Use these fields:

~~~python
@dataclass(frozen=True, slots=True)
class CandidateDiffEntryV1:
    old_path: str | None
    new_path: str | None
    change_kind: str
    old_mode: str | None
    new_mode: str | None
    old_object_type: str | None
    new_object_type: str | None
    old_oid: str | None
    new_oid: str | None

    def canonical(self) -> dict[str, str | None]:
        return {
            "old_path": self.old_path,
            "new_path": self.new_path,
            "change_kind": self.change_kind,
            "old_mode": self.old_mode,
            "new_mode": self.new_mode,
            "old_object_type": self.old_object_type,
            "new_object_type": self.new_object_type,
            "old_oid": self.old_oid,
            "new_oid": self.new_oid,
        }
~~~

Allowed change_kind values are add, delete, modify, and type-change. Allowed non-null object types are blob and gitlink. Modes are six ASCII octal characters. Paths are unpadded base64url raw path bytes.

CandidateDiffRecordV1 fields are schema_version, repository_object_format, base_commit_oid, base_tree_oid, candidate_commit_oid, candidate_tree_oid, entries, and optional record_digest. schema_version is exactly CandidateDiffRecordV1. Its canonical body is:

~~~python
{
    "schema_version": "CandidateDiffRecordV1",
    "repository_object_format": "sha1",
    "base": {"commit_oid": base_commit_oid, "tree_oid": base_tree_oid},
    "candidate": {
        "commit_oid": candidate_commit_oid,
        "tree_oid": candidate_tree_oid,
    },
    "entries": [entry.canonical() for entry in entries],
}
~~~

Implement the record as this immutable interface; add `import base64` beside the existing candidate_gate imports:

~~~python
@dataclass(frozen=True, slots=True)
class CandidateDiffRecordV1:
    schema_version: str
    repository_object_format: str
    base_commit_oid: str
    base_tree_oid: str
    candidate_commit_oid: str
    candidate_tree_oid: str
    entries: tuple[CandidateDiffEntryV1, ...]
    record_digest: str | None = None

    @classmethod
    def from_tree_entries(
        cls,
        *,
        repository_object_format: str,
        base_commit_oid: str,
        base_tree_oid: str,
        candidate_commit_oid: str,
        candidate_tree_oid: str,
        base_entries: Mapping[bytes, tuple[str, str, str]],
        candidate_entries: Mapping[bytes, tuple[str, str, str]],
    ) -> "CandidateDiffRecordV1":
        """Build add/delete/modify/type-change entries without rename inference."""
        def encode_path(raw_path: bytes) -> str:
            return base64.urlsafe_b64encode(raw_path).decode("ascii").rstrip("=")

        entries: list[CandidateDiffEntryV1] = []
        for raw_path in sorted(set(base_entries) | set(candidate_entries)):
            old = base_entries.get(raw_path)
            new = candidate_entries.get(raw_path)
            if old is None:
                change_kind = "add"
            elif new is None:
                change_kind = "delete"
            elif old[1] != new[1]:
                change_kind = "type-change"
            elif old != new:
                change_kind = "modify"
            else:
                continue
            entries.append(
                CandidateDiffEntryV1(
                    old_path=None if old is None else encode_path(raw_path),
                    new_path=None if new is None else encode_path(raw_path),
                    change_kind=change_kind,
                    old_mode=None if old is None else old[0],
                    new_mode=None if new is None else new[0],
                    old_object_type=None if old is None else old[1],
                    new_object_type=None if new is None else new[1],
                    old_oid=None if old is None else old[2],
                    new_oid=None if new is None else new[2],
                )
            )
        return cls(
            schema_version="CandidateDiffRecordV1",
            repository_object_format=repository_object_format,
            base_commit_oid=base_commit_oid,
            base_tree_oid=base_tree_oid,
            candidate_commit_oid=candidate_commit_oid,
            candidate_tree_oid=candidate_tree_oid,
            entries=tuple(entries),
        )

    @property
    def changed_path_tokens(self) -> tuple[str, ...]:
        ordered: list[str] = []
        for entry in self.entries:
            for token in (entry.old_path, entry.new_path):
                if token is not None and token not in ordered:
                    ordered.append(token)
        return tuple(ordered)

    @property
    def digest(self) -> str:
        assert self.record_digest is not None
        return self.record_digest

    def canonical(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "repository_object_format": self.repository_object_format,
            "base": {
                "commit_oid": self.base_commit_oid,
                "tree_oid": self.base_tree_oid,
            },
            "candidate": {
                "commit_oid": self.candidate_commit_oid,
                "tree_oid": self.candidate_tree_oid,
            },
            "entries": [entry.canonical() for entry in self.entries],
            "record_digest": self.digest,
        }
~~~

Sort entries by decoded raw old path, decoded raw new path, change_kind, and null-side ordering. Rename/copy inference is disabled. The digest is the SHA-256 of the repository canonical bytes prefixed by ASCII gwo.candidate-diff-record.v1 followed by a NUL. Use existing canonical_bytes and digest_bytes helpers; never use repr or str(dict).

Keep the Candidate identity constructor consistent with the renamed diff
property:

~~~python
@dataclass(frozen=True, slots=True)
class CandidateIdentity:
    reported_reference: str
    base_commit_oid: str
    base_tree_oid: str
    candidate_commit_oid: str
    candidate_tree_oid: str
    changed_path_tokens: tuple[str, ...]
    candidate_digest: str | None = None

    def __post_init__(self) -> None:
        _require_text(self.reported_reference, "reported_reference")
        for field_name in (
            "base_commit_oid",
            "base_tree_oid",
            "candidate_commit_oid",
            "candidate_tree_oid",
        ):
            _require_object_id(getattr(self, field_name), field_name)
        _require_text_tuple(self.changed_path_tokens, "changed_path_tokens")
        if self.changed_path_tokens != tuple(sorted(set(self.changed_path_tokens))):
            raise CandidateGateError(
                "CANDIDATE_GATE_EVIDENCE_INVALID",
                "changed_path_tokens must be sorted and unique",
            )
        expected = digest_value(self._body())
        if self.candidate_digest is None:
            object.__setattr__(self, "candidate_digest", expected)
        else:
            _validate_stored_digest(
                self.candidate_digest,
                self._body(),
                code="CANDIDATE_GATE_EVIDENCE_INVALID",
                detail="CandidateIdentity digest changed",
            )

    def _body(self) -> dict[str, object]:
        return {
            "kind": "candidate_identity.v1",
            "reported_reference": self.reported_reference,
            "base_commit_oid": self.base_commit_oid,
            "base_tree_oid": self.base_tree_oid,
            "candidate_commit_oid": self.candidate_commit_oid,
            "candidate_tree_oid": self.candidate_tree_oid,
            "changed_path_tokens": list(self.changed_path_tokens),
        }

    @property
    def digest(self) -> str:
        assert self.candidate_digest is not None
        return self.candidate_digest

    @property
    def changed_paths(self) -> tuple[str, ...]:
        """Read-only migration spelling for predecessor tests."""
        return self.changed_path_tokens

    def canonical(self) -> dict[str, object]:
        return {**self._body(), "candidate_digest": self.digest}
~~~

### Stable foundation-to-#113 seam

The foundation owns only these read-only Kernel methods:

~~~python
from .candidate_gate import CandidateGateError, CandidateReceipt


class ExecutionKernel:
    def read_candidate_receipt(
        self,
        campaign_handle: CampaignHandle,
        ticket_key: str,
    ) -> CandidateReceipt | None:
        state = self._load(campaign_handle)
        if state is None:
            return None
        runs = state.get("runs")
        if type(runs) is not dict or type(ticket_key) is not str:
            raise ExecutionKernelError(
                "EXECUTION_STORE_INVALID",
                "ExecutionKernel run state is not a ticket-keyed mapping",
            )
        run = runs.get(ticket_key)
        if run is None:
            return None
        if type(run) is not dict:
            raise ExecutionKernelError(
                "EXECUTION_STORE_INVALID",
                "ExecutionKernel Work Run state is not a mapping",
            )
        stored = run.get("candidate_receipt")
        if stored is None:
            return None
        try:
            receipt = CandidateReceipt.from_canonical(stored)
        except CandidateGateError as error:
            raise ExecutionKernelError(
                "EXECUTION_STORE_INVALID",
                "stored CandidateReceipt failed canonical readback",
            ) from error
        if (
            receipt.repository != campaign_handle.repository
            or receipt.campaign_key != campaign_handle.campaign_key
            or receipt.campaign_handle != campaign_handle.campaign_key
            or receipt.plan_revision_digest != state.get("plan_revision_digest")
            or receipt.ticket_key != ticket_key
            or receipt.work_run_key != run.get("work_run_key")
        ):
            raise ExecutionKernelError(
                "EXECUTION_STORE_INVALID",
                "stored CandidateReceipt is bound to another Campaign or Work Run",
            )
        return receipt

    def read_candidate_receipts(
        self,
        campaign_handle: CampaignHandle,
    ) -> tuple[tuple[str, CandidateReceipt], ...]:
        state = self._load(campaign_handle)
        if state is None:
            return ()
        runs = state.get("runs")
        if type(runs) is not dict:
            raise ExecutionKernelError(
                "EXECUTION_STORE_INVALID",
                "ExecutionKernel run state is not a mapping",
            )
        values: list[tuple[str, CandidateReceipt]] = []
        for ticket_key in sorted(runs):
            receipt = self.read_candidate_receipt(campaign_handle, ticket_key)
            if receipt is not None:
                values.append((ticket_key, receipt))
        return tuple(values)
~~~

They read an existing SQLite row only. They do not initialize, migrate, save, advance, execute effects, or write an Artifact. They sort by ticket key and fail with ExecutionKernelError(code="EXECUTION_STORE_INVALID") when the direct canonical mapping is malformed, digest-invalid, or bound to another run.

#113 owns Watchdog-specific methods and types. In particular, #113 defines WatchdogCampaignSnapshot, KernelWatchdogReadback if it needs one, stale-binding observations, trusted-progress digest rules, and ExecutionKernel.watchdog_snapshot. #113's watchdog_snapshot calls read_candidate_receipts or performs the same exact CandidateReceipt.from_canonical readback. It must not create another receipt type or change the storage path. Foundation does not define any of those Watchdog or liveness records.

### Exact direct storage and fixture handoff

Every initialized run has candidate_receipt: None. When a WorkRunObservation carries a CandidateReceipt, Kernel persists:

~~~python
state["runs"][ticket_key]["candidate_receipt"] = observation.candidate_receipt.canonical()
~~~

This mapping is stored directly at that path, with no wrapper and no top-level Campaign receipt. Kernel re-reads and validates the canonical receipt before applying the phase transition.

Create tests/v8_candidate_assurance_test_support.py with:

~~~python
from dataclasses import dataclass, field

import pytest

from gwo_v8.candidate_gate import (
    CandidateDiffEntryV1,
    CandidateDiffRecordV1,
    CandidateGateParent,
    CandidateIdentity,
    CandidateReadback,
    CandidateReceipt,
)
from gwo_v8.execution_kernel import (
    ExecutionKernel,
    WorkRunAction,
    WorkRunObservation,
)
from gwo_v8.runtime_gateway import WorkRunPurpose, WorkRunSubject
from v8_successor_test_support import _StaticPlanReader, _minimal_active_campaign


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
    if active is None or campaign is None:
        active, campaign = _minimal_active_campaign((ticket_key,))
    subject = WorkRunSubject(
        repository=campaign.repository,
        campaign_key=campaign.campaign_key,
        campaign_handle=campaign.campaign_key,
        plan_revision_digest=active.current_revision_digest,
        work_run_key=f"work-run:{ticket_key}",
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
    return CandidateReceipt.from_readback(
        parent=parent,
        reported_reference=candidate.reported_reference,
        readback=readback,
    )


def read_kernel_state(kernel: ExecutionKernel, campaign):
    state = kernel._load(campaign)
    assert state is not None
    return state


def write_kernel_state(kernel: ExecutionKernel, campaign, state: dict[str, object]) -> None:
    kernel._save(campaign, state)


@pytest.fixture
def kernel_with_candidate_receipt(tmp_path):
    active, campaign = _minimal_active_campaign(("issue:114",))
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
~~~

The fixture must have state created before yielding, must contain the exact canonical receipt at state["runs"][ticket]["candidate_receipt"], and must return an effect object whose effects.executed list remains inspectable. #113 test modules register it with pytest_plugins = ("v8_candidate_assurance_test_support",) and use the injected tuple; they do not call the fixture, duplicate it, or construct a second receipt.


### Task 1: Merge the Shared CandidateReceipt/Kernel Foundation First

**Files**

- Modify: skills/orchestrator/scripts/gwo_v8/candidate_gate.py
- Modify: skills/orchestrator/scripts/gwo_v8/execution_kernel.py
- Create: tests/v8_candidate_assurance_test_support.py
- Create: tests/test_v8_candidate_receipt_foundation.py
- Create: tests/test_v8_candidate_receipt_kernel.py

**Produces**

CandidateReceipt, exact diff identity, WorkRunObservation.candidate_receipt, direct Kernel run-state persistence/readback, optional WorkRunSummary.candidate_receipt_digest, and the reusable fixture. No WatchdogCampaignSnapshot, KernelWatchdogReadback, stale/liveness projection, or Watchdog code is produced.

- [ ] **Step 1: Write the failing foundation tests**

Add these exact tests:

~~~python
def test_candidate_receipt_canonical_exposes_candidate_tree_oid_at_root():
    receipt = make_candidate_receipt()
    value = receipt.canonical()
    assert value["candidate_tree_oid"] == receipt.candidate_tree_oid
    assert value["diff_record_digest"] == receipt.diff_record_digest
    assert value["authority_subtree_digest"] == receipt.authority_subtree_digest
    assert value["receipt_digest"] == receipt.digest


def test_candidate_receipt_round_trip_recomputes_digest():
    receipt = make_candidate_receipt()
    assert CandidateReceipt.from_canonical(receipt.canonical()) == receipt


@pytest.mark.parametrize("field", [
    "parent_digest",
    "candidate_commit_oid",
    "candidate_tree_oid",
    "diff_record_digest",
    "runtime_subject_digest",
])
def test_candidate_receipt_rejects_adversarial_identity_tamper(field):
    receipt = make_candidate_receipt()
    value = receipt.canonical()
    value[field] = "f" * (40 if field.endswith("_oid") else 64)
    with pytest.raises(CandidateGateError) as raised:
        CandidateReceipt.from_canonical(value)
    assert raised.value.code == "CANDIDATE_RECEIPT_INVALID"


def test_candidate_diff_record_contains_complete_old_new_entry_identity():
    record = make_candidate_diff_record()
    assert record.canonical()["entries"][0] == {
        "old_path": None,
        "new_path": "c3JjL21haW4ucHk",
        "change_kind": "add",
        "old_mode": None,
        "new_mode": "100644",
        "old_object_type": None,
        "new_object_type": "blob",
        "old_oid": None,
        "new_oid": "3" * 40,
    }
~~~

Add tests/test_v8_candidate_receipt_kernel.py with exact names:

~~~python
pytest_plugins = ("v8_candidate_assurance_test_support",)

def test_kernel_persists_exact_candidate_receipt_at_run_root(
    kernel_with_candidate_receipt,
):
    kernel, effects, campaign, receipt = kernel_with_candidate_receipt
    assert effects.executed
    assert kernel.read_candidate_receipt(campaign, "issue:114") == receipt
    state = read_kernel_state(kernel, campaign)
    assert state["runs"]["issue:114"]["candidate_receipt"] == receipt.canonical()
    assert state["runs"]["issue:114"]["candidate_receipt"]["candidate_tree_oid"] == (
        receipt.candidate_tree_oid
    )


def test_kernel_receipt_readback_is_read_only_and_sorted(
    kernel_with_candidate_receipt,
):
    kernel, _effects, campaign, receipt = kernel_with_candidate_receipt
    before = kernel._store_path.read_bytes()
    assert kernel.read_candidate_receipts(campaign) == (("issue:114", receipt),)
    assert kernel._store_path.read_bytes() == before


def test_kernel_receipt_readback_survives_restart(kernel_with_candidate_receipt):
    kernel, effects, campaign, receipt = kernel_with_candidate_receipt
    restarted = ExecutionKernel(
        store_path=kernel._store_path,
        plan_control=kernel._plan_control,
        effects=effects,
    )
    assert restarted.read_candidate_receipt(campaign, "issue:114") == receipt


def test_kernel_rejects_corrupt_candidate_receipt_at_direct_run_path(
    kernel_with_candidate_receipt,
):
    kernel, _effects, campaign, _receipt = kernel_with_candidate_receipt
    state = read_kernel_state(kernel, campaign)
    state["runs"]["issue:114"]["candidate_receipt"]["candidate_tree_oid"] = "f" * 40
    write_kernel_state(kernel, campaign, state)
    with pytest.raises(ExecutionKernelError) as raised:
        kernel.read_candidate_receipt(campaign, "issue:114")
    assert raised.value.code == "EXECUTION_STORE_INVALID"


def test_kernel_fixture_keeps_effects_executed_inspectable(
    kernel_with_candidate_receipt,
):
    _kernel, effects, _campaign, _receipt = kernel_with_candidate_receipt
    assert len(effects.executed) == 1
~~~

Define make_candidate_receipt, make_candidate_diff_record, read_kernel_state, and write_kernel_state in the test/support files with exact names before calling them.

- [ ] **Step 2: Prove RED**

Run:

~~~powershell
py -3.13 -m pytest tests/test_v8_candidate_receipt_foundation.py tests/test_v8_candidate_receipt_kernel.py -q
~~~

Expected: collection fails because CandidateReceipt is not exported by candidate_gate.py; after a symbol stub, the Kernel test fails with the missing candidate_receipt observation field or read_candidate_receipt method. Do not respond by adding Watchdog types.

- [ ] **Step 3: Implement minimal receipt/diff types**

Insert the complete `CandidateReceipt`, `CandidateDiffEntryV1`,
`CandidateDiffRecordV1`, and `CandidateIdentity` field sets shown in the
foundation contract. The following are the copyable validation/digest bodies
that complete those declarations; they replace the predecessor side/path diff
implementation rather than wrapping it:

~~~python
def _decode_candidate_path_token(token: str, field_name: str) -> bytes:
    _require_text(token, field_name)
    if "=" in token:
        raise CandidateGateError(
            "CANDIDATE_GATE_DIFF_INVALID",
            f"{field_name} is padded instead of unpadded base64url",
        )
    try:
        raw = base64.b64decode(
            token + "=" * (-len(token) % 4),
            altchars=b"-_",
            validate=True,
        )
    except (ValueError, binascii.Error) as error:
        raise CandidateGateError(
            "CANDIDATE_GATE_DIFF_INVALID",
            f"{field_name} is not canonical base64url",
        ) from error
    if not raw or b"\x00" in raw or raw.startswith(b"/"):
        raise CandidateGateError(
            "CANDIDATE_GATE_DIFF_INVALID",
            f"{field_name} is not a non-empty repository-relative raw path",
        )
    encoded = base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
    if encoded != token:
        raise CandidateGateError(
            "CANDIDATE_GATE_DIFF_INVALID",
            f"{field_name} is not in canonical base64url form",
        )
    return raw


def _validate_diff_side(
    *,
    path: str | None,
    mode: str | None,
    object_type: str | None,
    oid: str | None,
    side: str,
) -> None:
    values = (path, mode, object_type, oid)
    if all(value is None for value in values):
        return
    if any(value is None for value in values):
        raise CandidateGateError(
            "CANDIDATE_GATE_DIFF_INVALID",
            f"Candidate diff {side} identity is only partially present",
        )
    assert path is not None and mode is not None
    assert object_type is not None and oid is not None
    _decode_candidate_path_token(path, f"{side}_path")
    if re.fullmatch(r"[0-7]{6}", mode) is None:
        raise CandidateGateError(
            "CANDIDATE_GATE_DIFF_INVALID",
            f"Candidate diff {side} mode is invalid",
        )
    if object_type not in {"blob", "gitlink"}:
        raise CandidateGateError(
            "CANDIDATE_GATE_DIFF_INVALID",
            f"Candidate diff {side} object type is invalid",
        )
    _require_object_id(oid, f"{side}_oid")


@dataclass(frozen=True, slots=True)
class CandidateDiffEntryV1:
    old_path: str | None
    new_path: str | None
    change_kind: str
    old_mode: str | None
    new_mode: str | None
    old_object_type: str | None
    new_object_type: str | None
    old_oid: str | None
    new_oid: str | None

    def __post_init__(self) -> None:
        if self.change_kind not in {"add", "delete", "modify", "type-change"}:
            raise CandidateGateError(
                "CANDIDATE_GATE_DIFF_INVALID",
                "Candidate diff change kind is outside the closed union",
            )
        _validate_diff_side(
            path=self.old_path,
            mode=self.old_mode,
            object_type=self.old_object_type,
            oid=self.old_oid,
            side="old",
        )
        _validate_diff_side(
            path=self.new_path,
            mode=self.new_mode,
            object_type=self.new_object_type,
            oid=self.new_oid,
            side="new",
        )
        old_missing = self.old_path is None
        new_missing = self.new_path is None
        if (
            (self.change_kind == "add" and (not old_missing or new_missing))
            or (self.change_kind == "delete" and (old_missing or not new_missing))
            or (self.change_kind in {"modify", "type-change"} and (old_missing or new_missing))
        ):
            raise CandidateGateError(
                "CANDIDATE_GATE_DIFF_INVALID",
                "Candidate diff side presence does not match change kind",
            )
        if self.change_kind == "type-change" and (
            self.old_object_type == self.new_object_type
        ):
            raise CandidateGateError(
                "CANDIDATE_GATE_DIFF_INVALID",
                "type-change requires different old and new object types",
            )

    def canonical(self) -> dict[str, str | None]:
        return {
            "old_path": self.old_path,
            "new_path": self.new_path,
            "change_kind": self.change_kind,
            "old_mode": self.old_mode,
            "new_mode": self.new_mode,
            "old_object_type": self.old_object_type,
            "new_object_type": self.new_object_type,
            "old_oid": self.old_oid,
            "new_oid": self.new_oid,
        }


# Add these bodies to the CandidateDiffRecordV1 declaration from the contract.
def __post_init__(self) -> None:
    if self.schema_version != "CandidateDiffRecordV1":
        raise CandidateGateError(
            "CANDIDATE_GATE_DIFF_INVALID",
            "Candidate diff schema version is invalid",
        )
    if self.repository_object_format not in {"sha1", "sha256"}:
        raise CandidateGateError(
            "CANDIDATE_GATE_DIFF_INVALID",
            "Candidate diff repository object format is invalid",
        )
    for field_name in (
        "base_commit_oid",
        "base_tree_oid",
        "candidate_commit_oid",
        "candidate_tree_oid",
    ):
        _require_object_id(getattr(self, field_name), field_name)
    if type(self.entries) is not tuple or any(
        type(entry) is not CandidateDiffEntryV1 for entry in self.entries
    ):
        raise CandidateGateError(
            "CANDIDATE_GATE_DIFF_INVALID",
            "Candidate diff entries are not an exact immutable tuple",
        )
    def path_key(token: str | None) -> tuple[int, bytes]:
        return (0, b"") if token is None else (
            1,
            _decode_candidate_path_token(token, "entry path"),
        )
    ordered = tuple(
        sorted(
            self.entries,
            key=lambda entry: (
                path_key(entry.old_path),
                path_key(entry.new_path),
                entry.change_kind,
            ),
        )
    )
    if self.entries != ordered or len(
        {canonical_bytes(entry.canonical()) for entry in self.entries}
    ) != len(self.entries):
        raise CandidateGateError(
            "CANDIDATE_GATE_DIFF_INVALID",
            "Candidate diff entries are not canonical and unique",
        )
    expected = digest_bytes(
        b"gwo.candidate-diff-record.v1\x00" + canonical_bytes(self._body())
    )
    if self.record_digest is None:
        object.__setattr__(self, "record_digest", expected)
    elif self.record_digest != expected:
        raise CandidateGateError(
            "CANDIDATE_GATE_DIFF_INVALID",
            "Candidate diff record digest changed",
        )


def _body(self) -> dict[str, object]:
    return {
        "schema_version": self.schema_version,
        "repository_object_format": self.repository_object_format,
        "base": {
            "commit_oid": self.base_commit_oid,
            "tree_oid": self.base_tree_oid,
        },
        "candidate": {
            "commit_oid": self.candidate_commit_oid,
            "tree_oid": self.candidate_tree_oid,
        },
        "entries": [entry.canonical() for entry in self.entries],
    }


def canonical(self) -> dict[str, object]:
    return {**self._body(), "record_digest": self.digest}


# Replace CandidateReadback.__post_init__ with this exact binding check.
def __post_init__(self) -> None:
    _require_text(self.repository, "Candidate readback repository")
    if type(self.candidate) is not CandidateIdentity or type(
        self.diff_record
    ) is not CandidateDiffRecordV1:
        raise CandidateGateError(
            "CANDIDATE_GATE_READBACK_INVALID",
            "Candidate readback has an invalid typed identity",
        )
    if (
        self.diff_record.base_commit_oid != self.candidate.base_commit_oid
        or self.diff_record.base_tree_oid != self.candidate.base_tree_oid
        or self.diff_record.candidate_commit_oid
        != self.candidate.candidate_commit_oid
        or self.diff_record.candidate_tree_oid != self.candidate.candidate_tree_oid
        or self.diff_record.changed_path_tokens
        != self.candidate.changed_path_tokens
    ):
        raise CandidateGateError(
            "CANDIDATE_GATE_READBACK_INVALID",
            "Candidate readback diff does not bind the exact Candidate identity",
        )
    expected = digest_value(self._body())
    if self.readback_digest is None:
        object.__setattr__(self, "readback_digest", expected)
    else:
        _validate_stored_digest(
            self.readback_digest,
            self._body(),
            code="CANDIDATE_GATE_READBACK_INVALID",
            detail="Candidate readback digest changed",
        )
~~~

Add `import base64`, `import binascii`, and the new exported names to
`candidate_gate.__all__`. Preserve `CandidateGateParent` and
`CandidateReadbackPort` signatures. `CandidateReceipt` uses its complete
`__post_init__`, `_body`, `from_readback`, `from_canonical`, `digest`, and
`canonical` bodies from the exact foundation contract in this plan; none of
those methods is a stub.

- [ ] **Step 4: Prove receipt GREEN**

Run:

~~~powershell
py -3.13 -m pytest tests/test_v8_candidate_receipt_foundation.py -q
py -3.13 -m pytest tests/test_v8_candidate_gate.py -q
~~~

Expected: PASS; the old CandidateGate Plan Invalidation seam remains green.

- [ ] **Step 5: Implement Kernel persistence and the exact fixture**

Import `CandidateGateError` and `CandidateReceipt` from `candidate_gate`. Append
the field at the end of `WorkRunObservation` so predecessor positional calls
remain valid, and append the exact-type check to its existing
`__post_init__`:

~~~python
# Append as the last dataclass field.
candidate_receipt: CandidateReceipt | None = None

# Append these statements at the end of the existing __post_init__ body.
if self.candidate_receipt is not None and type(
    self.candidate_receipt
) is not CandidateReceipt:
    raise ExecutionKernelError(
        "WORK_RUN_OBSERVATION_INVALID",
        "candidate_receipt is not an exact CandidateReceipt",
    )
if (
    self.candidate_receipt is not None
    and self.receipt_digest != self.candidate_receipt.digest
):
    raise ExecutionKernelError(
        "WORK_RUN_OBSERVATION_INVALID",
        "effect receipt digest does not bind CandidateReceipt",
    )
~~~

Add `"candidate_receipt": None` to both run dictionaries created in
`_load_or_initialize` and `_new_run_state`. For an existing row, initialize
only the missing key with `run.setdefault("candidate_receipt", None)`; never
replace a non-null value. Immediately after the existing observation type and
stable-action validation in `_perform_due_effect`, and before assigning
`run["phase"]`, insert this complete persist/read-back branch:

~~~python
run.setdefault("candidate_receipt", None)
receipt = observation.candidate_receipt
if receipt is not None:
    if (
        receipt.repository != active.handle.repository
        or receipt.campaign_key != active.handle.campaign_key
        or receipt.campaign_handle != active.handle.campaign_key
        or receipt.plan_revision_digest != active.current_revision_digest
        or receipt.work_run_key != run["work_run_key"]
        or receipt.ticket_key != ticket_key
        or receipt.runtime_subject_digest != run["work_subject_digest"]
    ):
        raise ExecutionKernelError(
            "EXECUTION_STORE_INVALID",
            "CandidateReceipt is bound to another Campaign or Work Run",
        )
    run["candidate_receipt"] = receipt.canonical()
    self._save(active.handle, state)
    persisted_state = self._load(active.handle)
    if persisted_state is None:
        raise ExecutionKernelError(
            "EXECUTION_STORE_INVALID",
            "Campaign state disappeared after CandidateReceipt persistence",
        )
    persisted_run = persisted_state.get("runs", {}).get(ticket_key)
    if type(persisted_run) is not dict:
        raise ExecutionKernelError(
            "EXECUTION_STORE_INVALID",
            "CandidateReceipt Work Run disappeared during readback",
        )
    try:
        persisted_receipt = CandidateReceipt.from_canonical(
            persisted_run.get("candidate_receipt")
        )
    except CandidateGateError as error:
        raise ExecutionKernelError(
            "EXECUTION_STORE_INVALID",
            "persisted CandidateReceipt failed canonical readback",
        ) from error
    if persisted_receipt != receipt:
        raise ExecutionKernelError(
            "EXECUTION_STORE_INVALID",
            "persisted CandidateReceipt changed during readback",
        )
    state.clear()
    state.update(persisted_state)
    run = state["runs"][ticket_key]

run["phase"] = observation.phase
run["reason"] = observation.reason
run["next_check_at"] = observation.next_check_at
~~~

Implement `read_candidate_receipt` and `read_candidate_receipts` with the
complete pure-reader bodies in the foundation contract above. They call only
`_load` and `CandidateReceipt.from_canonical`; neither method calls `_save`,
`advance`, or an effect. Do not add `WorkRunSummary.candidate_receipt_digest`
unless an existing non-Watchdog diagnostic test first fails because it needs
that digest.

Create `tests/v8_candidate_assurance_test_support.py` with the complete
`CandidateReceiptEffects`, `make_candidate_diff_record`,
`make_candidate_receipt`, `read_kernel_state`, `write_kernel_state`, and
`kernel_with_candidate_receipt` bodies in the foundation handoff block. The
fixture executes `kernel.advance(campaign)`, proves the direct SQLite mapping,
then returns exactly `(kernel, effects, campaign, receipt)`; it does not yield
before state exists.

- [ ] **Step 6: Prove Kernel GREEN**

Run:

~~~powershell
py -3.13 -m pytest tests/test_v8_candidate_receipt_kernel.py -q
py -3.13 -m pytest tests/test_v8_execution_kernel.py tests/test_v8_successor_execution_kernel.py -q
~~~

Expected: PASS; the direct run path contains exact receipt.canonical(), root candidate_tree_oid is present, corrupt readback fails closed, and effects.executed remains inspectable.

- [ ] **Step 7: Refactor and commit only the foundation write set**

~~~powershell
py -3.13 -m pytest tests/test_v8_candidate_receipt_foundation.py tests/test_v8_candidate_receipt_kernel.py tests/test_v8_candidate_gate.py -q
py -3.13 scripts/sync_orchestrator.py
py -3.13 scripts/sync_orchestrator.py --check
git diff --check
git add skills/orchestrator/.skill-package.json skills/orchestrator/scripts/gwo_v8/candidate_gate.py skills/orchestrator/scripts/gwo_v8/execution_kernel.py tests/v8_candidate_assurance_test_support.py tests/test_v8_candidate_receipt_foundation.py tests/test_v8_candidate_receipt_kernel.py
git commit -m "feat: persist authoritative Candidate receipts"
~~~

Expected: no Watchdog, stale/liveness, RuntimeGateway, public API, or #115 file is included. Merge this PR, let #113 exclusively occupy and release the package-manifest lane, and only then start Task 2. Read-only review and docs-only work with no package or manifest write may run concurrently.

---

### Task 2: Implement Authoritative Git Candidate Readback and Complete Diff

**Manifest-lane dependency:** start only after Task 1 and #113 have both merged. This is a scheduling dependency caused solely by the shared generated `skills/orchestrator/.skill-package.json`; #114 does not consume a Watchdog type or change an #113-owned file.

**Files**

- Modify: skills/orchestrator/scripts/gwo_v8/candidate_gate.py
- Create: skills/orchestrator/scripts/gwo_v8/candidate_git.py
- Create: tests/test_v8_candidate_git_readback.py
- Modify: tests/test_v8_candidate_gate.py for changed_path_tokens and exact diff expectations

**Interfaces**

- Consumes: merged Task 1 CandidateReceipt, CandidateDiffRecordV1, CandidateReadback, and CandidateReadbackPort.
- Produces: GitCandidateReader(repository_path: Path, base_reader: CandidateBasePort).read_candidate(repository: str, reported_reference: str) -> CandidateReadback.
- Ownership: #114 only. Never modify execution_kernel.py or #113 files.

- [ ] **Step 1: Write RED**

~~~python
def test_git_candidate_reader_reads_exact_commit_and_tree_from_reference(tmp_path):
    repository = make_git_repository(tmp_path)
    readback = GitCandidateReader(
        repository_path=repository.path,
        base_reader=repository,
    ).read_candidate(repository.name, "refs/heads/candidate")
    assert readback.candidate.reported_reference == "refs/heads/candidate"
    assert readback.candidate.base_commit_oid == repository.base_commit_oid
    assert readback.candidate.candidate_tree_oid == repository.candidate_tree_oid


def test_git_candidate_reader_represents_rename_as_delete_and_add(tmp_path):
    repository = make_rename_repository(tmp_path)
    readback = GitCandidateReader(
        repository_path=repository.path,
        base_reader=repository,
    ).read_candidate(repository.name, "refs/heads/candidate")
    assert [entry.change_kind for entry in readback.diff_record.entries] == [
        "delete",
        "add",
    ]


def test_git_candidate_reader_preserves_raw_non_utf8_path_bytes(tmp_path):
    repository = make_non_utf8_path_repository(tmp_path)
    readback = GitCandidateReader(
        repository_path=repository.path,
        base_reader=repository,
    ).read_candidate(repository.name, "refs/heads/candidate")
    assert encode_raw_path(b"old-\xff.txt") in readback.diff_record.changed_path_tokens
~~~

Define the test repositories with raw Git plumbing so the non-UTF-8 case is
not normalized by the host filesystem. Each returned object implements the
real `CandidateBasePort.read_base` signature:

~~~python
import base64
from dataclasses import dataclass
from pathlib import Path
import subprocess


@dataclass(frozen=True)
class GitRepositoryFixture:
    path: Path
    name: str
    base_commit_oid: str
    base_tree_oid: str
    candidate_commit_oid: str
    candidate_tree_oid: str

    def read_base(self, repository: str) -> tuple[str, str]:
        if repository != self.name:
            raise AssertionError("Candidate reader requested another repository")
        return self.base_commit_oid, self.base_tree_oid


def run_git(repository: Path, *args: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ["git", "-C", str(repository), *args],
        check=True,
        capture_output=True,
    )


def run_git_input(
    repository: Path,
    *args: str,
    input_bytes: bytes,
) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ["git", "-C", str(repository), *args],
        input=input_bytes,
        check=True,
        capture_output=True,
    )


def encode_raw_path(raw_path: bytes) -> str:
    return base64.urlsafe_b64encode(raw_path).decode("ascii").rstrip("=")


def write_tree(
    repository: Path,
    entries: tuple[tuple[bytes, bytes], ...],
) -> str:
    records: list[bytes] = []
    for raw_path, content in sorted(entries):
        blob_oid = run_git_input(
            repository,
            "hash-object",
            "-w",
            "--stdin",
            input_bytes=content,
        ).stdout.strip()
        records.append(b"100644 blob " + blob_oid + b"\t" + raw_path + b"\x00")
    return run_git_input(
        repository,
        "mktree",
        "-z",
        input_bytes=b"".join(records),
    ).stdout.decode("ascii").strip()


def commit_tree(
    repository: Path,
    tree_oid: str,
    *,
    parent_oid: str | None,
    message: bytes,
) -> str:
    args = ["commit-tree", tree_oid]
    if parent_oid is not None:
        args.extend(("-p", parent_oid))
    return run_git_input(
        repository,
        *args,
        input_bytes=message,
    ).stdout.decode("ascii").strip()


def make_repository(
    tmp_path: Path,
    *,
    directory: str,
    base_entries: tuple[tuple[bytes, bytes], ...],
    candidate_entries: tuple[tuple[bytes, bytes], ...],
) -> GitRepositoryFixture:
    path = tmp_path / directory
    path.mkdir()
    run_git(path, "init", "-q")
    run_git(path, "config", "user.name", "GWO Test")
    run_git(path, "config", "user.email", "gwo@example.invalid")
    base_tree_oid = write_tree(path, base_entries)
    base_commit_oid = commit_tree(
        path,
        base_tree_oid,
        parent_oid=None,
        message=b"base\n",
    )
    candidate_tree_oid = write_tree(path, candidate_entries)
    candidate_commit_oid = commit_tree(
        path,
        candidate_tree_oid,
        parent_oid=base_commit_oid,
        message=b"candidate\n",
    )
    run_git(path, "update-ref", "refs/heads/candidate", candidate_commit_oid)
    return GitRepositoryFixture(
        path=path,
        name="owner/repository",
        base_commit_oid=base_commit_oid,
        base_tree_oid=base_tree_oid,
        candidate_commit_oid=candidate_commit_oid,
        candidate_tree_oid=candidate_tree_oid,
    )


def make_git_repository(tmp_path: Path) -> GitRepositoryFixture:
    return make_repository(
        tmp_path,
        directory="ordinary",
        base_entries=((b"main.py", b"print('base')\n"),),
        candidate_entries=((b"main.py", b"print('candidate')\n"),),
    )


def make_rename_repository(tmp_path: Path) -> GitRepositoryFixture:
    return make_repository(
        tmp_path,
        directory="rename",
        base_entries=((b"old.py", b"same\n"),),
        candidate_entries=((b"new.py", b"same\n"),),
    )


def make_non_utf8_path_repository(tmp_path: Path) -> GitRepositoryFixture:
    return make_repository(
        tmp_path,
        directory="non-utf8",
        base_entries=((b"old-\xff.txt", b"base\n"),),
        candidate_entries=((b"old-\xff.txt", b"candidate\n"),),
    )
~~~

- [ ] **Step 2: Prove RED**

~~~powershell
py -3.13 -m pytest tests/test_v8_candidate_git_readback.py -q
~~~

Expected: collection fails because candidate_git.GitCandidateReader and CandidateBasePort do not exist.

- [ ] **Step 3: Implement minimal raw Git reader**

Define:

~~~python
class CandidateBasePort(Protocol):
    def read_base(self, repository: str) -> tuple[str, str]:
        """Return the frozen base commit OID and base tree OID."""
        pass


class GitCandidateReader(CandidateReadbackPort):
    def __init__(
        self,
        *,
        repository_path: Path,
        base_reader: CandidateBasePort,
    ) -> None:
        self._repository_path = repository_path
        self._base_reader = base_reader

    def _git_bytes(self, *args: str) -> bytes:
        return subprocess.run(
            ["git", "-C", str(self._repository_path), *args],
            check=True,
            capture_output=True,
        ).stdout

    def _git_text(self, *args: str) -> str:
        return self._git_bytes(*args).decode("ascii").strip()

    def _read_tree(self, tree_oid: str) -> dict[bytes, tuple[str, str, str]]:
        result: dict[bytes, tuple[str, str, str]] = {}
        for record in self._git_bytes("ls-tree", "-rz", tree_oid).split(b"\0"):
            if not record:
                continue
            header, raw_path = record.split(b"\t", 1)
            mode, object_type, object_oid = header.split(b" ", 2)
            if raw_path in result:
                raise CandidateGateError(
                    "CANDIDATE_GATE_DIFF_INVALID",
                    "Git tree contains a duplicate raw path",
                )
            result[raw_path] = (
                mode.decode("ascii"),
                ("gitlink" if mode == b"160000" else object_type.decode("ascii")),
                object_oid.decode("ascii"),
            )
        return result

    def read_candidate(
        self,
        repository: str,
        reported_reference: str,
    ) -> CandidateReadback:
        base_commit_oid, base_tree_oid = self._base_reader.read_base(repository)
        candidate_commit_oid = self._git_text(
            "rev-parse", "--verify", f"{reported_reference}^{{commit}}"
        )
        candidate_tree_oid = self._git_text(
            "rev-parse", "--verify", f"{reported_reference}^{{tree}}"
        )
        base_entries = self._read_tree(base_tree_oid)
        candidate_entries = self._read_tree(candidate_tree_oid)
        diff_record = CandidateDiffRecordV1.from_tree_entries(
            repository_object_format=self._git_text("rev-parse", "--show-object-format"),
            base_commit_oid=base_commit_oid,
            base_tree_oid=base_tree_oid,
            candidate_commit_oid=candidate_commit_oid,
            candidate_tree_oid=candidate_tree_oid,
            base_entries=base_entries,
            candidate_entries=candidate_entries,
        )
        candidate = CandidateIdentity(
            reported_reference=reported_reference,
            base_commit_oid=base_commit_oid,
            base_tree_oid=base_tree_oid,
            candidate_commit_oid=candidate_commit_oid,
            candidate_tree_oid=candidate_tree_oid,
            changed_path_tokens=diff_record.changed_path_tokens,
        )
        confirmed_commit_oid = self._git_text(
            "rev-parse", "--verify", f"{reported_reference}^{{commit}}"
        )
        if confirmed_commit_oid != candidate_commit_oid:
            raise CandidateGateError(
                "CANDIDATE_GATE_READBACK_INVALID",
                "Candidate reference moved during authoritative readback",
            )
        return CandidateReadback(
            repository=repository,
            candidate=candidate,
            diff_record=diff_record,
        )
~~~

Use git rev-parse --verify reference^{commit}, reference^{tree}, and git ls-tree -rz. Parse raw bytes first, encode paths with base64.urlsafe_b64encode(raw).decode("ascii").rstrip("="), validate exact OIDs/modes/types, and sort by decoded raw bytes. Read base identity only from CandidateBasePort; never infer it from a Worker report or workspace head. Reject moving references, repository mismatch, non-commit references, malformed paths, duplicate entries, and digest mismatch.

- [ ] **Step 4: Prove GREEN**

~~~powershell
py -3.13 -m pytest tests/test_v8_candidate_git_readback.py tests/test_v8_candidate_gate.py tests/test_v8_candidate_gate_public.py -q
~~~

Expected: PASS; the one CandidateDiffRecordV1 is the record later used by all deterministic and Review consumers.

- [ ] **Step 5: Commit**

~~~powershell
py -3.13 scripts/sync_orchestrator.py
py -3.13 scripts/sync_orchestrator.py --check
git diff --check
git add skills/orchestrator/.skill-package.json skills/orchestrator/scripts/gwo_v8/candidate_gate.py skills/orchestrator/scripts/gwo_v8/candidate_git.py tests/test_v8_candidate_git_readback.py tests/test_v8_candidate_gate.py
git commit -m "feat: read exact Candidate trees from Git"
~~~

---

### Task 3: Add #114 Standard Assurance, ReviewSubject, and Accepted-Candidate Receipt

**Files**

- Modify: skills/orchestrator/scripts/gwo_v8/candidate_gate.py
- Create: tests/test_v8_candidate_assurance_standard.py
- Modify: tests/test_v8_candidate_gate.py and tests/test_v8_candidate_gate_public.py

**Interfaces**

- Consumes: Task 1–2 and existing PlanInvalidationReporter/capability proof.
- Produces: CandidateCheckEvidence, AssuranceRequirement, AssurancePolicy, ReviewSubject, ReviewAction, AcceptedCandidateReceipt, CandidateGate.gate_candidate(parent, reported_reference), and result fields candidate_receipt, candidate_diff_record, assurance_requirement, review_subject, accepted_candidate_receipt, and review_finding_ledger_digest.
- Ownership: remaining #114 CandidateGate work; `candidate_gate.py` owns the concrete InteractionClassification/InteractionKey and their diff-derived construction; do not modify execution_kernel.py. #116 only imports and consumes those values and does not redefine them.

- [ ] **Step 1: Write RED**

~~~python
def test_standard_gate_reads_once_and_runs_one_primary_review(gate_with_standard):
    gate, reader, reviewer, parent = gate_with_standard
    result = gate.gate_candidate(parent, "refs/heads/candidate")
    assert result.status == CandidateGateStatus.REVIEW_ACCEPTED
    assert reader.calls == [(parent.runtime_subject.repository, "refs/heads/candidate")]
    assert [action.kind for action in reviewer.actions] == ["formal_review"]
    assert result.candidate_receipt is not None
    assert result.accepted_candidate_receipt.candidate_receipt_digest == (
        result.candidate_receipt.digest
    )


def test_deterministic_failure_stops_before_reviewer(gate_with_failed_check):
    gate, reviewer, parent = gate_with_failed_check
    result = gate.gate_candidate(parent, "refs/heads/candidate")
    assert result.status == CandidateGateStatus.ORDINARY_REJECTED
    assert reviewer.actions == []


def test_no_review_allowlist_uses_zero_calls(no_review_gate):
    gate, reviewer, parent = no_review_gate
    result = gate.gate_candidate(parent, "refs/heads/candidate")
    assert result.status == CandidateGateStatus.REVIEW_ACCEPTED
    assert reviewer.actions == []


def test_accepted_candidate_receipt_matches_batch_handoff_fields(
    accepted_candidate_result,
):
    receipt = accepted_candidate_result.accepted_candidate_receipt
    assert set(receipt.canonical()) == {
        "kind",
        "repository",
        "campaign_key",
        "plan_revision_digest",
        "target_branch",
        "ticket_key",
        "work_run_key",
        "integration_node_key",
        "accepted_sequence",
        "base_sha",
        "base_tree_oid",
        "candidate_sha",
        "candidate_tree_oid",
        "candidate_receipt_digest",
        "diff_schema_version",
        "diff_record_digest",
        "authority_subtree_digest",
        "policy_witness_digest",
        "review_subject_digest",
        "assurance",
        "assurance_requirement_digest",
        "check_environment_digest",
        "delivery_identity_digest",
        "interaction_keys",
        "protected_surfaces",
        "gitlink_change",
        "evidence_digests",
        "review_finding_ledger_digest",
        "receipt_digest",
    }
    assert "result_digest" not in receipt.canonical()
    assert receipt.base_sha == accepted_candidate_result.candidate_receipt.base_commit_oid
    assert receipt.candidate_sha == accepted_candidate_result.candidate_receipt.candidate_commit_oid
    assert receipt.candidate_receipt_digest == accepted_candidate_result.candidate_receipt.digest


def test_interaction_keys_are_concrete_and_derived_from_candidate_diff(
    accepted_candidate_result,
):
    keys = accepted_candidate_result.accepted_candidate_receipt.interaction_keys
    assert all(type(key) is InteractionKey for key in keys)
    assert tuple(key.value for key in keys) == tuple(
        sorted(
            accepted_candidate_result.candidate_diff_record.changed_path_tokens
        )
    )
    assert all(key.namespace == "candidate-path" for key in keys)
    assert all(key.canonical()["classification"] in {
        "ordinary",
        "protected",
        "high_coupling",
        "non_decomposable",
    } for key in keys)


def test_gate_candidate_never_writes_kernel_state(gate_with_standard):
    gate, _reader, _reviewer, parent = gate_with_standard
    gate.gate_candidate(parent, "refs/heads/candidate")
    assert not hasattr(gate, "advance")
    assert not hasattr(gate, "persist_candidate_receipt")
~~~

- [ ] **Step 2: Prove RED**

~~~powershell
py -3.13 -m pytest tests/test_v8_candidate_assurance_standard.py -q
~~~

Expected: CandidateGate.gate_candidate or one of the exact assurance/review result fields is missing.

- [ ] **Step 3: Define exact assurance/review types**

Add these exact interfaces:

~~~python
class AssuranceMode(str, Enum):
    NO_REVIEW = "no_review"
    STANDARD = "standard"
    STRICT = "strict"


@dataclass(frozen=True, slots=True)
class CandidateCheckEvidence:
    check_id: str
    candidate_tree_oid: str
    outcome: str
    definition_digest: str
    observation_digest: str
    failure: DeterministicAuditFailure | None = None

    def __post_init__(self) -> None:
        _require_text(self.check_id, "check_id")
        _require_object_id(self.candidate_tree_oid, "candidate_tree_oid")
        if self.outcome not in {"passed", "failed"}:
            raise CandidateGateError(
                "CANDIDATE_GATE_CHECK_INVALID",
                "Candidate check outcome is outside the closed union",
            )
        _require_digest(self.definition_digest, "definition_digest")
        _require_digest(self.observation_digest, "observation_digest")
        if (self.outcome == "failed") != (self.failure is not None):
            raise CandidateGateError(
                "CANDIDATE_GATE_CHECK_INVALID",
                "failed Candidate check must carry one deterministic failure",
            )
        if self.failure is not None and type(
            self.failure
        ) is not DeterministicAuditFailure:
            raise CandidateGateError(
                "CANDIDATE_GATE_CHECK_INVALID",
                "Candidate check failure is not exact typed Evidence",
            )

    def canonical(self) -> dict[str, str | None]:
        return {
            "check_id": self.check_id,
            "candidate_tree_oid": self.candidate_tree_oid,
            "outcome": self.outcome,
            "definition_digest": self.definition_digest,
            "observation_digest": self.observation_digest,
            "failure_digest": (
                None if self.failure is None else self.failure.digest
            ),
        }

    @property
    def digest(self) -> str:
        return digest_value(self.canonical())


@dataclass(frozen=True, slots=True)
class AssuranceRequirement:
    policy_id: str
    policy_version: str
    mode: AssuranceMode
    required_check_ids: tuple[str, ...]
    standards: tuple[str, ...]
    specialist_policy_id: str | None = None
    requirement_digest: str | None = None

    def __post_init__(self) -> None:
        if type(self.mode) is not AssuranceMode:
            raise CandidateGateError(
                "CANDIDATE_GATE_ASSURANCE_INVALID",
                "AssuranceRequirement mode is not an exact AssuranceMode",
            )
        _require_text_tuple(self.required_check_ids, "required_check_ids")
        _require_text_tuple(self.standards, "standards")
        expected = digest_value(self._body())
        if self.requirement_digest is None:
            object.__setattr__(self, "requirement_digest", expected)
        else:
            _validate_stored_digest(
                self.requirement_digest,
                self._body(),
                code="CANDIDATE_GATE_ASSURANCE_INVALID",
                detail="AssuranceRequirement digest changed",
            )

    def _body(self) -> dict[str, object]:
        return {
            "kind": "assurance_requirement.v1",
            "policy_id": self.policy_id,
            "policy_version": self.policy_version,
            "mode": self.mode.value,
            "required_check_ids": list(self.required_check_ids),
            "standards": list(self.standards),
            "specialist_policy_id": self.specialist_policy_id,
        }

    @property
    def digest(self) -> str:
        return digest_value(self._body())

    def canonical(self) -> dict[str, object]:
        return {**self._body(), "requirement_digest": self.digest}


class CandidateCheckRunner(Protocol):
    def run(
        self,
        parent: CandidateGateParent,
        readback: CandidateReadback,
    ) -> tuple[CandidateCheckEvidence, ...]:
        pass


class AssurancePolicy(Protocol):
    def derive(
        self,
        parent: CandidateGateParent,
        readback: CandidateReadback,
        checks: tuple[CandidateCheckEvidence, ...],
    ) -> AssuranceRequirement:
        pass
~~~

Freeze the downstream handoff in the same task. The following field list is
identical to the #116/#117 Batch plan; do not add a Result field, a Batch
field, or a second Candidate receipt. #114 owns the concrete
`InteractionClassification` and `InteractionKey` below in `candidate_gate.py`.
Task #116 imports those exact values and consumes them; it must not redefine
either type in `batch_integrator.py`.

~~~python
from enum import Enum
from typing import Protocol


class InteractionClassification(str, Enum):
    ORDINARY = "ordinary"
    PROTECTED = "protected"
    HIGH_COUPLING = "high_coupling"
    NON_DECOMPOSABLE = "non_decomposable"


@dataclass(frozen=True, slots=True)
class InteractionKey:
    namespace: str
    value: str
    classification: InteractionClassification

    def __post_init__(self) -> None:
        _require_text(self.namespace, "interaction namespace")
        _require_text(self.value, "interaction value")
        if type(self.classification) is not InteractionClassification:
            raise CandidateGateError(
                "CANDIDATE_GATE_INTERACTION_INVALID",
                "interaction classification is outside the closed union",
            )

    @property
    def requires_singleton(self) -> bool:
        return self.classification is not InteractionClassification.ORDINARY

    def canonical(self) -> dict[str, str]:
        return {
            "namespace": self.namespace,
            "value": self.value,
            "classification": self.classification.value,
        }


def derive_interaction_keys(
    record: CandidateDiffRecordV1,
    *,
    protected_surfaces: tuple[str, ...],
) -> tuple[InteractionKey, ...]:
    protected = set(protected_surfaces)
    keys: list[InteractionKey] = []
    gitlink_paths = {
        token
        for entry in record.entries
        if entry.old_object_type == "gitlink" or entry.new_object_type == "gitlink"
        for token in (entry.old_path, entry.new_path)
        if token is not None
    }
    for token in record.changed_path_tokens:
        classification = (
            InteractionClassification.PROTECTED
            if token in protected
            else (
                InteractionClassification.HIGH_COUPLING
                if token in gitlink_paths
                else InteractionClassification.ORDINARY
            )
        )
        keys.append(InteractionKey("candidate-path", token, classification))
    return tuple(
        sorted(
            set(keys),
            key=lambda key: (
                key.namespace,
                key.value,
                key.classification.value,
            ),
        )
    )


def record_has_gitlink_change(record: CandidateDiffRecordV1) -> bool:
    return any(
        entry.old_object_type == "gitlink" or entry.new_object_type == "gitlink"
        for entry in record.entries
    )


class DigestEvidence(Protocol):
    @property
    def digest(self) -> str:
        pass


@dataclass(frozen=True, slots=True)
class CandidateAcceptanceFacts:
    target_branch: str
    integration_node_key: str
    accepted_sequence: int
    check_environment_digest: str
    delivery_identity_digest: str
    protected_surfaces: tuple[str, ...]

    def __post_init__(self) -> None:
        _require_text(self.target_branch, "target_branch")
        _require_text(self.integration_node_key, "integration_node_key")
        if type(self.accepted_sequence) is not int or self.accepted_sequence < 1:
            raise CandidateGateError(
                "CANDIDATE_GATE_ACCEPTANCE_INVALID",
                "accepted_sequence must be a positive integer",
            )
        _require_digest(self.check_environment_digest, "check_environment_digest")
        _require_digest(self.delivery_identity_digest, "delivery_identity_digest")
        _require_text_tuple(self.protected_surfaces, "protected_surfaces")
        if self.protected_surfaces != tuple(sorted(set(self.protected_surfaces))):
            raise CandidateGateError(
                "CANDIDATE_GATE_ACCEPTANCE_INVALID",
                "protected_surfaces must be sorted and unique",
            )


@dataclass(frozen=True, slots=True)
class AcceptedCandidateReceipt:
    repository: str
    campaign_key: str
    plan_revision_digest: str
    target_branch: str
    ticket_key: str
    work_run_key: str
    integration_node_key: str
    accepted_sequence: int
    base_sha: str
    base_tree_oid: str
    candidate_sha: str
    candidate_tree_oid: str
    candidate_receipt_digest: str
    diff_record_digest: str
    authority_subtree_digest: str
    policy_witness_digest: str
    review_subject_digest: str
    assurance: str
    assurance_requirement_digest: str
    check_environment_digest: str
    delivery_identity_digest: str
    interaction_keys: tuple[InteractionKey, ...]
    protected_surfaces: tuple[str, ...]
    gitlink_change: bool
    evidence_digests: tuple[str, ...]
    review_finding_ledger_digest: str
    diff_schema_version: str = "CandidateDiffRecordV1"

    def __post_init__(self) -> None:
        for field_name in (
            "repository",
            "campaign_key",
            "target_branch",
            "ticket_key",
            "work_run_key",
            "integration_node_key",
        ):
            _require_text(getattr(self, field_name), field_name)
        for field_name in (
            "plan_revision_digest",
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
            _require_digest(getattr(self, field_name), field_name)
        for field_name in ("base_sha", "base_tree_oid", "candidate_sha", "candidate_tree_oid"):
            _require_object_id(getattr(self, field_name), field_name)
        if self.diff_schema_version != "CandidateDiffRecordV1":
            raise CandidateGateError(
                "CANDIDATE_GATE_ACCEPTANCE_INVALID",
                "AcceptedCandidateReceipt diff schema is invalid",
            )
        if self.assurance not in {"standard", "strict"}:
            raise CandidateGateError(
                "CANDIDATE_GATE_ACCEPTANCE_INVALID",
                "assurance must be standard or strict",
            )
        if type(self.accepted_sequence) is not int or self.accepted_sequence < 1:
            raise CandidateGateError(
                "CANDIDATE_GATE_ACCEPTANCE_INVALID",
                "accepted_sequence must be a positive integer",
            )
        if type(self.interaction_keys) is not tuple or any(
            type(value) is not InteractionKey for value in self.interaction_keys
        ):
            raise CandidateGateError(
                "CANDIDATE_GATE_ACCEPTANCE_INVALID",
                "interaction_keys are not canonical InteractionKey values",
            )
        _require_text_tuple(self.protected_surfaces, "protected_surfaces")
        if self.protected_surfaces != tuple(sorted(set(self.protected_surfaces))):
            raise CandidateGateError(
                "CANDIDATE_GATE_ACCEPTANCE_INVALID",
                "protected_surfaces are not sorted and unique",
            )
        if type(self.gitlink_change) is not bool:
            raise CandidateGateError(
                "CANDIDATE_GATE_ACCEPTANCE_INVALID",
                "gitlink_change is not an exact boolean",
            )
        _require_digest_tuple(self.evidence_digests, "evidence_digests")

    def _body(self) -> dict[str, object]:
        return {
            "kind": "accepted_candidate_receipt.v1",
            "repository": self.repository,
            "campaign_key": self.campaign_key,
            "plan_revision_digest": self.plan_revision_digest,
            "target_branch": self.target_branch,
            "ticket_key": self.ticket_key,
            "work_run_key": self.work_run_key,
            "integration_node_key": self.integration_node_key,
            "accepted_sequence": self.accepted_sequence,
            "base_sha": self.base_sha,
            "base_tree_oid": self.base_tree_oid,
            "candidate_sha": self.candidate_sha,
            "candidate_tree_oid": self.candidate_tree_oid,
            "candidate_receipt_digest": self.candidate_receipt_digest,
            "diff_schema_version": self.diff_schema_version,
            "diff_record_digest": self.diff_record_digest,
            "authority_subtree_digest": self.authority_subtree_digest,
            "policy_witness_digest": self.policy_witness_digest,
            "review_subject_digest": self.review_subject_digest,
            "assurance": self.assurance,
            "assurance_requirement_digest": self.assurance_requirement_digest,
            "check_environment_digest": self.check_environment_digest,
            "delivery_identity_digest": self.delivery_identity_digest,
            "interaction_keys": [key.canonical() for key in self.interaction_keys],
            "protected_surfaces": list(self.protected_surfaces),
            "gitlink_change": self.gitlink_change,
            "evidence_digests": list(self.evidence_digests),
            "review_finding_ledger_digest": self.review_finding_ledger_digest,
        }

    @property
    def digest(self) -> str:
        return digest_value(self._body())

    def canonical(self) -> dict[str, object]:
        return {**self._body(), "receipt_digest": self.digest}
~~~

`AcceptedCandidateReceipt` deliberately has no `result_digest` and no
constructor-only `receipt_digest`; its `digest` property is the immutable
receipt identity and `canonical()` exposes that computed value. CandidateGate
builds it by copying `base_commit_oid -> base_sha`,
`candidate_commit_oid -> candidate_sha`, and `CandidateReceipt.digest ->
candidate_receipt_digest`, while taking the remaining delivery fields from
`CandidateAcceptanceFacts`, the exact Review Subject, Assurance Requirement,
complete Evidence tuple, and complete Finding ledger. #116 must consume this
exact field set and must not rename `base_sha`, `candidate_sha`,
`candidate_receipt_digest`, or `review_finding_ledger_digest`.

Rename the implementation of existing `FormalReviewRequest` to
`ReviewSubject`, then use this complete replacement. The compatibility alias
is assigned only after the class body.

~~~python
@dataclass(frozen=True, slots=True)
class ReviewSubject:
    parent_digest: str
    candidate_receipt_digest: str
    runtime_subject_digest: str
    candidate_digest: str
    candidate_audit_digest: str
    ticket_contract_digest: str
    policy_witness_digest: str
    base_commit_oid: str
    base_tree_oid: str
    candidate_commit_oid: str
    candidate_tree_oid: str
    diff_schema_version: str
    diff_record_digest: str
    standards: tuple[str, ...]
    check_evidence_digests: tuple[str, ...]
    assurance_requirement_digest: str
    protocol_version: str = "gwo.formal-review.v1"
    action_kind: str = "formal_review"
    prior_review_subject_digest: str | None = None
    repair_packet_digest: str | None = None
    repair_delta_digest: str | None = None
    subject_digest: str | None = None

    def __post_init__(self) -> None:
        for field_name in (
            "parent_digest",
            "candidate_receipt_digest",
            "runtime_subject_digest",
            "candidate_digest",
            "candidate_audit_digest",
            "ticket_contract_digest",
            "policy_witness_digest",
            "diff_record_digest",
            "assurance_requirement_digest",
        ):
            _require_digest(getattr(self, field_name), field_name)
        for field_name in (
            "base_commit_oid",
            "base_tree_oid",
            "candidate_commit_oid",
            "candidate_tree_oid",
        ):
            _require_object_id(getattr(self, field_name), field_name)
        if self.diff_schema_version != "CandidateDiffRecordV1":
            raise CandidateGateError(
                "CANDIDATE_GATE_REVIEW_SUBJECT_INVALID",
                "ReviewSubject diff schema is invalid",
            )
        _require_text_tuple(self.standards, "standards")
        _require_digest_tuple(
            self.check_evidence_digests,
            "check_evidence_digests",
            allow_empty=True,
        )
        _require_text(self.protocol_version, "protocol_version")
        if self.action_kind not in {"formal_review", "repair_verify"}:
            raise CandidateGateError(
                "CANDIDATE_GATE_REVIEW_SUBJECT_INVALID",
                "ReviewSubject action kind is outside the closed union",
            )
        repair_digests = (
            self.prior_review_subject_digest,
            self.repair_packet_digest,
            self.repair_delta_digest,
        )
        if self.action_kind == "repair_verify":
            if any(value is None for value in repair_digests):
                raise CandidateGateError(
                    "CANDIDATE_GATE_REVIEW_SUBJECT_INVALID",
                    "repair_verify subject lacks prior Subject, packet, or delta",
                )
            for value in repair_digests:
                _require_digest(value, "repair ReviewSubject digest")
        elif any(value is not None for value in repair_digests):
            raise CandidateGateError(
                "CANDIDATE_GATE_REVIEW_SUBJECT_INVALID",
                "initial ReviewSubject carries repair-only identity",
            )
        expected = digest_value(self._body())
        if self.subject_digest is None:
            object.__setattr__(self, "subject_digest", expected)
        else:
            _validate_stored_digest(
                self.subject_digest,
                self._body(),
                code="CANDIDATE_GATE_REVIEW_SUBJECT_INVALID",
                detail="ReviewSubject digest changed",
            )

    @classmethod
    def from_assurance(
        cls,
        *,
        parent: CandidateGateParent,
        candidate_receipt: CandidateReceipt,
        readback: CandidateReadback,
        audit: CandidateAuditReport,
        checks: tuple[CandidateCheckEvidence, ...],
        requirement: AssuranceRequirement,
    ) -> "ReviewSubject":
        return cls(
            parent_digest=parent.digest,
            candidate_receipt_digest=candidate_receipt.digest,
            runtime_subject_digest=parent.runtime_subject.digest,
            candidate_digest=readback.candidate.digest,
            candidate_audit_digest=audit.evidence.digest,
            ticket_contract_digest=parent.ticket_contract_digest,
            policy_witness_digest=parent.policy_witness_digest,
            base_commit_oid=readback.candidate.base_commit_oid,
            base_tree_oid=readback.candidate.base_tree_oid,
            candidate_commit_oid=readback.candidate.candidate_commit_oid,
            candidate_tree_oid=readback.candidate.candidate_tree_oid,
            diff_schema_version=readback.diff_record.schema_version,
            diff_record_digest=readback.diff_record.digest,
            standards=requirement.standards,
            check_evidence_digests=tuple(sorted(check.digest for check in checks)),
            assurance_requirement_digest=requirement.digest,
        )

    def _body(self) -> dict[str, object]:
        return {
            "kind": "review_subject.v1",
            "parent_digest": self.parent_digest,
            "candidate_receipt_digest": self.candidate_receipt_digest,
            "runtime_subject_digest": self.runtime_subject_digest,
            "candidate_digest": self.candidate_digest,
            "candidate_audit_digest": self.candidate_audit_digest,
            "ticket_contract_digest": self.ticket_contract_digest,
            "policy_witness_digest": self.policy_witness_digest,
            "base_commit_oid": self.base_commit_oid,
            "base_tree_oid": self.base_tree_oid,
            "candidate_commit_oid": self.candidate_commit_oid,
            "candidate_tree_oid": self.candidate_tree_oid,
            "diff_schema_version": self.diff_schema_version,
            "diff_record_digest": self.diff_record_digest,
            "standards": list(self.standards),
            "check_evidence_digests": list(self.check_evidence_digests),
            "assurance_requirement_digest": self.assurance_requirement_digest,
            "protocol_version": self.protocol_version,
            "action_kind": self.action_kind,
            "prior_review_subject_digest": self.prior_review_subject_digest,
            "repair_packet_digest": self.repair_packet_digest,
            "repair_delta_digest": self.repair_delta_digest,
        }

    @property
    def digest(self) -> str:
        assert self.subject_digest is not None
        return self.subject_digest

    def canonical(self) -> dict[str, object]:
        return {**self._body(), "subject_digest": self.digest}


FormalReviewRequest = ReviewSubject


@dataclass(frozen=True, slots=True)
class ReviewAction:
    kind: str
    subject: ReviewSubject
    runtime_subject_digest: str
    stable_action_id: str
    specialist_policy_id: str | None = None

    @classmethod
    def for_subject(
        cls,
        *,
        kind: str,
        subject: ReviewSubject,
        specialist_policy_id: str | None = None,
    ) -> "ReviewAction":
        if kind not in {"formal_review", "review_strong", "specialist_review"}:
            raise CandidateGateError(
                "CANDIDATE_GATE_REVIEW_SUBJECT_INVALID",
                "ReviewAction kind is outside the closed Review union",
            )
        if subject.action_kind != "formal_review":
            raise CandidateGateError(
                "CANDIDATE_GATE_REVIEW_SUBJECT_INVALID",
                "ReviewAction requires an initial formal-review Subject",
            )
        if (kind == "specialist_review") != (specialist_policy_id is not None):
            raise CandidateGateError(
                "CANDIDATE_GATE_REVIEW_SUBJECT_INVALID",
                "specialist policy identity does not match ReviewAction kind",
            )
        if specialist_policy_id is not None:
            _require_text(specialist_policy_id, "specialist_policy_id")
        return cls(
            kind=kind,
            subject=subject,
            runtime_subject_digest=subject.runtime_subject_digest,
            stable_action_id="review:" + digest_value(
                {
                    "kind": kind,
                    "subject_digest": subject.digest,
                    "specialist_policy_id": specialist_policy_id,
                }
            ),
            specialist_policy_id=specialist_policy_id,
        )

    @property
    def purpose(self) -> WorkRunPurpose:
        if self.kind == "formal_review":
            return WorkRunPurpose.formal_review()
        if self.kind == "review_strong":
            return WorkRunPurpose.invalid_review_payload_retry()
        assert self.specialist_policy_id is not None
        return WorkRunPurpose.specialist_review(self.specialist_policy_id)


class FormalReviewer(Protocol):
    def review(self, action: ReviewAction) -> FormalReviewResult:
        pass
~~~

AcceptedCandidateReceipt must bind the persisted CandidateReceipt through candidate_receipt_digest, the Candidate commit/tree, diff schema/digest, authority_subtree_digest, ReviewSubject digest, AssuranceRequirement digest, Evidence digests, all Batch delivery identity fields listed above, and its computed canonical receipt digest. It does not add a parent_digest or result_digest field; parent identity is already bound by CandidateReceipt.digest.

In the same task, replace the predecessor result field
`formal_review_request` with these exact fields and retain only a read-only
compatibility property:

~~~python
@dataclass(frozen=True, slots=True)
class CandidateGateResult:
    status: CandidateGateStatus
    evidence: tuple[object, ...]
    plan_invalidation_receipt: PlanInvalidationReceipt | None = None
    plan_invalidation_report: PlanInvalidationReport | None = None
    repair_packet: RepairPacket | None = None
    candidate_receipt: CandidateReceipt | None = None
    candidate_diff_record: CandidateDiffRecordV1 | None = None
    assurance_requirement: AssuranceRequirement | None = None
    review_subject: ReviewSubject | None = None
    accepted_candidate_receipt: AcceptedCandidateReceipt | None = None
    review_finding_ledger_digest: str | None = None

    def __post_init__(self) -> None:
        if type(self.status) is not CandidateGateStatus:
            raise CandidateGateError(
                "CANDIDATE_GATE_EVIDENCE_INVALID",
                "CandidateGate result status is outside the closed union",
            )
        if type(self.evidence) is not tuple or any(
            item is None for item in self.evidence
        ):
            raise CandidateGateError(
                "CANDIDATE_GATE_EVIDENCE_INVALID",
                "CandidateGate result Evidence is not an immutable tuple",
            )
        typed_optional = (
            (self.candidate_receipt, CandidateReceipt),
            (self.candidate_diff_record, CandidateDiffRecordV1),
            (self.assurance_requirement, AssuranceRequirement),
            (self.review_subject, ReviewSubject),
            (self.accepted_candidate_receipt, AcceptedCandidateReceipt),
        )
        if any(value is not None and type(value) is not expected for value, expected in typed_optional):
            raise CandidateGateError(
                "CANDIDATE_GATE_EVIDENCE_INVALID",
                "CandidateGate result contains a non-exact typed value",
            )
        if self.review_finding_ledger_digest is not None:
            _require_digest(
                self.review_finding_ledger_digest,
                "review_finding_ledger_digest",
            )

    @property
    def formal_review_request(self) -> ReviewSubject | None:
        return self.review_subject

    @property
    def receipt(self) -> PlanInvalidationReceipt | None:
        return self.plan_invalidation_receipt

    @property
    def classification(self) -> None:
        return None
~~~

Task 5 supplies the complete cross-field `__post_init__` body once all #114
branches exist. During this task, update every predecessor constructor call
from `formal_review_request=request` to `review_subject=request`.

- [ ] **Step 4: Implement minimal Standard gate_candidate**

Extend the existing `CandidateGate.__init__` with these keyword-only
dependencies; all remain optional only so the predecessor
`audit_candidate(parent, audit)` composition can still be constructed:

~~~python
def __init__(
    self,
    *,
    invalidation_reporter: PlanInvalidationReporter,
    candidate_reader: CandidateReadbackPort | None = None,
    formal_reviewer: FormalReviewer | None = None,
    repair_verifier: RepairVerifier | None = None,
    check_runner: CandidateCheckRunner | None = None,
    assurance_policy: AssurancePolicy | None = None,
    acceptance_facts: CandidateAcceptanceFacts | None = None,
) -> None:
    if not callable(
        getattr(invalidation_reporter, "report_plan_invalidation", None)
    ):
        raise CandidateGateError(
            "CANDIDATE_GATE_ADAPTER_INVALID",
            "CandidateGate requires an explicit Plan Invalidation reporter",
        )
    if candidate_reader is not None and not callable(
        getattr(candidate_reader, "read_candidate", None)
    ):
        raise CandidateGateError(
            "CANDIDATE_GATE_ADAPTER_INVALID",
            "Candidate readback port does not expose read_candidate",
        )
    if formal_reviewer is not None and not callable(
        getattr(formal_reviewer, "review", None)
    ):
        raise CandidateGateError(
            "CANDIDATE_GATE_ADAPTER_INVALID",
            "Formal Reviewer does not expose review",
        )
    if repair_verifier is not None and not callable(
        getattr(repair_verifier, "verify", None)
    ):
        raise CandidateGateError(
            "CANDIDATE_GATE_ADAPTER_INVALID",
            "Repair Verifier does not expose verify",
        )
    if check_runner is not None and not callable(
        getattr(check_runner, "run", None)
    ):
        raise CandidateGateError(
            "CANDIDATE_GATE_ADAPTER_INVALID",
            "Candidate check runner does not expose run",
        )
    if assurance_policy is not None and not callable(
        getattr(assurance_policy, "derive", None)
    ):
        raise CandidateGateError(
            "CANDIDATE_GATE_ADAPTER_INVALID",
            "Assurance policy does not expose derive",
        )
    if acceptance_facts is not None and type(
        acceptance_facts
    ) is not CandidateAcceptanceFacts:
        raise CandidateGateError(
            "CANDIDATE_GATE_ACCEPTANCE_INVALID",
            "Candidate acceptance facts are not exact",
        )
    self._invalidation_reporter = invalidation_reporter
    self._candidate_reader = candidate_reader
    self._formal_reviewer = formal_reviewer
    self._repair_verifier = repair_verifier
    self._check_runner = check_runner
    self._assurance_policy = assurance_policy
    self._acceptance_facts = acceptance_facts
~~~

The existing `audit_candidate(parent, audit)` compatibility signature remains
unchanged. The exact new method is:

~~~python
def gate_candidate(
    self,
    parent: CandidateGateParent,
    reported_reference: str,
) -> CandidateGateResult:
    reader = self._candidate_reader
    check_runner = self._check_runner
    assurance_policy = self._assurance_policy
    if reader is None or check_runner is None or assurance_policy is None:
        raise CandidateGateError(
            "CANDIDATE_GATE_ADAPTER_INVALID",
            "gate_candidate requires reader, checks, and Assurance policy",
        )
    readback = reader.read_candidate(
        parent.runtime_subject.repository,
        reported_reference,
    )
    receipt = CandidateReceipt.from_readback(
        parent=parent,
        reported_reference=reported_reference,
        readback=readback,
    )
    checks = check_runner.run(parent, readback)
    requirement = assurance_policy.derive(parent, readback, checks)
    audit = self._audit_readback(parent, readback, checks, requirement)
    result = self._audit_without_second_readback(
        parent,
        audit,
        receipt=receipt,
        readback=readback,
        checks=checks,
        requirement=requirement,
    )
    if result.status is not CandidateGateStatus.REVIEW_ACCEPTED:
        return replace(
            result,
            candidate_receipt=receipt,
            candidate_diff_record=readback.diff_record,
            assurance_requirement=requirement,
        )
    if result.review_subject is None or self._acceptance_facts is None:
        raise CandidateGateError(
            "CANDIDATE_GATE_ACCEPTANCE_INVALID",
            "accepted Candidate lacks Review Subject or delivery identity facts",
        )
    accepted = self._make_accepted_candidate_receipt(
        parent=parent,
        candidate_receipt=receipt,
        candidate_diff_record=readback.diff_record,
        review_subject=result.review_subject,
        assurance_requirement=requirement,
        evidence=result.evidence,
        review_finding_ledger_digest=result.review_finding_ledger_digest,
    )
    return replace(
        result,
        candidate_receipt=receipt,
        candidate_diff_record=readback.diff_record,
        assurance_requirement=requirement,
        accepted_candidate_receipt=accepted,
    )
~~~

Define `_make_accepted_candidate_receipt` in the same task with this concrete
body. `CandidateGateResult.review_finding_ledger_digest` is `None` for the
initial empty ledger and is filled by Task 7 for a complete ledger; the empty
ledger digest is deterministic and not a placeholder.

~~~python
def _make_accepted_candidate_receipt(
    self,
    *,
    parent: CandidateGateParent,
    candidate_receipt: CandidateReceipt,
    candidate_diff_record: CandidateDiffRecordV1,
    review_subject: ReviewSubject,
    assurance_requirement: AssuranceRequirement,
    evidence: tuple[DigestEvidence, ...],
    review_finding_ledger_digest: str | None,
) -> AcceptedCandidateReceipt:
    facts = self._acceptance_facts
    if facts is None:
        raise CandidateGateError(
            "CANDIDATE_GATE_ACCEPTANCE_INVALID",
            "CandidateGate acceptance facts are not configured",
        )
    evidence_digests = tuple(sorted({item.digest for item in evidence}))
    ledger_digest = review_finding_ledger_digest or digest_value(
        {"kind": "review_finding_ledger.v1", "entries": []}
    )
    return AcceptedCandidateReceipt(
        repository=candidate_receipt.repository,
        campaign_key=candidate_receipt.campaign_key,
        plan_revision_digest=candidate_receipt.plan_revision_digest,
        target_branch=facts.target_branch,
        ticket_key=candidate_receipt.ticket_key,
        work_run_key=candidate_receipt.work_run_key,
        integration_node_key=facts.integration_node_key,
        accepted_sequence=facts.accepted_sequence,
        base_sha=candidate_receipt.base_commit_oid,
        base_tree_oid=candidate_receipt.base_tree_oid,
        candidate_sha=candidate_receipt.candidate_commit_oid,
        candidate_tree_oid=candidate_receipt.candidate_tree_oid,
        candidate_receipt_digest=candidate_receipt.digest,
        diff_schema_version=candidate_receipt.diff_schema_version,
        diff_record_digest=candidate_receipt.diff_record_digest,
        authority_subtree_digest=candidate_receipt.authority_subtree_digest,
        policy_witness_digest=parent.policy_witness_digest,
        review_subject_digest=review_subject.digest,
        assurance=(
            "strict"
            if assurance_requirement.mode is AssuranceMode.STRICT
            else "standard"
        ),
        assurance_requirement_digest=assurance_requirement.digest,
        check_environment_digest=facts.check_environment_digest,
        delivery_identity_digest=facts.delivery_identity_digest,
        interaction_keys=derive_interaction_keys(
            candidate_diff_record,
            protected_surfaces=facts.protected_surfaces,
        ),
        protected_surfaces=facts.protected_surfaces,
        gitlink_change=record_has_gitlink_change(candidate_diff_record),
        evidence_digests=evidence_digests,
        review_finding_ledger_digest=ledger_digest,
    )
~~~

Add these exact helper bodies. A failed `CandidateCheckEvidence` carries the
already-typed `DeterministicAuditFailure`; this avoids inventing a second audit
port and lets one `CandidateDiffRecordV1` feed every consumer.

~~~python
def _audit_readback(
    self,
    parent: CandidateGateParent,
    readback: CandidateReadback,
    checks: tuple[CandidateCheckEvidence, ...],
    requirement: AssuranceRequirement,
) -> CandidateAuditReport:
    if any(check.candidate_tree_oid != readback.candidate.candidate_tree_oid for check in checks):
        raise CandidateGateError(
            "CANDIDATE_GATE_EVIDENCE_STALE",
            "Candidate check Evidence is bound to another Candidate tree",
        )
    failures = tuple(
        sorted(
            (
                check.failure
                for check in checks
                if check.failure is not None
            ),
            key=lambda failure: failure.digest,
        )
    )
    return CandidateAuditReport(
        parent_digest=parent.digest,
        candidate=readback.candidate,
        failures=failures,
        diff_record=readback.diff_record,
        standards=requirement.standards,
        check_evidence_digests=tuple(sorted(check.digest for check in checks)),
        assurance_requirement=requirement.digest,
    )


def _review_primary(
    self,
    parent: CandidateGateParent,
    subject: ReviewSubject,
) -> FormalReviewResult:
    reviewer = self._formal_reviewer
    if reviewer is None:
        raise CandidateGateError(
            "CANDIDATE_GATE_REVIEWER_UNAVAILABLE",
            "Standard Assurance requires the CandidateGate Formal Reviewer",
        )
    self._validate_read_only_port(reviewer, "Formal Reviewer")
    action = ReviewAction.for_subject(kind="formal_review", subject=subject)
    result = reviewer.review(action)
    self._validate_review_result(parent, subject, result)
    return result


def _audit_without_second_readback(
    self,
    parent: CandidateGateParent,
    audit: CandidateAuditReport,
    *,
    receipt: CandidateReceipt,
    readback: CandidateReadback,
    checks: tuple[CandidateCheckEvidence, ...],
    requirement: AssuranceRequirement,
) -> CandidateGateResult:
    self._validate_parent(parent)
    self._validate_audit(parent, audit)
    candidate_evidence = audit.evidence
    invalidating = tuple(
        failure
        for failure in audit.failures
        if failure.route is AuditFailureRoute.TICKET_UNSATISFIABLE
    )
    if invalidating:
        plan_evidence = self._plan_evidence_from_audit(
            parent,
            audit,
            invalidating,
        )
        invalidation_receipt, report = self._report_invalidation(
            parent,
            plan_evidence,
        )
        return CandidateGateResult(
            status=CandidateGateStatus.PLAN_INVALIDATION_REPORTED,
            evidence=(candidate_evidence, plan_evidence),
            plan_invalidation_receipt=invalidation_receipt,
            plan_invalidation_report=report,
        )
    if audit.failures:
        return CandidateGateResult(
            status=CandidateGateStatus.ORDINARY_REJECTED,
            evidence=(candidate_evidence,),
        )
    subject = ReviewSubject.from_assurance(
        parent=parent,
        candidate_receipt=receipt,
        readback=readback,
        audit=audit,
        checks=checks,
        requirement=requirement,
    )
    if requirement.mode is AssuranceMode.NO_REVIEW:
        return CandidateGateResult(
            status=CandidateGateStatus.REVIEW_ACCEPTED,
            evidence=(candidate_evidence,),
            review_subject=subject,
        )
    if requirement.mode is AssuranceMode.STRICT:
        raise CandidateGateError(
            "CANDIDATE_GATE_ASSURANCE_INVALID",
            "Strict Assurance is enabled only after the Task 6 matrix lands",
        )
    review_result = self._review_primary(parent, subject)
    findings = tuple(sorted(review_result.findings, key=lambda finding: finding.digest))
    scope_findings = tuple(finding for finding in findings if finding.scope_escape)
    if scope_findings:
        plan_evidence = self._plan_evidence_from_findings(
            parent,
            audit,
            findings,
        )
        invalidation_receipt, report = self._report_invalidation(
            parent,
            plan_evidence,
        )
        return CandidateGateResult(
            status=CandidateGateStatus.PLAN_INVALIDATION_REPORTED,
            evidence=(candidate_evidence, *findings, plan_evidence),
            plan_invalidation_receipt=invalidation_receipt,
            plan_invalidation_report=report,
            review_subject=subject,
        )
    hard_findings = tuple(
        finding for finding in findings if finding.severity == "hard"
    )
    if hard_findings:
        packet = RepairPacket.from_findings(
            parent,
            audit.candidate,
            subject,
            hard_findings,
        )
        return CandidateGateResult(
            status=CandidateGateStatus.REPAIR_REQUIRED,
            evidence=(candidate_evidence, *findings),
            repair_packet=packet,
            review_subject=subject,
        )
    return CandidateGateResult(
        status=CandidateGateStatus.REVIEW_ACCEPTED,
        evidence=(candidate_evidence, *findings),
        review_subject=subject,
    )
~~~

The existing `audit_candidate(parent, audit)` remains the compatibility path
for already-read tests and #137. It does not call `gate_candidate`, and
`gate_candidate` never calls `_read_authoritative_candidate`; therefore the
new path performs exactly one `CandidateReadbackPort.read_candidate` call.

- [ ] **Step 5: Prove GREEN**

~~~powershell
py -3.13 -m pytest tests/test_v8_candidate_assurance_standard.py -q
py -3.13 -m pytest tests/test_v8_candidate_gate.py tests/test_v8_candidate_gate_public.py -q
py -3.13 -m pytest tests/test_v8_candidate_receipt_kernel.py -q
~~~

Expected: PASS; #114 consumes no Watchdog type, but this implementation starts only after #113 has merged and released the shared package-manifest lane.

- [ ] **Step 6: Commit**

~~~powershell
py -3.13 scripts/sync_orchestrator.py
py -3.13 scripts/sync_orchestrator.py --check
git diff --check
git add skills/orchestrator/.skill-package.json skills/orchestrator/scripts/gwo_v8/candidate_gate.py tests/test_v8_candidate_assurance_standard.py tests/test_v8_candidate_gate.py tests/test_v8_candidate_gate_public.py
git commit -m "feat: gate standard Candidates through one readback"
~~~


### Task 4: Make Formal Review Reuse Artifact-Backed and Capability-Proven

**Files**

- Modify: skills/orchestrator/scripts/gwo_v8/candidate_gate.py
- Create: tests/test_v8_candidate_review_reuse.py

**Interfaces**

- Consumes: ReviewSubject, ReviewAction, CandidateDiffRecordV1, CandidateGate result, and CapabilityPolicyProof.
- Produces: CandidateDiffArtifactStore and CandidateGate.reuse_formal_review(subject, result).
- Ownership: #114 only; no execution_kernel.py or #113 files.

- [ ] **Step 1: Write RED**

~~~python
def test_reuse_requires_identical_subject_and_revalidated_diff(review_reuse_gate):
    gate, subject, result, store = review_reuse_gate
    assert gate.reuse_formal_review(subject=subject, result=result) == result
    assert store.reads == 1


@pytest.mark.parametrize("field", [
    "candidate_tree_oid",
    "diff_record_digest",
    "policy_witness_digest",
    "assurance_requirement_digest",
    "protocol_version",
    "action_kind",
])
def test_changed_subject_fails_closed_before_reviewer(review_reuse_gate, field):
    gate, subject, result, _store = review_reuse_gate
    with pytest.raises(CandidateGateError) as raised:
        gate.reuse_formal_review(
            subject=replace_subject_field(subject, field),
            result=result,
        )
    assert raised.value.code == "CANDIDATE_GATE_REVIEW_REUSE_INVALID"


def test_missing_or_changed_diff_fails_before_reviewer(review_reuse_gate):
    gate, subject, result, store = review_reuse_gate
    store.corrupt(subject.diff_record_digest)
    with pytest.raises(CandidateGateError) as raised:
        gate.reuse_formal_review(subject=subject, result=result)
    assert raised.value.code == "CANDIDATE_GATE_DIFF_ARTIFACT_INVALID"
    assert gate.reviewer_calls == 0
~~~

Define:

~~~python
class CandidateDiffArtifactStore(Protocol):
    def put(self, record: CandidateDiffRecordV1) -> str:
        pass

    def read(self, digest: str) -> CandidateDiffRecordV1:
        pass
~~~

- [ ] **Step 2: Prove RED**

~~~powershell
py -3.13 -m pytest tests/test_v8_candidate_review_reuse.py -q
~~~

Expected: CandidateDiffArtifactStore or reuse_formal_review is missing.

- [ ] **Step 3: Implement minimal reuse**

Extend the existing keyword-only constructor with
`diff_artifacts: CandidateDiffArtifactStore | None = None` and execute this
exact validation/storage body before assigning the dependency:

~~~python
if diff_artifacts is not None and (
    not callable(getattr(diff_artifacts, "put", None))
    or not callable(getattr(diff_artifacts, "read", None))
):
    raise CandidateGateError(
        "CANDIDATE_GATE_ADAPTER_INVALID",
        "Candidate diff Artifact Store lacks put/read",
    )
self._diff_artifacts = diff_artifacts
~~~

After the one authoritative read in `gate_candidate`, persist and read back
the exact record with this helper; pass the returned record to checks,
Assurance, Review, interaction-key derivation, and receipt creation:

~~~python
def _store_candidate_diff(
    self,
    record: CandidateDiffRecordV1,
) -> CandidateDiffRecordV1:
    store = self._diff_artifacts
    if store is None:
        raise CandidateGateError(
            "CANDIDATE_GATE_DIFF_ARTIFACT_INVALID",
            "Candidate diff Artifact Store is not configured",
        )
    stored_digest = store.put(record)
    if stored_digest != record.digest:
        raise CandidateGateError(
            "CANDIDATE_GATE_DIFF_ARTIFACT_INVALID",
            "Candidate diff Artifact Store changed the record digest",
        )
    persisted = store.read(stored_digest)
    if type(persisted) is not CandidateDiffRecordV1 or persisted != record:
        raise CandidateGateError(
            "CANDIDATE_GATE_DIFF_ARTIFACT_INVALID",
            "Candidate diff Artifact readback changed the complete record",
        )
    return persisted
~~~

Replace the local `readback` immediately after the reader call:

~~~python
stored_record = self._store_candidate_diff(readback.diff_record)
readback = replace(
    readback,
    diff_record=stored_record,
    readback_digest=None,
)
~~~

Use this concrete CandidateGate reuse branch:

~~~python
def reuse_formal_review(
    self,
    *,
    subject: ReviewSubject,
    result: CandidateGateResult,
) -> CandidateGateResult:
    store = self._diff_artifacts
    if store is None:
        raise CandidateGateError(
            "CANDIDATE_GATE_DIFF_ARTIFACT_INVALID",
            "Candidate diff Artifact Store is not configured",
        )
    record = store.read(subject.diff_record_digest)
    if type(record) is not CandidateDiffRecordV1 or record.digest != subject.diff_record_digest:
        raise CandidateGateError(
            "CANDIDATE_GATE_DIFF_ARTIFACT_INVALID",
            "stored Candidate diff is missing or digest-invalid",
        )
    if result.review_subject is None or result.review_subject.digest != subject.digest:
        raise CandidateGateError(
            "CANDIDATE_GATE_REVIEW_REUSE_INVALID",
            "Review reuse Subject identity changed",
        )
    if result.candidate_diff_record != record:
        raise CandidateGateError(
            "CANDIDATE_GATE_REVIEW_REUSE_INVALID",
            "Review result is not bound to the stored complete diff",
        )
    self._validate_read_only_port(self._formal_reviewer, "Formal Reviewer")
    return result
~~~

The branch above performs no Reviewer call. Keep the existing concrete
`_validate_read_only_port` body as the capability gate for Reviewer and
RepairVerifier; its `CapabilityPolicyProof.capability_policy.is_proven`
readback proves read-only/no-delegation before a result is trusted. Do not put
ambient skill names into Evidence identity.

- [ ] **Step 4: Prove GREEN and commit**

~~~powershell
py -3.13 -m pytest tests/test_v8_candidate_review_reuse.py tests/test_v8_candidate_assurance_standard.py -q
py -3.13 scripts/sync_orchestrator.py
py -3.13 scripts/sync_orchestrator.py --check
git diff --check
git add skills/orchestrator/.skill-package.json skills/orchestrator/scripts/gwo_v8/candidate_gate.py tests/test_v8_candidate_review_reuse.py
git commit -m "feat: bind Review reuse to Candidate diff Artifacts"
~~~

---

### Task 5: Close #114 and Preserve the #137 Plan Invalidation Boundary

**Files**

- Modify: skills/orchestrator/scripts/gwo_v8/candidate_gate.py
- Create: tests/test_v8_candidate_gate_acceptance.py
- Modify: tests/test_v8_candidate_gate.py

**Interfaces**

- Consumes: Tasks 1–4.
- Produces: complete #114 CandidateGateResult with private receipt, one diff record, deterministic Evidence, AssuranceRequirement, ReviewSubject, accepted-Candidate receipt, and existing Plan Invalidation readback.
- Ownership: #114 only; no execution_kernel.py edits.

- [ ] **Step 1: Write RED**

~~~python
def test_candidate_result_distinguishes_private_and_accepted_receipts(
    accepted_candidate_result,
):
    assert accepted_candidate_result.candidate_receipt is not None
    assert accepted_candidate_result.accepted_candidate_receipt is not None
    assert accepted_candidate_result.accepted_candidate_receipt.candidate_receipt_digest == (
        accepted_candidate_result.candidate_receipt.digest
    )


def test_scope_escape_routes_plan_invalidation_without_classification(
    scope_escape_result,
):
    assert scope_escape_result.status == CandidateGateStatus.PLAN_INVALIDATION_REPORTED
    assert scope_escape_result.classification is None
    assert scope_escape_result.plan_invalidation_report is not None


def test_mismatched_authoritative_readback_is_rejected(gate_with_mismatch):
    gate, parent = gate_with_mismatch
    with pytest.raises(CandidateGateError) as raised:
        gate.gate_candidate(parent, "refs/heads/candidate")
    assert raised.value.code == "CANDIDATE_GATE_EVIDENCE_STALE"
~~~

- [ ] **Step 2: Prove RED**

~~~powershell
py -3.13 -m pytest tests/test_v8_candidate_gate_acceptance.py -q
~~~

Expected: result fields or exact #137 boundary assertions fail.

- [ ] **Step 3: Implement final #114 result fields**

Expand the Task 3 `CandidateGateResult.__post_init__` with these exact
cross-field branches:

~~~python
has_invalidation = self.status is CandidateGateStatus.PLAN_INVALIDATION_REPORTED
if has_invalidation != (
    self.plan_invalidation_receipt is not None
    and self.plan_invalidation_report is not None
):
    raise CandidateGateError(
        "CANDIDATE_GATE_EVIDENCE_INVALID",
        "Plan Invalidation status and readback pair do not match",
    )
if self.status in {
    CandidateGateStatus.ORDINARY_REJECTED,
    CandidateGateStatus.PLAN_INVALIDATION_REPORTED,
} and (
    self.review_subject is not None
    or self.accepted_candidate_receipt is not None
):
    raise CandidateGateError(
        "CANDIDATE_GATE_EVIDENCE_INVALID",
        "deterministic stop carries Review-only identity",
    )
if self.accepted_candidate_receipt is not None:
    if (
        self.status not in {
            CandidateGateStatus.REVIEW_ACCEPTED,
            CandidateGateStatus.REPAIR_ACCEPTED,
        }
        or self.candidate_receipt is None
        or self.candidate_diff_record is None
        or self.assurance_requirement is None
        or self.review_subject is None
        or self.accepted_candidate_receipt.candidate_receipt_digest
        != self.candidate_receipt.digest
        or self.accepted_candidate_receipt.diff_record_digest
        != self.candidate_diff_record.digest
        or self.accepted_candidate_receipt.review_subject_digest
        != self.review_subject.digest
        or self.accepted_candidate_receipt.assurance_requirement_digest
        != self.assurance_requirement.digest
    ):
        raise CandidateGateError(
            "CANDIDATE_GATE_ACCEPTANCE_INVALID",
            "accepted Candidate receipt is not bound to complete #114 identity",
        )
if (
    self.status in {
        CandidateGateStatus.REVIEW_ACCEPTED,
        CandidateGateStatus.REPAIR_ACCEPTED,
    }
    and self.candidate_receipt is not None
    and self.accepted_candidate_receipt is None
):
    raise CandidateGateError(
        "CANDIDATE_GATE_ACCEPTANCE_INVALID",
        "public accepted Candidate lacks the delivery receipt",
    )
~~~

In `gate_candidate`, fail closed before `_audit_readback` when the typed
readback does not bind the requested repository/reference; the exact branch
is:

~~~python
if (
    readback.repository != parent.runtime_subject.repository
    or readback.candidate.reported_reference != reported_reference
):
    raise CandidateGateError(
        "CANDIDATE_GATE_EVIDENCE_STALE",
        "authoritative Candidate readback changed repository or reference",
    )
~~~

Keep the Plan Invalidation branch exactly as implemented in Task 3: it calls
only `_report_invalidation(parent, plan_evidence)`, then constructs a result
whose `classification` property returns `None`. Update predecessor
`audit_candidate` constructors to use `review_subject`; do not add an Issue,
membership, successor-revision, or authority-writing call. Keep the existing
`verify_repair(parent, packet, candidate)` signature as the #115 boundary; its
Task 9 body never calls `gate_candidate`.

- [ ] **Step 4: Prove GREEN and commit**

~~~powershell
py -3.13 -m pytest tests/test_v8_candidate_gate_acceptance.py tests/test_v8_candidate_gate.py tests/test_v8_candidate_gate_public.py -q
py -3.13 -m pytest tests/test_v8_candidate_receipt_kernel.py -q
py -3.13 scripts/sync_orchestrator.py
py -3.13 scripts/sync_orchestrator.py --check
git diff --check
git add skills/orchestrator/.skill-package.json skills/orchestrator/scripts/gwo_v8/candidate_gate.py tests/test_v8_candidate_gate_acceptance.py tests/test_v8_candidate_gate.py
git commit -m "feat: close the CandidateGate acceptance boundary"
~~~

Expected: #114 has no semantic dependency on a Watchdog projection, while its PR is still sequenced after #113 because both must commit the generated orchestrator manifest.

---

### Task 6: Implement #115 Strict Review and Same-Subject Invalid-Transport Retry

**Files**

- Modify: skills/orchestrator/scripts/gwo_v8/candidate_gate.py
- Create: tests/test_v8_candidate_strict_review.py

**Interfaces**

- Consumes: #114 AssuranceRequirement, ReviewSubject, ReviewAction, FormalReviewer, and accepted receipt.
- Produces: Strict action matrix, specialist/human Decision seam, and InvalidReviewTransport plus one review_strong retry.
- Ownership: #115 CandidateGate only; do not modify execution_kernel.py.

- [ ] **Step 1: Write RED**

~~~python
def test_strict_uses_primary_then_at_most_one_specialist(strict_gate):
    gate, reviewer, parent = strict_gate
    result = gate.gate_candidate(parent, "refs/heads/candidate")
    assert result.status == CandidateGateStatus.REVIEW_ACCEPTED
    assert [action.kind for action in reviewer.actions] == [
        "formal_review",
        "specialist_review",
    ]


def test_strict_without_specialist_returns_typed_decision(strict_decision_gate):
    gate, reviewer, parent = strict_decision_gate
    result = gate.gate_candidate(parent, "refs/heads/candidate")
    assert result.status == CandidateGateStatus.DECISION_REQUIRED
    assert reviewer.actions == []


def test_invalid_transport_retries_same_subject_as_review_strong(invalid_transport_gate):
    gate, reviewer, parent = invalid_transport_gate
    gate.gate_candidate(parent, "refs/heads/candidate")
    assert [action.kind for action in reviewer.actions] == [
        "formal_review",
        "review_strong",
    ]
    assert reviewer.actions[0].subject.digest == reviewer.actions[1].subject.digest


def test_valid_rejection_does_not_repeat_unchanged_subject(rejected_gate):
    gate, reviewer, parent = rejected_gate
    gate.gate_candidate(parent, "refs/heads/candidate")
    gate.gate_candidate(parent, "refs/heads/candidate")
    assert len(reviewer.actions) == 1
~~~

- [ ] **Step 2: Prove RED**

~~~powershell
py -3.13 -m pytest tests/test_v8_candidate_strict_review.py -q
~~~

Expected: wrong action count or missing DECISION_REQUIRED, specialist_review, review_strong, or same-subject behavior.

- [ ] **Step 3: Implement the closed matrix**

Add the status and typed transport error, then initialize the Subject-keyed
cache once in `CandidateGate.__init__`:

~~~python
# Add to CandidateGateStatus.
DECISION_REQUIRED = "decision_required"


class InvalidReviewTransport(RuntimeError):
    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.code = "INVALID_REVIEW_TRANSPORT"
        self.detail = detail


# CandidateGate.__init__
self._review_results: dict[str, FormalReviewResult] = {}
~~~

Use these exact helpers for all Standard/Strict calls:

~~~python
def _invoke_review_action(
    self,
    parent: CandidateGateParent,
    action: ReviewAction,
) -> FormalReviewResult:
    reviewer = self._formal_reviewer
    if reviewer is None:
        raise CandidateGateError(
            "CANDIDATE_GATE_REVIEWER_UNAVAILABLE",
            "Assurance requires the CandidateGate Formal Reviewer",
        )
    self._validate_read_only_port(reviewer, "Formal Reviewer")
    result = reviewer.review(action)
    self._validate_review_result(parent, action.subject, result)
    return result


def _review_with_transport_retry(
    self,
    parent: CandidateGateParent,
    action: ReviewAction,
) -> FormalReviewResult:
    try:
        return self._invoke_review_action(parent, action)
    except InvalidReviewTransport:
        retry = ReviewAction.for_subject(
            kind="review_strong",
            subject=action.subject,
        )
        if retry.subject.digest != action.subject.digest:
            raise CandidateGateError(
                "CANDIDATE_GATE_REVIEW_SUBJECT_INVALID",
                "review_strong retry changed ReviewSubject identity",
            )
        return self._invoke_review_action(parent, retry)


def _merge_review_results(
    self,
    subject: ReviewSubject,
    results: tuple[FormalReviewResult, ...],
) -> FormalReviewResult:
    by_id: dict[str, FormalReviewFinding] = {}
    for result in results:
        for finding in result.findings:
            prior = by_id.get(finding.finding_id)
            if prior is not None and prior.digest != finding.digest:
                raise CandidateGateError(
                    "CANDIDATE_GATE_EVIDENCE_INVALID",
                    "Reviewers returned conflicting Findings with one ID",
                )
            by_id[finding.finding_id] = finding
    return FormalReviewResult(
        subject_digest=subject.digest,
        findings=tuple(by_id[key] for key in sorted(by_id)),
    )


def _run_assurance_review(
    self,
    parent: CandidateGateParent,
    subject: ReviewSubject,
    requirement: AssuranceRequirement,
) -> FormalReviewResult | None:
    cached = self._review_results.get(subject.digest)
    if cached is not None:
        self._validate_review_result(parent, subject, cached)
        return cached
    if requirement.mode is AssuranceMode.NO_REVIEW:
        return FormalReviewResult(subject_digest=subject.digest, findings=())
    if (
        requirement.mode is AssuranceMode.STRICT
        and requirement.specialist_policy_id is None
    ):
        return None
    primary = self._review_with_transport_retry(
        parent,
        ReviewAction.for_subject(kind="formal_review", subject=subject),
    )
    results = [primary]
    if requirement.mode is AssuranceMode.STRICT:
        assert requirement.specialist_policy_id is not None
        results.append(
            self._review_with_transport_retry(
                parent,
                ReviewAction.for_subject(
                    kind="specialist_review",
                    subject=subject,
                    specialist_policy_id=requirement.specialist_policy_id,
                ),
            )
        )
    merged = self._merge_review_results(subject, tuple(results))
    self._review_results[subject.digest] = merged
    return merged
~~~

Replace the Task 3 Strict error and primary-review call in
`_audit_without_second_readback` with:

~~~python
review_result = self._run_assurance_review(parent, subject, requirement)
if review_result is None:
    return CandidateGateResult(
        status=CandidateGateStatus.DECISION_REQUIRED,
        evidence=(candidate_evidence,),
        review_subject=subject,
    )
~~~

The subsequent Finding branches consume `review_result` unchanged. A retry
uses `WorkRunPurpose.invalid_review_payload_retry()` through
`ReviewAction.purpose`, carries the identical `ReviewSubject.digest`, and does
not call `CandidateReadbackPort`; therefore it consumes no Candidate
submission. The cache is populated only after a typed, capability-proven
result, so a valid hard rejection is not reviewed again for the same Subject.

- [ ] **Step 4: Prove GREEN and commit**

~~~powershell
py -3.13 -m pytest tests/test_v8_candidate_strict_review.py tests/test_v8_candidate_assurance_standard.py -q
py -3.13 scripts/sync_orchestrator.py
py -3.13 scripts/sync_orchestrator.py --check
git diff --check
git add skills/orchestrator/.skill-package.json skills/orchestrator/scripts/gwo_v8/candidate_gate.py tests/test_v8_candidate_strict_review.py
git commit -m "feat: bound Strict Review actions"
~~~

---

### Task 7: Add Complete Review Finding Ledger and Repair Packet

**Files**

- Modify: skills/orchestrator/scripts/gwo_v8/candidate_gate.py
- Create: tests/test_v8_review_finding_ledger.py
- Create: tests/test_v8_repair_packet.py

**Interfaces**

- Consumes: ReviewSubject, existing FormalReviewFinding/FormalReviewResult, CandidateReceipt, and CandidateDiffRecordV1.
- Produces: ReviewFindingDisposition, ReviewFindingLedgerEntry, ReviewFindingLedger, and complete RepairPacket.
- Ownership: #115 CandidateGate only; no Kernel or Watchdog writes.

- [ ] **Step 1: Write RED**

~~~python
def test_ledger_preserves_all_findings_with_unresolved_dispositions(review_result):
    ledger = ReviewFindingLedger.from_review_result(review_result)
    assert tuple(entry.finding.finding_id for entry in ledger.entries) == (
        "finding:authority",
        "finding:test",
    )
    assert all(
        entry.disposition is ReviewFindingDisposition.UNRESOLVED
        for entry in ledger.entries
    )


def test_packet_contains_ledger_scope_checks_protocol_and_instructions(rejected):
    packet = RepairPacket.from_review(
        parent=rejected.parent,
        candidate_receipt=rejected.candidate_receipt,
        subject=rejected.subject,
        result=rejected.result,
        allowed_path_tokens=("c3JjL21haW4ucHk",),
        required_check_ids=("unit", "typecheck"),
        repair_instructions=("fix named findings only",),
    )
    assert packet.finding_ledger.entries
    assert packet.required_disposition_ids == (
        "finding:authority",
        "finding:test",
    )
    assert packet.required_check_ids == ("typecheck", "unit")
    assert packet.protocol_version == "gwo.formal-review.v1"
    assert packet.repair_instructions == ("fix named findings only",)


def test_packet_rejects_truncated_ledger(rejected):
    packet = make_repair_packet(rejected)
    with pytest.raises(CandidateGateError) as raised:
        packet.with_ledger(packet.finding_ledger.entries[:1])
    assert raised.value.code == "CANDIDATE_GATE_REPAIR_PACKET_INVALID"
~~~

- [ ] **Step 2: Prove RED**

~~~powershell
py -3.13 -m pytest tests/test_v8_review_finding_ledger.py tests/test_v8_repair_packet.py -q
~~~

Expected: ledger or expanded RepairPacket contract is missing, or old packet omits advisory/required Findings.

- [ ] **Step 3: Implement exact ledger and packet**

Rename the existing complete `FormalReviewFinding` implementation to
`ReviewFinding`, then assign `FormalReviewFinding = ReviewFinding` after that
class. Add these complete ledger bodies:

~~~python
FormalReviewFinding = ReviewFinding


class ReviewFindingDisposition(str, Enum):
    UNRESOLVED = "unresolved"
    FIXED = "fixed"
    ACCEPTED_RISK = "accepted_risk"
    NOT_REPRODUCED = "not_reproduced"
    SCOPE_ESCAPED = "scope_escaped"


@dataclass(frozen=True, slots=True)
class ReviewFindingLedgerEntry:
    finding: ReviewFinding
    disposition: ReviewFindingDisposition
    disposition_reason: str | None = None
    disposition_evidence_digest: str | None = None
    entry_digest: str | None = None

    def __post_init__(self) -> None:
        if type(self.finding) is not ReviewFinding or type(
            self.disposition
        ) is not ReviewFindingDisposition:
            raise CandidateGateError(
                "CANDIDATE_GATE_REPAIR_LEDGER_INVALID",
                "Finding ledger entry is not exactly typed",
            )
        if self.disposition is ReviewFindingDisposition.UNRESOLVED:
            if (
                self.disposition_reason is not None
                or self.disposition_evidence_digest is not None
            ):
                raise CandidateGateError(
                    "CANDIDATE_GATE_REPAIR_LEDGER_INVALID",
                    "unresolved Finding carries a completed disposition",
                )
        else:
            _require_text(self.disposition_reason, "disposition_reason")
            if self.disposition_evidence_digest is not None:
                _require_digest(
                    self.disposition_evidence_digest,
                    "disposition_evidence_digest",
                )
        expected = digest_value(self._body())
        if self.entry_digest is None:
            object.__setattr__(self, "entry_digest", expected)
        else:
            _validate_stored_digest(
                self.entry_digest,
                self._body(),
                code="CANDIDATE_GATE_REPAIR_LEDGER_INVALID",
                detail="Finding ledger entry digest changed",
            )

    def _body(self) -> dict[str, object]:
        return {
            "kind": "review_finding_ledger_entry.v1",
            "finding": self.finding.canonical(),
            "disposition": self.disposition.value,
            "disposition_reason": self.disposition_reason,
            "disposition_evidence_digest": self.disposition_evidence_digest,
        }

    @property
    def digest(self) -> str:
        assert self.entry_digest is not None
        return self.entry_digest

    def canonical(self) -> dict[str, object]:
        return {**self._body(), "entry_digest": self.digest}


@dataclass(frozen=True, slots=True)
class ReviewFindingLedger:
    entries: tuple[ReviewFindingLedgerEntry, ...]
    ledger_digest: str | None = None

    def __post_init__(self) -> None:
        if type(self.entries) is not tuple or any(
            type(entry) is not ReviewFindingLedgerEntry for entry in self.entries
        ):
            raise CandidateGateError(
                "CANDIDATE_GATE_REPAIR_LEDGER_INVALID",
                "Finding ledger entries are not an exact immutable tuple",
            )
        finding_ids = tuple(entry.finding.finding_id for entry in self.entries)
        if finding_ids != tuple(sorted(set(finding_ids))):
            raise CandidateGateError(
                "CANDIDATE_GATE_REPAIR_LEDGER_INVALID",
                "Finding ledger IDs are not sorted and unique",
            )
        expected = digest_value(self._body())
        if self.ledger_digest is None:
            object.__setattr__(self, "ledger_digest", expected)
        else:
            _validate_stored_digest(
                self.ledger_digest,
                self._body(),
                code="CANDIDATE_GATE_REPAIR_LEDGER_INVALID",
                detail="Finding ledger digest changed",
            )

    @classmethod
    def from_review_result(
        cls,
        result: FormalReviewResult,
    ) -> "ReviewFindingLedger":
        return cls(
            entries=tuple(
                ReviewFindingLedgerEntry(
                    finding=finding,
                    disposition=ReviewFindingDisposition.UNRESOLVED,
                )
                for finding in sorted(result.findings, key=lambda item: item.finding_id)
            )
        )

    def with_disposition(
        self,
        *,
        finding_id: str,
        disposition: ReviewFindingDisposition,
        reason: str,
        evidence_digest: str | None = None,
    ) -> "ReviewFindingLedger":
        if finding_id not in {entry.finding.finding_id for entry in self.entries}:
            raise CandidateGateError(
                "CANDIDATE_GATE_REPAIR_LEDGER_INVALID",
                "disposition names a Finding absent from the ledger",
            )
        return ReviewFindingLedger(
            entries=tuple(
                ReviewFindingLedgerEntry(
                    finding=entry.finding,
                    disposition=disposition,
                    disposition_reason=reason,
                    disposition_evidence_digest=evidence_digest,
                )
                if entry.finding.finding_id == finding_id
                else entry
                for entry in self.entries
            )
        )

    @property
    def is_complete(self) -> bool:
        return all(
            entry.disposition is not ReviewFindingDisposition.UNRESOLVED
            for entry in self.entries
        )

    def _body(self) -> dict[str, object]:
        return {
            "kind": "review_finding_ledger.v1",
            "entries": [entry.canonical() for entry in self.entries],
        }

    @property
    def digest(self) -> str:
        assert self.ledger_digest is not None
        return self.ledger_digest

    def canonical(self) -> dict[str, object]:
        return {**self._body(), "ledger_digest": self.digest}


@dataclass(frozen=True, slots=True)
class RepairPacket:
    parent_digest: str
    rejected_candidate_digest: str
    candidate_receipt: CandidateReceipt
    prior_review_subject_digest: str
    assurance_requirement_digest: str
    finding_ledger: ReviewFindingLedger
    required_disposition_ids: tuple[str, ...]
    allowed_path_tokens: tuple[str, ...]
    required_check_ids: tuple[str, ...]
    protocol_version: str
    repair_instructions: tuple[str, ...]
    required_effects: tuple[str, ...] = ()
    packet_digest: str | None = None

    def __post_init__(self) -> None:
        _require_digest(self.parent_digest, "parent_digest")
        _require_digest(self.rejected_candidate_digest, "rejected_candidate_digest")
        _require_digest(
            self.prior_review_subject_digest,
            "prior_review_subject_digest",
        )
        _require_digest(
            self.assurance_requirement_digest,
            "assurance_requirement_digest",
        )
        if type(self.candidate_receipt) is not CandidateReceipt:
            raise CandidateGateError(
                "CANDIDATE_GATE_REPAIR_PACKET_INVALID",
                "Repair Packet CandidateReceipt is not exact",
            )
        if type(self.finding_ledger) is not ReviewFindingLedger:
            raise CandidateGateError(
                "CANDIDATE_GATE_REPAIR_PACKET_INVALID",
                "Repair Packet Finding ledger is not exact",
            )
        expected_ids = tuple(
            entry.finding.finding_id for entry in self.finding_ledger.entries
        )
        if self.required_disposition_ids != expected_ids:
            raise CandidateGateError(
                "CANDIDATE_GATE_REPAIR_PACKET_INVALID",
                "Repair Packet disposition IDs do not cover the complete ledger",
            )
        for value, field_name, allow_empty in (
            (self.allowed_path_tokens, "allowed_path_tokens", True),
            (self.required_check_ids, "required_check_ids", False),
            (self.repair_instructions, "repair_instructions", False),
            (self.required_effects, "required_effects", True),
        ):
            _require_text_tuple(value, field_name, allow_empty=allow_empty)
            if value != tuple(sorted(set(value))):
                raise CandidateGateError(
                    "CANDIDATE_GATE_REPAIR_PACKET_INVALID",
                    f"{field_name} is not sorted and unique",
                )
        _require_text(self.protocol_version, "protocol_version")
        if self.protocol_version != "gwo.formal-review.v1":
            raise CandidateGateError(
                "CANDIDATE_GATE_REPAIR_PACKET_INVALID",
                "Repair Packet protocol version changed",
            )
        expected = digest_value(self._body())
        if self.packet_digest is None:
            object.__setattr__(self, "packet_digest", expected)
        else:
            _validate_stored_digest(
                self.packet_digest,
                self._body(),
                code="CANDIDATE_GATE_REPAIR_PACKET_INVALID",
                detail="Repair Packet digest changed",
            )

    @classmethod
    def from_review(
        cls,
        *,
        parent: CandidateGateParent,
        candidate_receipt: CandidateReceipt,
        subject: ReviewSubject,
        result: FormalReviewResult,
        allowed_path_tokens: tuple[str, ...],
        required_check_ids: tuple[str, ...],
        repair_instructions: tuple[str, ...],
    ) -> "RepairPacket":
        if (
            result.subject_digest != subject.digest
            or subject.candidate_receipt_digest != candidate_receipt.digest
        ):
            raise CandidateGateError(
                "CANDIDATE_GATE_REPAIR_PACKET_INVALID",
                "Review result, Subject, and CandidateReceipt are not bound",
            )
        ledger = ReviewFindingLedger.from_review_result(result)
        return cls(
            parent_digest=parent.digest,
            rejected_candidate_digest=subject.candidate_digest,
            candidate_receipt=candidate_receipt,
            prior_review_subject_digest=subject.digest,
            assurance_requirement_digest=subject.assurance_requirement_digest,
            finding_ledger=ledger,
            required_disposition_ids=tuple(
                entry.finding.finding_id for entry in ledger.entries
            ),
            allowed_path_tokens=tuple(sorted(set(allowed_path_tokens))),
            required_check_ids=tuple(sorted(set(required_check_ids))),
            protocol_version=subject.protocol_version,
            repair_instructions=tuple(sorted(set(repair_instructions))),
            required_effects=tuple(
                sorted(
                    {
                        effect
                        for entry in ledger.entries
                        for effect in entry.finding.required_effects
                    }
                )
            ),
        )

    def with_ledger(
        self,
        entries: tuple[ReviewFindingLedgerEntry, ...],
    ) -> "RepairPacket":
        ledger = ReviewFindingLedger(entries=entries)
        if tuple(entry.finding.finding_id for entry in ledger.entries) != (
            self.required_disposition_ids
        ):
            raise CandidateGateError(
                "CANDIDATE_GATE_REPAIR_PACKET_INVALID",
                "replacement ledger truncates required Findings",
            )
        return replace(self, finding_ledger=ledger, packet_digest=None)

    @property
    def candidate_receipt_digest(self) -> str:
        return self.candidate_receipt.digest

    @property
    def allowed_paths(self) -> tuple[str, ...]:
        return self.allowed_path_tokens

    def _body(self) -> dict[str, object]:
        return {
            "kind": "repair_packet.v1",
            "parent_digest": self.parent_digest,
            "rejected_candidate_digest": self.rejected_candidate_digest,
            "candidate_receipt": self.candidate_receipt.canonical(),
            "prior_review_subject_digest": self.prior_review_subject_digest,
            "assurance_requirement_digest": self.assurance_requirement_digest,
            "finding_ledger": self.finding_ledger.canonical(),
            "required_disposition_ids": list(self.required_disposition_ids),
            "allowed_path_tokens": list(self.allowed_path_tokens),
            "required_check_ids": list(self.required_check_ids),
            "protocol_version": self.protocol_version,
            "repair_instructions": list(self.repair_instructions),
            "required_effects": list(self.required_effects),
        }

    @property
    def digest(self) -> str:
        assert self.packet_digest is not None
        return self.packet_digest

    def canonical(self) -> dict[str, object]:
        return {**self._body(), "packet_digest": self.digest}
~~~

Replace the hard-Finding packet branch from Task 3 with this exact code so the
packet carries both hard and advisory Findings:

~~~python
hard_findings = tuple(
    finding for finding in findings if finding.severity == "hard"
)
ledger = ReviewFindingLedger.from_review_result(review_result)
if hard_findings:
    packet = RepairPacket.from_review(
        parent=parent,
        candidate_receipt=receipt,
        subject=subject,
        result=review_result,
        allowed_path_tokens=readback.diff_record.changed_path_tokens,
        required_check_ids=requirement.required_check_ids,
        repair_instructions=tuple(
            sorted(
                f"{finding.finding_id}:{finding.message}"
                for finding in findings
            )
        ),
    )
    return CandidateGateResult(
        status=CandidateGateStatus.REPAIR_REQUIRED,
        evidence=(candidate_evidence, *findings),
        repair_packet=packet,
        review_subject=subject,
        review_finding_ledger_digest=ledger.digest,
    )
~~~

For an accepted result, set `review_finding_ledger_digest=ledger.digest` as
well. The immutable canonical bodies above include every Finding,
disposition, required check, and instruction. `hard_findings` alone selects
`REPAIR_REQUIRED`; advisory Findings remain in the same ledger and Task 9
requires a non-`UNRESOLVED` disposition before verification.

- [ ] **Step 4: Prove GREEN and commit**

~~~powershell
py -3.13 -m pytest tests/test_v8_review_finding_ledger.py tests/test_v8_repair_packet.py tests/test_v8_candidate_strict_review.py -q
py -3.13 scripts/sync_orchestrator.py
py -3.13 scripts/sync_orchestrator.py --check
git diff --check
git add skills/orchestrator/.skill-package.json skills/orchestrator/scripts/gwo_v8/candidate_gate.py tests/test_v8_review_finding_ledger.py tests/test_v8_repair_packet.py
git commit -m "feat: preserve the complete Review Finding ledger"
~~~

---

### Task 8: After #113 Merge, Add the Serialized Candidate Budget Adapter

**Dependency boundary:** wait for the #113 Watchdog PR to merge. Rebase on its merged SHA, run the exact foundation Kernel test, and confirm Tasks 2–7 did not modify execution_kernel.py. This is the only later task in this plan allowed to modify that file.

**Files**

- Modify: skills/orchestrator/scripts/gwo_v8/execution_kernel.py
- Create: tests/test_v8_candidate_budget_kernel.py

**Interfaces**

- Consumes: foundation CandidateReceipt storage, merged #113 liveness code, #115 CandidateReceipt identities, and WorkRunObservation.
- Produces: persisted distinct Candidate OID list, typed CandidateBudgetExhausted Decision, and restart-preserved bounds.

- [ ] **Step 1: Write RED**

~~~python
import pytest

from gwo_v8.candidate_gate import CandidateReceipt
from gwo_v8.execution_kernel import CampaignStatus, ExecutionKernel
from v8_candidate_assurance_test_support import (
    CandidateReceiptEffects,
    make_candidate_receipt,
    read_kernel_state,
    write_kernel_state,
)
from v8_successor_test_support import _StaticPlanReader, _minimal_active_campaign

pytest_plugins = ("v8_candidate_assurance_test_support",)


@pytest.fixture
def candidate_sequence_kernel(tmp_path):
    active, campaign = _minimal_active_campaign(("issue:115",))
    ticket_key = "issue:115"
    foundation_receipt = make_candidate_receipt(active, campaign, ticket_key)
    effects = CandidateReceiptEffects(foundation_receipt)
    kernel = ExecutionKernel(
        store_path=tmp_path / "candidate-budget.sqlite3",
        plan_control=_StaticPlanReader(active),
        effects=effects,
    )
    kernel.advance(campaign)

    def run_sequence(candidate_oids: tuple[str, ...]):
        state = read_kernel_state(kernel, campaign)
        run = state["runs"][ticket_key]
        state["effects"] = {}
        run["candidate_receipt"] = None
        run["phase"] = "parked"
        run["slot_held"] = True
        run["claim_state"] = "held"
        run["last_action_id"] = None
        run["semantic_action_id"] = None
        run["resume_ordinal"] = 0
        effects.executed.clear()
        write_kernel_state(kernel, campaign, state)

        receipts: list[CandidateReceipt] = []
        for ordinal, candidate_oid in enumerate(candidate_oids, start=1):
            receipt = make_candidate_receipt(
                active,
                campaign,
                ticket_key,
                candidate_commit_oid=candidate_oid,
                candidate_tree_oid=candidate_oid,
            )
            receipts.append(receipt)
            state = read_kernel_state(kernel, campaign)
            run = state["runs"][ticket_key]
            run["candidate_receipt"] = receipt.canonical()
            run["phase"] = "parked"
            run["slot_held"] = True
            run["claim_state"] = "held"
            effects.receipt = receipt
            write_kernel_state(kernel, campaign, state)
            kernel._perform_due_effect(
                active,
                state,
                ticket_key,
                wake_ref=f"candidate-sequence:{ordinal}",
            )
        return (
            kernel,
            effects,
            campaign,
            kernel.advance(campaign),
            tuple(receipts),
        )

    return run_sequence


def test_kernel_records_distinct_candidate_oids_only(candidate_sequence_kernel):
    kernel, effects, campaign, _outcome, receipts = candidate_sequence_kernel(
        ("4" * 40, "4" * 40, "5" * 40)
    )
    state = read_kernel_state(kernel, campaign)
    assert state["runs"]["issue:115"]["candidate_commit_oids"] == [
        "4" * 40,
        "5" * 40,
    ]
    assert state["runs"]["issue:115"]["candidate_receipt_digests"] == list(
        dict.fromkeys(receipt.digest for receipt in receipts)
    )
    assert len(effects.executed) == 3


def test_fourth_distinct_candidate_returns_decision_before_effect(
    candidate_sequence_kernel,
):
    kernel, effects, campaign, outcome, receipts = candidate_sequence_kernel(
        ("4" * 40, "5" * 40, "6" * 40, "7" * 40)
    )
    assert outcome.status == CampaignStatus.DECISION
    assert outcome.reason == "CandidateBudgetExhausted:issue:115"
    assert len(effects.executed) == 3
    state = read_kernel_state(kernel, campaign)
    run = state["runs"]["issue:115"]
    assert run["phase"] == "decision"
    assert run["slot_held"] is False
    assert run["claim_state"] == "released"
    assert run["candidate_receipt_digests"][-1] == receipts[-1].digest


def test_restart_does_not_reset_candidate_bound(candidate_sequence_kernel):
    kernel, effects, campaign, _outcome, _receipts = candidate_sequence_kernel(
        ("4" * 40, "5" * 40)
    )
    restarted = ExecutionKernel(
        store_path=kernel._store_path,
        plan_control=kernel._plan_control,
        effects=effects,
    )
    state = read_kernel_state(restarted, campaign)
    assert state["runs"]["issue:115"]["candidate_commit_oids"] == [
        "4" * 40,
        "5" * 40,
    ]
~~~

- [ ] **Step 2: Prove RED**

~~~powershell
py -3.13 -m pytest tests/test_v8_candidate_budget_kernel.py -q
~~~

Expected: candidate_commit_oids is absent and the fourth distinct Candidate is not converted to a Decision.

- [ ] **Step 3: Implement minimum bound**

Add both lists to the run dictionaries in `_load_or_initialize` and
`_new_run_state`:

~~~python
{
    "candidate_commit_oids": [],
    "candidate_receipt_digests": [],
}
~~~

For legacy rows, repair/replacement transitions, and #113-upgraded rows, use
`setdefault` only. Insert this exact block at the beginning of the existing
`_perform_due_effect`, immediately after
`run = state["runs"][ticket_key]` and before `resuming`, action construction,
effect-intent persistence, `self._effects.readback(action)`, or
`self._effects.execute(action)`:

~~~python
candidate_commit_oids = run.setdefault("candidate_commit_oids", [])
candidate_receipt_digests = run.setdefault("candidate_receipt_digests", [])
if type(candidate_commit_oids) is not list or any(
    type(oid) is not str
    or re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", oid) is None
    for oid in candidate_commit_oids
):
    raise ExecutionKernelError(
        "EXECUTION_STORE_INVALID",
        "Candidate commit OID history is malformed",
    )
if len(candidate_commit_oids) != len(set(candidate_commit_oids)):
    raise ExecutionKernelError(
        "EXECUTION_STORE_INVALID",
        "Candidate commit OID history contains duplicates",
    )
if type(candidate_receipt_digests) is not list or any(
    type(digest) is not str
    or re.fullmatch(r"[0-9a-f]{64}", digest) is None
    for digest in candidate_receipt_digests
):
    raise ExecutionKernelError(
        "EXECUTION_STORE_INVALID",
        "Candidate receipt digest history is malformed",
    )
if len(candidate_receipt_digests) != len(set(candidate_receipt_digests)):
    raise ExecutionKernelError(
        "EXECUTION_STORE_INVALID",
        "Candidate receipt digest history contains duplicates",
    )

stored_receipt = run.get("candidate_receipt")
history_changed = False
if stored_receipt is not None:
    try:
        receipt = CandidateReceipt.from_canonical(stored_receipt)
    except CandidateGateError as error:
        raise ExecutionKernelError(
            "EXECUTION_STORE_INVALID",
            "Candidate budget input failed canonical receipt readback",
        ) from error
    if (
        receipt.repository != active.handle.repository
        or receipt.campaign_key != active.handle.campaign_key
        or receipt.campaign_handle != active.handle.campaign_key
        or receipt.plan_revision_digest != active.current_revision_digest
        or receipt.ticket_key != ticket_key
        or receipt.work_run_key != run["work_run_key"]
        or receipt.runtime_subject_digest != run["work_subject_digest"]
    ):
        raise ExecutionKernelError(
            "EXECUTION_STORE_INVALID",
            "Candidate budget receipt is bound to another Work Run",
        )
    if receipt.digest not in candidate_receipt_digests:
        candidate_receipt_digests.append(receipt.digest)
        history_changed = True
    if receipt.candidate_commit_oid not in candidate_commit_oids:
        candidate_commit_oids.append(receipt.candidate_commit_oid)
        history_changed = True
    if len(candidate_commit_oids) > 3:
        run["phase"] = "decision"
        run["reason"] = f"CandidateBudgetExhausted:{ticket_key}"
        run["next_check_at"] = None
        run["slot_held"] = False
        run["claim_state"] = "released"
        self._save(active.handle, state)
        return

if history_changed:
    self._save(active.handle, state)
~~~

The fourth receipt and its digest are therefore durable in the same DECISION
write, while only the first three distinct OIDs reach the existing external
effect branch. Exact receipt replay and repeated SHA are idempotent because
both histories append only unseen identities. No repair, resume, or terminal
binding replacement branch assigns either list; `setdefault` preserves them
across restart and #113 state reconciliation.

- [ ] **Step 4: Prove GREEN and commit**

~~~powershell
py -3.13 -m pytest tests/test_v8_candidate_budget_kernel.py -q
py -3.13 -m pytest tests/test_v8_candidate_receipt_kernel.py tests/test_v8_watchdog_execution_kernel.py -q
py -3.13 -m pytest tests/test_v8_execution_kernel.py tests/test_v8_successor_execution_kernel.py -q
py -3.13 scripts/sync_orchestrator.py
py -3.13 scripts/sync_orchestrator.py --check
git diff --check
git add skills/orchestrator/.skill-package.json skills/orchestrator/scripts/gwo_v8/execution_kernel.py tests/test_v8_candidate_budget_kernel.py
git commit -m "feat: bound Candidate submissions per Work Run"
~~~

Expected: the exact foundation test and #113 baseline pass after the serialized merge.

---

### Task 9: Implement Bounded Repair Verification with Exact Delta

**Files**

- Modify: skills/orchestrator/scripts/gwo_v8/candidate_gate.py
- Create: tests/test_v8_repair_verification.py

**Interfaces**

- Consumes: existing verify_repair(parent, packet, candidate), authoritative CandidateReadbackPort, complete RepairPacket/ledger, and RepairVerifier.
- Produces: RepairDelta, repair ReviewSubject fields, RepairVerificationRequest, and bounded repair outcome.
- Ownership: #115 CandidateGate only; do not modify execution_kernel.py.

- [ ] **Step 1: Write RED**

~~~python
def test_verify_repair_uses_repair_verify_not_formal_review(repair_gate):
    gate, verifier, parent, packet, candidate = repair_gate
    result = gate.verify_repair(parent, packet, candidate)
    assert result.status == CandidateGateStatus.REPAIR_ACCEPTED
    assert verifier.requests[0].review_subject.action_kind == "repair_verify"
    assert verifier.requests[0].review_subject.prior_review_subject_digest == (
        packet.prior_review_subject_digest
    )
    assert verifier.requests[0].review_subject.repair_packet_digest == packet.digest
    assert verifier.requests[0].review_subject.repair_delta_digest == (
        verifier.requests[0].repair_delta.digest
    )


def test_repair_requires_disposition_for_every_prior_finding(unresolved_repair):
    gate, _verifier, parent, packet, candidate = unresolved_repair
    with pytest.raises(CandidateGateError) as raised:
        gate.verify_repair(parent, packet, candidate)
    assert raised.value.code == "CANDIDATE_GATE_REPAIR_LEDGER_INVALID"


def test_repair_scope_escape_fails_before_verifier(scope_escape_repair):
    gate, verifier, parent, packet, candidate = scope_escape_repair
    with pytest.raises(CandidateGateError) as raised:
        gate.verify_repair(parent, packet, candidate)
    assert raised.value.code == "CANDIDATE_GATE_REPAIR_SCOPE_INVALID"
    assert verifier.requests == []
~~~

- [ ] **Step 2: Prove RED**

~~~powershell
py -3.13 -m pytest tests/test_v8_repair_verification.py -q
~~~

Expected: RepairDelta, repair subject identity, or complete-ledger validation is missing.

- [ ] **Step 3: Define and implement RepairDelta**

Use:

~~~python
@dataclass(frozen=True, slots=True)
class RepairDelta:
    prior_candidate_commit_oid: str
    prior_candidate_tree_oid: str
    prior_diff_record_digest: str
    repaired_candidate_commit_oid: str
    repaired_candidate_tree_oid: str
    repaired_diff_record_digest: str
    added_path_tokens: tuple[str, ...]
    removed_path_tokens: tuple[str, ...]
    changed_path_tokens: tuple[str, ...]
    delta_digest: str | None = None

    def __post_init__(self) -> None:
        for field_name in (
            "prior_candidate_commit_oid",
            "prior_candidate_tree_oid",
            "repaired_candidate_commit_oid",
            "repaired_candidate_tree_oid",
        ):
            _require_object_id(getattr(self, field_name), field_name)
        for field_name in (
            "prior_diff_record_digest",
            "repaired_diff_record_digest",
        ):
            _require_digest(getattr(self, field_name), field_name)
        token_groups = (
            self.added_path_tokens,
            self.removed_path_tokens,
            self.changed_path_tokens,
        )
        for value, field_name in zip(
            token_groups,
            (
                "added_path_tokens",
                "removed_path_tokens",
                "changed_path_tokens",
            ),
            strict=True,
        ):
            _require_text_tuple(value, field_name)
            if value != tuple(sorted(set(value))):
                raise CandidateGateError(
                    "CANDIDATE_GATE_REPAIR_DELTA_INVALID",
                    f"{field_name} is not sorted and unique",
                )
        if any(set(left) & set(right) for left, right in (
            (self.added_path_tokens, self.removed_path_tokens),
            (self.added_path_tokens, self.changed_path_tokens),
            (self.removed_path_tokens, self.changed_path_tokens),
        )):
            raise CandidateGateError(
                "CANDIDATE_GATE_REPAIR_DELTA_INVALID",
                "RepairDelta path classifications overlap",
            )
        expected = digest_value(self._body())
        if self.delta_digest is None:
            object.__setattr__(self, "delta_digest", expected)
        else:
            _validate_stored_digest(
                self.delta_digest,
                self._body(),
                code="CANDIDATE_GATE_REPAIR_DELTA_INVALID",
                detail="RepairDelta digest changed",
            )

    @classmethod
    def from_records(
        cls,
        prior: CandidateDiffRecordV1,
        repaired: CandidateDiffRecordV1,
    ) -> "RepairDelta":
        old = set(prior.changed_path_tokens)
        new = set(repaired.changed_path_tokens)
        return cls(
            prior_candidate_commit_oid=prior.candidate_commit_oid,
            prior_candidate_tree_oid=prior.candidate_tree_oid,
            prior_diff_record_digest=prior.digest,
            repaired_candidate_commit_oid=repaired.candidate_commit_oid,
            repaired_candidate_tree_oid=repaired.candidate_tree_oid,
            repaired_diff_record_digest=repaired.digest,
            added_path_tokens=tuple(sorted(new - old)),
            removed_path_tokens=tuple(sorted(old - new)),
            changed_path_tokens=tuple(sorted(old & new)),
        )

    def _body(self) -> dict[str, object]:
        return {
            "kind": "repair_delta.v1",
            "prior_candidate_commit_oid": self.prior_candidate_commit_oid,
            "prior_candidate_tree_oid": self.prior_candidate_tree_oid,
            "prior_diff_record_digest": self.prior_diff_record_digest,
            "repaired_candidate_commit_oid": self.repaired_candidate_commit_oid,
            "repaired_candidate_tree_oid": self.repaired_candidate_tree_oid,
            "repaired_diff_record_digest": self.repaired_diff_record_digest,
            "added_path_tokens": list(self.added_path_tokens),
            "removed_path_tokens": list(self.removed_path_tokens),
            "changed_path_tokens": list(self.changed_path_tokens),
        }

    @property
    def digest(self) -> str:
        assert self.delta_digest is not None
        return self.delta_digest

    def canonical(self) -> dict[str, object]:
        return {**self._body(), "delta_digest": self.digest}
~~~

Replace the predecessor `RepairVerificationRequest` with this complete type:

~~~python
@dataclass(frozen=True, slots=True)
class RepairVerificationRequest:
    parent_digest: str
    repair_packet_digest: str
    candidate_receipt: CandidateReceipt
    candidate: CandidateIdentity
    review_subject: ReviewSubject
    repair_delta: RepairDelta
    finding_ledger: ReviewFindingLedger
    required_check_evidence: tuple[CandidateCheckEvidence, ...]
    request_digest: str | None = None

    def __post_init__(self) -> None:
        _require_digest(self.parent_digest, "parent_digest")
        _require_digest(self.repair_packet_digest, "repair_packet_digest")
        if (
            type(self.candidate_receipt) is not CandidateReceipt
            or type(self.candidate) is not CandidateIdentity
            or type(self.review_subject) is not ReviewSubject
            or type(self.repair_delta) is not RepairDelta
            or type(self.finding_ledger) is not ReviewFindingLedger
        ):
            raise CandidateGateError(
                "CANDIDATE_GATE_REPAIR_REQUEST_INVALID",
                "Repair Verification request contains a non-exact typed value",
            )
        if type(self.required_check_evidence) is not tuple or any(
            type(check) is not CandidateCheckEvidence
            for check in self.required_check_evidence
        ):
            raise CandidateGateError(
                "CANDIDATE_GATE_REPAIR_REQUEST_INVALID",
                "Repair Verification checks are not an exact tuple",
            )
        if (
            self.review_subject.action_kind != "repair_verify"
            or self.review_subject.repair_packet_digest
            != self.repair_packet_digest
            or self.review_subject.repair_delta_digest != self.repair_delta.digest
            or self.review_subject.candidate_receipt_digest
            != self.candidate_receipt.digest
            or self.candidate_receipt.candidate_commit_oid
            != self.candidate.candidate_commit_oid
            or self.candidate_receipt.candidate_tree_oid
            != self.candidate.candidate_tree_oid
        ):
            raise CandidateGateError(
                "CANDIDATE_GATE_REPAIR_REQUEST_INVALID",
                "Repair Verification request identity is internally inconsistent",
            )
        expected = digest_value(self._body())
        if self.request_digest is None:
            object.__setattr__(self, "request_digest", expected)
        else:
            _validate_stored_digest(
                self.request_digest,
                self._body(),
                code="CANDIDATE_GATE_REPAIR_REQUEST_INVALID",
                detail="Repair Verification request digest changed",
            )

    def _body(self) -> dict[str, object]:
        return {
            "kind": "repair_verification_request.v1",
            "parent_digest": self.parent_digest,
            "repair_packet_digest": self.repair_packet_digest,
            "candidate_receipt": self.candidate_receipt.canonical(),
            "candidate": self.candidate.canonical(),
            "review_subject": self.review_subject.canonical(),
            "repair_delta": self.repair_delta.canonical(),
            "finding_ledger": self.finding_ledger.canonical(),
            "required_check_evidence": [
                check.canonical() for check in self.required_check_evidence
            ],
        }

    @property
    def digest(self) -> str:
        assert self.request_digest is not None
        return self.request_digest

    def canonical(self) -> dict[str, object]:
        return {**self._body(), "request_digest": self.digest}
~~~

Keep the real `verify_repair(parent, packet, candidate)` signature and replace
its body with this bounded continuation:

~~~python
def verify_repair(
    self,
    parent: CandidateGateParent,
    packet: RepairPacket,
    candidate: CandidateIdentity,
) -> CandidateGateResult:
    self._validate_parent(parent)
    self._validate_repair_packet(parent, packet)
    if type(candidate) is not CandidateIdentity:
        raise CandidateGateError(
            "CANDIDATE_GATE_EVIDENCE_INVALID",
            "Repair Verification requires an exact CandidateIdentity",
        )
    if not packet.finding_ledger.is_complete:
        raise CandidateGateError(
            "CANDIDATE_GATE_REPAIR_LEDGER_INVALID",
            "every prior Finding requires a completed disposition",
        )

    readback = self._read_authoritative_repair_candidate(parent, candidate)
    repaired_record = self._store_candidate_diff(readback.diff_record)
    readback = replace(
        readback,
        diff_record=repaired_record,
        readback_digest=None,
    )
    repaired_receipt = CandidateReceipt.from_readback(
        parent=parent,
        reported_reference=candidate.reported_reference,
        readback=readback,
    )
    store = self._diff_artifacts
    if store is None:
        raise CandidateGateError(
            "CANDIDATE_GATE_DIFF_ARTIFACT_INVALID",
            "Repair Verification lacks the prior Candidate diff Artifact",
        )
    prior_record = store.read(packet.candidate_receipt.diff_record_digest)
    if (
        type(prior_record) is not CandidateDiffRecordV1
        or prior_record.digest != packet.candidate_receipt.diff_record_digest
        or prior_record.candidate_commit_oid
        != packet.candidate_receipt.candidate_commit_oid
        or prior_record.candidate_tree_oid
        != packet.candidate_receipt.candidate_tree_oid
    ):
        raise CandidateGateError(
            "CANDIDATE_GATE_DIFF_ARTIFACT_INVALID",
            "prior Candidate diff Artifact changed before Repair Verification",
        )
    delta = RepairDelta.from_records(prior_record, repaired_record)
    escaped_paths = tuple(
        sorted(
            set(repaired_record.changed_path_tokens)
            - set(packet.allowed_path_tokens)
        )
    )
    if escaped_paths:
        raise CandidateGateError(
            "CANDIDATE_GATE_REPAIR_SCOPE_INVALID",
            "repaired Candidate changed paths outside Repair Packet scope: "
            + ",".join(escaped_paths),
        )

    check_runner = self._check_runner
    assurance_policy = self._assurance_policy
    if check_runner is None or assurance_policy is None:
        raise CandidateGateError(
            "CANDIDATE_GATE_ADAPTER_INVALID",
            "Repair Verification requires checks and Assurance policy",
        )
    checks = check_runner.run(parent, readback)
    by_id = {check.check_id: check for check in checks}
    if set(by_id) != set(packet.required_check_ids) or any(
        by_id[check_id].outcome != "passed"
        for check_id in packet.required_check_ids
    ):
        raise CandidateGateError(
            "CANDIDATE_GATE_REPAIR_CHECK_INVALID",
            "Repair Verification lacks an exact passing required-check set",
        )
    required_checks = tuple(by_id[key] for key in sorted(by_id))
    requirement = assurance_policy.derive(parent, readback, required_checks)
    if requirement.digest != packet.assurance_requirement_digest:
        raise CandidateGateError(
            "CANDIDATE_GATE_REPAIR_ASSURANCE_INVALID",
            "Repair changed the frozen Assurance Requirement",
        )
    audit = self._audit_readback(parent, readback, required_checks, requirement)
    initial_subject = ReviewSubject.from_assurance(
        parent=parent,
        candidate_receipt=repaired_receipt,
        readback=readback,
        audit=audit,
        checks=required_checks,
        requirement=requirement,
    )
    repair_subject = replace(
        initial_subject,
        action_kind="repair_verify",
        prior_review_subject_digest=packet.prior_review_subject_digest,
        repair_packet_digest=packet.digest,
        repair_delta_digest=delta.digest,
        subject_digest=None,
    )
    request = RepairVerificationRequest(
        parent_digest=parent.digest,
        repair_packet_digest=packet.digest,
        candidate_receipt=repaired_receipt,
        candidate=readback.candidate,
        review_subject=repair_subject,
        repair_delta=delta,
        finding_ledger=packet.finding_ledger,
        required_check_evidence=required_checks,
    )
    verifier = self._repair_verifier
    if verifier is None:
        raise CandidateGateError(
            "CANDIDATE_GATE_REPAIR_VERIFIER_UNAVAILABLE",
            "Repair Verification requires the CandidateGate Repair Verifier",
        )
    self._validate_read_only_port(verifier, "Repair Verifier")
    verification = verifier.verify(request)
    self._validate_repair_result(request, verification)
    verification_evidence = RepairVerificationEvidence(
        parent_digest=parent.digest,
        candidate_digest=readback.candidate.digest,
        repair_packet_digest=packet.digest,
        request_digest=request.digest,
        accepted=verification.accepted,
        scope_escape_paths=(),
        details=verification.details,
    )
    accepted = None
    if verification.accepted:
        accepted = self._make_accepted_candidate_receipt(
            parent=parent,
            candidate_receipt=repaired_receipt,
            candidate_diff_record=repaired_record,
            review_subject=repair_subject,
            assurance_requirement=requirement,
            evidence=(verification_evidence,),
            review_finding_ledger_digest=packet.finding_ledger.digest,
        )
    return CandidateGateResult(
        status=(
            CandidateGateStatus.REPAIR_ACCEPTED
            if verification.accepted
            else CandidateGateStatus.REPAIR_REJECTED
        ),
        evidence=(verification_evidence,),
        repair_packet=packet,
        candidate_receipt=repaired_receipt,
        candidate_diff_record=repaired_record,
        assurance_requirement=requirement,
        review_subject=repair_subject,
        accepted_candidate_receipt=accepted,
        review_finding_ledger_digest=packet.finding_ledger.digest,
    )
~~~

This body invokes only `RepairVerifier.verify`; it never invokes
`FormalReviewer.review` or `gate_candidate`. The new CandidateReceipt and
RepairDelta make a changed Candidate ineligible for exact-SHA Review reuse.

- [ ] **Step 4: Prove GREEN and commit**

~~~powershell
py -3.13 -m pytest tests/test_v8_repair_verification.py tests/test_v8_repair_packet.py tests/test_v8_candidate_gate.py -q
py -3.13 scripts/sync_orchestrator.py
py -3.13 scripts/sync_orchestrator.py --check
git diff --check
git add skills/orchestrator/.skill-package.json skills/orchestrator/scripts/gwo_v8/candidate_gate.py tests/test_v8_repair_verification.py tests/test_v8_repair_packet.py tests/test_v8_candidate_gate.py
git commit -m "feat: verify repaired Candidates through bounded delta"
~~~

---

### Task 10: Update Accepted ADR/Architecture/Spec/Roadmap Contract

**Files**

- Create: docs/adr/0063-candidate-review-repair-boundary.md
- Modify: docs/design/gwo-v8-lean-architecture.md
- Modify: docs/design/gwo-v8-lean-stabilization-spec.md
- Modify: docs/design/gwo-v8-lean-roadmap.md
- Create: tests/test_v8_candidate_assurance_contract.py

**Interfaces**

- Consumes: merged #114/#115 code, focused tests, and Issue #114/#115 owner comments.
- Produces: accepted ADR-0063 amending ADR-0041, ADR-0043, and ADR-0057, plus integrated documentation.
- Ownership: docs-only worker; no production code or GitHub mutation.

- [ ] **Step 1: Write RED**

~~~python
def test_candidate_assurance_domain_contract_names_exact_interfaces():
    text = "\n".join(
        path.read_text("utf-8")
        for path in (
            ROOT / "docs" / "adr" / "0063-candidate-review-repair-boundary.md",
            ROOT / "docs" / "design" / "gwo-v8-lean-architecture.md",
            ROOT / "docs" / "design" / "gwo-v8-lean-stabilization-spec.md",
            ROOT / "docs" / "design" / "gwo-v8-lean-roadmap.md",
        )
    )
    for required in (
        "CandidateReceipt",
        "candidate_tree_oid",
        "CandidateDiffRecordV1",
        "ReviewSubject",
        "ReviewFindingLedger",
        "AssuranceRequirement",
        "repair_verify",
        "CandidateBudgetExhausted",
        "Beta2",
        "Beta3",
        "root Canary",
    ):
        assert required in text
~~~

- [ ] **Step 2: Prove RED**

~~~powershell
py -3.13 -m pytest tests/test_v8_candidate_assurance_contract.py -q
~~~

Expected: ADR-0063 or the exact integrated contract text is absent.

- [ ] **Step 3: Write the accepted contract**

Run this generator from the repository root. It writes the complete ADR and
upserts one concrete, uniquely headed section in each integrated design
document; rerunning it produces byte-identical content.

~~~powershell
@'
from pathlib import Path
from textwrap import dedent


ROOT = Path.cwd()


def upsert_section(path: Path, heading: str, body: str) -> None:
    rendered = dedent(body).strip().splitlines()
    if not rendered or rendered[0] != heading:
        raise RuntimeError(f"section for {path} does not start with {heading}")
    current = path.read_text("utf-8").splitlines()
    if heading in current:
        start = current.index(heading)
        end = next(
            (
                index
                for index in range(start + 1, len(current))
                if current[index].startswith("## ")
            ),
            len(current),
        )
        updated = current[:start] + rendered + current[end:]
    else:
        updated = current + ([""] if current and current[-1] else []) + rendered
    path.write_text("\n".join(updated).rstrip() + "\n", encoding="utf-8")


adr = dedent(
    """
    # ADR-0063: Candidate Review and Repair Boundary

    - Status: Accepted
    - Date: 2026-08-03
    - Amends: ADR-0041, ADR-0043, and ADR-0057
    - Issues: #114 and #115

    ## Context

    Worker output is not authoritative Candidate identity, Evidence, or a
    delivery Result. Lean V8 needs one fail-closed boundary that rereads the
    exact Git base and reported Candidate reference, proves the complete tree
    delta, selects bounded Assurance, and preserves Review and Repair lineage
    without granting a Reviewer mutation, delegation, merge, tracker, or
    global-planning authority.

    Campaign Watchdog work in #113 needs only an immutable persisted Candidate
    receipt. Batch delivery in #116 consumes only an accepted-Candidate
    receipt. Neither consumer may invent a competing Candidate identity.

    ## Decision

    CandidateGate is the sole Formal Review entry. GitCandidateReader resolves
    the frozen base through CandidateBasePort and resolves the reported
    reference to exact base_commit_oid, base_tree_oid, candidate_commit_oid,
    and candidate_tree_oid values. It emits one immutable
    CandidateDiffRecordV1. Rename and copy inference are disabled; raw Git path
    bytes use unpadded base64url tokens and a rename is delete plus add.

    CandidateReceipt is private Work Run identity. ExecutionKernel persists
    CandidateReceipt.canonical() directly at
    state["runs"][ticket_key]["candidate_receipt"], reads it back before the
    phase transition, and exposes read-only receipt access. candidate_tree_oid
    remains a root canonical field. #113 imports this receipt and owns every
    Watchdog or liveness projection; it does not construct or repair receipts.

    The exact CandidateDiffRecordV1 instance and digest are reused by scope,
    protected-surface, authority, affected-check, AssuranceRequirement,
    ReviewSubject, InteractionKey, RepairDelta, and AcceptedCandidateReceipt
    construction. A deterministic failure returns before any Reviewer call.

    AssuranceMode.NO_REVIEW performs zero Reviewer actions but still emits an
    exact ReviewSubject and accepted-Candidate receipt. STANDARD performs one
    formal_review. STRICT performs one formal_review followed by at most one
    policy-selected specialist_review; absence of the required specialist
    returns a typed human Decision. InvalidReviewTransport permits exactly one
    review_strong retry over the identical ReviewSubject.digest. A valid result
    for an unchanged Subject is not reviewed again.

    ReviewFindingLedger retains every hard and advisory ReviewFinding with one
    typed ReviewFindingDisposition. RepairPacket binds the complete ledger,
    required disposition IDs, allowed raw-path tokens, required checks,
    protocol version, and repair instructions. repair_verify rereads the
    repaired Candidate, computes RepairDelta from the prior and repaired
    CandidateDiffRecordV1 values, rejects path escape before RepairVerifier,
    requires a disposition for every prior Finding, reruns the exact required
    checks, and invokes only RepairVerifier.verify. It never reopens Formal
    Review for a changed Candidate.

    One Work Run records at most three effect-admitted distinct Candidate
    commit OIDs. A fourth distinct persisted receipt produces
    CandidateBudgetExhausted: concatenated with the exact ticket_key as a
    durable Decision before the next
    external effect and releases the Worker Slot. Exact replay and repeated SHA
    are idempotent. Repair, restart, resume, and the single
    terminal-binding-Evidence-authorized replacement do not reset Candidate or
    binding bounds.

    Formal Reviewer and Repair Verifier capability readback must prove a
    read-only, non-delegating boundary with no tracker mutation, merge,
    authority expansion, or global planning. A proved Ticket-unsatisfiable
    scope escape invokes only the existing PlanInvalidationReporter seam owned
    by #137. CandidateGate does not classify the Campaign, edit Issues, change
    membership, or create a successor Plan Revision.

    AcceptedCandidateReceipt binds CandidateReceipt.digest, exact base and
    Candidate commit/tree identity, CandidateDiffRecordV1 digest,
    AssuranceRequirement, ReviewSubject, Policy Witness, Evidence digests,
    complete ReviewFindingLedger digest, protected surfaces, and concrete
    InteractionKey values. It has no result_digest. A code Result exists only
    after exact Batch integration and target read-back.

    ## Release admission

    Beta1 is metadata and tracker repair only and grants no production
    admission. Beta2 is the feature-complete preview after #113 through #117
    and #137 merge with exact Candidate-assurance evidence. Beta3 is the
    cutover candidate and still requires Guard and Activation read-back. GA
    requires a real public-API root Canary plus exact target, Activation, and
    default-writer read-back.

    ## Consequences

    Candidate identity has one owner and one canonical digest chain. Watchdog,
    CandidateGate, and Batch delivery can evolve without redefining receipt
    fields. Review and Repair calls are bounded and replayable. Failures remain
    auditable through immutable Evidence, while production admission remains
    outside Beta1 and outside CandidateGate itself.
    """
).strip() + "\n"

(ROOT / "docs" / "adr" / "0063-candidate-review-repair-boundary.md").write_text(
    adr,
    encoding="utf-8",
)

upsert_section(
    ROOT / "docs" / "design" / "gwo-v8-lean-architecture.md",
    "## Candidate assurance, Review, and Repair",
    """
    ## Candidate assurance, Review, and Repair

    CandidateGate is the sole Formal Review entry and remains a private deep
    module behind start, advance, and inspect. Its execution flow is:

    1. GitCandidateReader resolves the frozen base and reported reference to
       exact commit and tree OIDs.
    2. CandidateGate constructs one CandidateDiffRecordV1 and one private
       CandidateReceipt; ExecutionKernel persists the canonical receipt at
       state["runs"][ticket_key]["candidate_receipt"].
    3. Scope, protected-surface, authority, and affected checks consume the
       same diff. A deterministic failure stops before Review.
    4. AssuranceRequirement selects NO_REVIEW, STANDARD, or STRICT. STANDARD
       allows one formal_review; STRICT adds at most one specialist_review;
       malformed transport allows one same-Subject review_strong retry.
    5. Accepted Review emits AcceptedCandidateReceipt. Hard Findings emit a
       RepairPacket containing the complete ReviewFindingLedger. repair_verify
       rereads Git, computes RepairDelta, reruns required checks, and calls only
       RepairVerifier.
    6. A proved Ticket-unsatisfiable escape goes only to #137 Plan
       Invalidation. CandidateGate never performs Campaign classification or
       successor planning.

    CandidateReceipt and AcceptedCandidateReceipt are distinct. The first is
    Kernel-persisted Work Run identity consumed read-only by #113. The second
    is delivery eligibility consumed by #116 and contains concrete
    InteractionKey values owned by candidate_gate.py. Neither value is a code
    Result; Result identity requires integration and target read-back.

    Reviewer and RepairVerifier ports accept only capability-proven read-only,
    no-delegation subjects. Candidate history, binding history, repair, restart,
    and replacement remain bounded by ADR-0063.
    """,
)

upsert_section(
    ROOT / "docs" / "design" / "gwo-v8-lean-stabilization-spec.md",
    "## Candidate assurance normative requirements",
    """
    ## Candidate assurance normative requirements

    - Candidate identity MUST come from authoritative Git read-back of the
      frozen base and reported reference, including exact commit and tree OIDs.
    - CandidateDiffRecordV1 MUST contain complete old/new raw-tree identity,
      MUST encode paths as unpadded base64url raw bytes, and MUST disable rename
      and copy inference.
    - ExecutionKernel MUST persist CandidateReceipt.canonical() directly at
      state["runs"][ticket_key]["candidate_receipt"] and MUST validate exact
      read-back before applying the observation phase.
    - Deterministic scope, protected-surface, authority, and affected-check
      failure MUST stop before Formal Review.
    - STANDARD MUST execute exactly one formal_review. STRICT MUST execute one
      formal_review and at most one policy-selected specialist_review, or
      return a typed Decision. InvalidReviewTransport MAY execute one
      review_strong retry only when ReviewSubject.digest is unchanged.
    - Reviewer and RepairVerifier capability proof MUST be read-only and
      non-delegating and MUST deny tracker mutation, merge, authority expansion,
      and global planning.
    - ReviewFindingLedger MUST retain every hard and advisory Finding and every
      typed disposition. repair_verify MUST require complete dispositions,
      exact required-check Evidence, and a RepairDelta within RepairPacket path
      scope before invoking RepairVerifier.verify.
    - One Work Run MUST NOT execute an external effect for a fourth distinct
      Candidate commit OID. It MUST persist CandidateBudgetExhausted:
      concatenated with the exact ticket_key, release the Slot, and preserve
      CandidateReceipt digests first.
      Repair, restart, resume, and one authorized terminal-binding replacement
      MUST NOT reset Candidate or binding bounds.
    - CandidateGate MUST route proved Ticket-unsatisfiable escape only through
      #137 PlanInvalidationReporter and MUST NOT classify a Campaign or create a
      successor Plan Revision.
    - AcceptedCandidateReceipt MUST bind the private receipt, exact diff,
      AssuranceRequirement, ReviewSubject, Policy Witness, complete Finding
      ledger, Evidence, protected surfaces, and concrete InteractionKey values.
      It MUST NOT contain result_digest.
    """,
)

upsert_section(
    ROOT / "docs" / "design" / "gwo-v8-lean-roadmap.md",
    "## Lean V8 release admission after Candidate assurance",
    """
    ## Lean V8 release admission after Candidate assurance

    - Beta1: metadata and tracker repair only. It does not admit Lean V8
      production execution.
    - Beta2: feature-complete preview. Exit requires merged #113, #114, #115,
      #116, #117, and #137; the exact CandidateReceipt Kernel baseline;
      authoritative Candidate Git identity; Standard and Strict Review bounds;
      complete Finding dispositions; bounded repair_verify; Candidate budget
      restart evidence; isolated V3 composition; and green CI for the exact
      main SHA. Beta2 does not switch the default writer.
    - Beta3: cutover candidate. Entry requires the cutover Guard, exact target
      and Activation read-back, rollback proof, and no unresolved native
      blocker.
    - GA: requires a real public-API root Canary and exact target, Activation,
      and default-writer read-back. Synthetic or private-helper execution is
      not GA evidence.
    """,
)
'@ | py -3.13 -
if ($LASTEXITCODE -ne 0) {
    throw 'Candidate assurance contract generation failed.'
}
~~~

- [ ] **Step 4: Prove GREEN and commit**

~~~powershell
py -3.13 -m pytest tests/test_v8_candidate_assurance_contract.py -q
py -3.13 scripts/quick_validate.py
git diff --check
git add docs/adr/0063-candidate-review-repair-boundary.md docs/design/gwo-v8-lean-architecture.md docs/design/gwo-v8-lean-stabilization-spec.md docs/design/gwo-v8-lean-roadmap.md tests/test_v8_candidate_assurance_contract.py
git commit -m "docs: record Candidate Review and Repair boundary"
~~~

---

### Task 11: Validation and Explicit Beta2 Evidence

This task records evidence only. It does not create tags, change GitHub state, or claim production admission.

**Files**

- Read: tests/test_v8_candidate_receipt_kernel.py
- Read: tests/v8_candidate_assurance_test_support.py
- Read: all #113–#117 focused tests and #137 revalidation tests
- Read: docs/superpowers/plans/2026-08-03-gwo-v8-campaign-watchdog.md
- Read: docs/superpowers/plans/2026-08-03-gwo-v8-ga-delivery-program.md
- Evidence destination: exact PR descriptions, CI URLs, and Issue #114/#115 comments; the master release plan owns docs/releases/v8.0.0-beta.2.md.

**Interfaces**

- Consumes: merged foundation, #113, #114, #115, #116, #117, and #137.
- Produces: auditable focused/full validation and a Beta2 exit decision.

- [ ] **Step 1: Run the foundation baseline required by #113**

~~~powershell
git fetch origin main
git show origin/main:skills/orchestrator/scripts/gwo_v8/candidate_gate.py | Select-String 'class CandidateReceipt'
git show origin/main:skills/orchestrator/scripts/gwo_v8/execution_kernel.py | Select-String 'candidate_receipt: CandidateReceipt | None'
git show origin/main:tests/test_v8_candidate_receipt_kernel.py | Select-String 'test_kernel_persists_exact_candidate_receipt_at_run_root'
py -3.13 -m pytest tests/test_v8_candidate_receipt_kernel.py -q
~~~

Expected: all three symbol/test checks and the exact Kernel baseline pass. If not, #113 must stop; it must not define another receipt.

- [ ] **Step 2: Run focused #114/#115 tests**

~~~powershell
py -3.13 -m pytest tests/test_v8_candidate_receipt_foundation.py tests/test_v8_candidate_receipt_kernel.py -q
py -3.13 -m pytest tests/test_v8_candidate_git_readback.py tests/test_v8_candidate_gate.py tests/test_v8_candidate_gate_public.py -q
py -3.13 -m pytest tests/test_v8_candidate_assurance_standard.py tests/test_v8_candidate_review_reuse.py tests/test_v8_candidate_gate_acceptance.py -q
py -3.13 -m pytest tests/test_v8_candidate_strict_review.py tests/test_v8_review_finding_ledger.py tests/test_v8_repair_packet.py tests/test_v8_repair_verification.py -q
py -3.13 -m pytest tests/test_v8_candidate_budget_kernel.py -q
~~~

Expected: PASS; fixture discovery uses the imported pytest plugin, the direct run-state key is exact, Review reuse is identity-bound, and Repair never reopens a full Formal Review.

- [ ] **Step 3: Run repository gates**

~~~powershell
py -3.13 -m pytest -q
py -3.13 scripts/quick_validate.py
py -3.13 scripts/sync_orchestrator.py --check
git diff --check
~~~

Expected: full pytest, package validation, synchronization, and whitespace checks pass.

- [ ] **Step 4: Read back Issue states and bodies without mutation**

~~~powershell
gh issue view 113 --repo NOirBRight/github-work-orchestrator --json number,state,body,comments
gh issue view 114 --repo NOirBRight/github-work-orchestrator --json number,state,body,comments
gh issue view 115 --repo NOirBRight/github-work-orchestrator --json number,state,body,comments
gh issue view 116 --repo NOirBRight/github-work-orchestrator --json number,state,body,comments
gh issue view 117 --repo NOirBRight/github-work-orchestrator --json number,state,body,comments
gh issue view 137 --repo NOirBRight/github-work-orchestrator --json number,state,body,comments
~~~

Expected for Beta2: #113–#117 and #137 are CLOSED by merged, read-back Results, with no open native blocker. Otherwise record Beta2 as not admitted.

- [ ] **Step 5: Record the Beta2 exit gate**

Run the following read-only generator from the repository root. It obtains the
main SHA, successful CI URL/jobs, focused and full pytest counts, exact Issue
bodies/comments, and merged closing-PR identities from Git, pytest JUnit, and
GitHub read-back. It writes complete destination bodies plus a destination
manifest under the system temporary directory. It deliberately performs no
GitHub write, tag, release, or repository-file mutation; the release-train
owner uses the generated body files at the exact URLs recorded in
`destinations.json`.

~~~powershell
$ErrorActionPreference = 'Stop'
$repository = 'NOirBRight/github-work-orchestrator'
$owner = 'NOirBRight'
$name = 'github-work-orchestrator'

function Invoke-Native {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [Parameter(Mandatory = $true)][string[]]$ArgumentList
    )
    $output = & $FilePath @ArgumentList 2>&1
    if ($LASTEXITCODE -ne 0) {
        throw "$FilePath failed with exit $LASTEXITCODE`n$($output -join "`n")"
    }
    return @($output)
}

function Write-Utf8 {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Content
    )
    [System.IO.File]::WriteAllText(
        $Path,
        $Content.TrimEnd() + "`n",
        [System.Text.UTF8Encoding]::new($false)
    )
}

function Get-CanonicalJsonDigest {
    param([Parameter(Mandatory = $true)][object]$Value)
    $json = $Value | ConvertTo-Json -Depth 100 -Compress
    $bytes = [System.Text.Encoding]::UTF8.GetBytes($json)
    return [Convert]::ToHexString(
        [System.Security.Cryptography.SHA256]::HashData($bytes)
    ).ToLowerInvariant()
}

function Read-JUnitSummary {
    param([Parameter(Mandatory = $true)][string]$Path)
    [xml]$document = Get-Content -LiteralPath $Path -Raw
    $suite = if ($null -ne $document.testsuites) {
        $document.testsuites
    } else {
        $document.testsuite
    }
    $tests = [int]$suite.tests
    $failures = [int]$suite.failures
    $errors = [int]$suite.errors
    $skipped = [int]$suite.skipped
    return [pscustomobject]@{
        tests = $tests
        passed = $tests - $failures - $errors - $skipped
        failures = $failures
        errors = $errors
        skipped = $skipped
    }
}

function Read-ClosingPullRequest {
    param([Parameter(Mandatory = $true)][int]$IssueNumber)
    $query = @'
query($owner: String!, $name: String!, $number: Int!) {
  repository(owner: $owner, name: $name) {
    issue(number: $number) {
      closedByPullRequestsReferences(first: 20) {
        nodes {
          number
          url
          merged
          mergedAt
          mergeCommit { oid }
        }
      }
    }
  }
}
'@
    $raw = Invoke-Native -FilePath 'gh' -ArgumentList @(
        'api', 'graphql',
        '-f', "query=$query",
        '-F', "owner=$owner",
        '-F', "name=$name",
        '-F', "number=$IssueNumber"
    )
    $response = ($raw -join "`n") | ConvertFrom-Json
    $nodes = @(
        $response.data.repository.issue.closedByPullRequestsReferences.nodes |
            Where-Object { $_.merged -eq $true -and $null -ne $_.mergeCommit.oid } |
            Sort-Object mergedAt -Descending
    )
    if ($nodes.Count -lt 1) {
        throw "Issue #$IssueNumber has no merged closing PR read-back."
    }
    $closing = $nodes[0]
    $details = (
        Invoke-Native -FilePath 'gh' -ArgumentList @(
            'pr', 'view', [string]$closing.number,
            '--repo', $repository,
            '--json', 'number,url,body,mergedAt,mergeCommit'
        )
    ) -join "`n" | ConvertFrom-Json
    if ($details.mergeCommit.oid -ne $closing.mergeCommit.oid) {
        throw "Issue #$IssueNumber closing PR merge identity changed during read-back."
    }
    return $details
}

Invoke-Native -FilePath 'git' -ArgumentList @('fetch', 'origin', 'main') | Out-Host
$mainSha = (
    Invoke-Native -FilePath 'git' -ArgumentList @('rev-parse', 'origin/main')
)[-1].Trim()
if ($mainSha -notmatch '^[0-9a-f]{40}$') {
    throw 'origin/main did not resolve to an exact SHA-1 commit.'
}

$outputDirectory = Join-Path $env:TEMP (
    'gwo-v8-candidate-assurance-beta2-' + $mainSha.Substring(0, 12)
)
New-Item -ItemType Directory -Force -Path $outputDirectory | Out-Null
$focusedXml = Join-Path $outputDirectory 'candidate-focused.xml'
$fullXml = Join-Path $outputDirectory 'repository-full.xml'

$focusedTests = @(
    'tests/test_v8_candidate_receipt_foundation.py',
    'tests/test_v8_candidate_receipt_kernel.py',
    'tests/test_v8_candidate_git_readback.py',
    'tests/test_v8_candidate_gate.py',
    'tests/test_v8_candidate_gate_public.py',
    'tests/test_v8_candidate_assurance_standard.py',
    'tests/test_v8_candidate_review_reuse.py',
    'tests/test_v8_candidate_gate_acceptance.py',
    'tests/test_v8_candidate_strict_review.py',
    'tests/test_v8_review_finding_ledger.py',
    'tests/test_v8_repair_packet.py',
    'tests/test_v8_repair_verification.py',
    'tests/test_v8_candidate_budget_kernel.py'
)
$focusedArguments = @('-3.13', '-m', 'pytest') + $focusedTests + @(
    '--junitxml', $focusedXml, '-q'
)
Invoke-Native -FilePath 'py' -ArgumentList $focusedArguments | Out-Host
Invoke-Native -FilePath 'py' -ArgumentList @(
    '-3.13', '-m', 'pytest', '--junitxml', $fullXml, '-q'
) | Out-Host
Invoke-Native -FilePath 'py' -ArgumentList @(
    '-3.13', 'scripts/quick_validate.py'
) | Out-Host
Invoke-Native -FilePath 'py' -ArgumentList @(
    '-3.13', 'scripts/sync_orchestrator.py', '--check'
) | Out-Host
Invoke-Native -FilePath 'git' -ArgumentList @('diff', '--check') | Out-Host

$focused = Read-JUnitSummary -Path $focusedXml
$full = Read-JUnitSummary -Path $fullXml
if (
    $focused.failures -ne 0 -or $focused.errors -ne 0 -or
    $full.failures -ne 0 -or $full.errors -ne 0
) {
    throw 'Beta2 evidence cannot be generated from a failing pytest read-back.'
}

$issues = @{}
foreach ($number in @(113, 114, 115, 116, 117, 137)) {
    $issue = (
        Invoke-Native -FilePath 'gh' -ArgumentList @(
            'issue', 'view', [string]$number,
            '--repo', $repository,
            '--json', 'number,state,url,title,closedAt,body,comments'
        )
    ) -join "`n" | ConvertFrom-Json
    if ($issue.state -ne 'CLOSED' -or $null -eq $issue.closedAt) {
        throw "Issue #$number is not closed by exact read-back."
    }
    $issues[$number] = $issue
}

$pr114 = Read-ClosingPullRequest -IssueNumber 114
$pr115 = Read-ClosingPullRequest -IssueNumber 115

$runs = @(
    (
        Invoke-Native -FilePath 'gh' -ArgumentList @(
            'run', 'list',
            '--repo', $repository,
            '--commit', $mainSha,
            '--workflow', 'GWO CI',
            '--status', 'success',
            '--limit', '1',
            '--json', 'databaseId,url,headSha,conclusion'
        )
    ) -join "`n" | ConvertFrom-Json
)
if (
    $runs.Count -ne 1 -or
    $runs[0].headSha -ne $mainSha -or
    $runs[0].conclusion -ne 'success'
) {
    throw 'No successful GWO CI run exists for the exact origin/main SHA.'
}
$ci = $runs[0]
$ciDetails = (
    Invoke-Native -FilePath 'gh' -ArgumentList @(
        'run', 'view', [string]$ci.databaseId,
        '--repo', $repository,
        '--json', 'databaseId,url,headSha,conclusion,jobs'
    )
) -join "`n" | ConvertFrom-Json
$badJobs = @(
    $ciDetails.jobs | Where-Object {
        $_.conclusion -notin @('success', 'skipped')
    }
)
if ($badJobs.Count -ne 0) {
    throw 'Exact-main GWO CI contains a non-successful required job.'
}
$jobSummary = (
    $ciDetails.jobs |
        Sort-Object name |
        ForEach-Object { "$($_.name)=$($_.conclusion)" }
) -join ', '

$issue114Digest = Get-CanonicalJsonDigest -Value $issues[114]
$issue115Digest = Get-CanonicalJsonDigest -Value $issues[115]
$pr114BodyDigest = Get-CanonicalJsonDigest -Value $pr114.body
$pr115BodyDigest = Get-CanonicalJsonDigest -Value $pr115.body
$generatedAt = (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')

$commonEvidence = @"
GWO V8 Candidate Assurance Beta2 Evidence

- Generated at UTC: $generatedAt
- Exact origin/main SHA: $mainSha
- Exact successful GWO CI: $($ci.url)
- GWO CI jobs: $jobSummary
- Candidate-focused pytest: $($focused.passed)/$($focused.tests) passed; $($focused.skipped) skipped; 0 failures; 0 errors
- Full repository pytest: $($full.passed)/$($full.tests) passed; $($full.skipped) skipped; 0 failures; 0 errors
- Package validation: py -3.13 scripts/quick_validate.py passed
- Package synchronization: py -3.13 scripts/sync_orchestrator.py --check passed
- Whitespace gate: git diff --check passed
"@

$issue114Body = @"
$commonEvidence

Issue #114 Candidate assurance read-back

- Issue: $($issues[114].url)
- State/closedAt: $($issues[114].state) / $($issues[114].closedAt)
- Exact body/comments read-back digest: $issue114Digest
- Existing comment count at read-back: $(@($issues[114].comments).Count)
- Closing PR: $($pr114.url)
- Closing merge SHA: $($pr114.mergeCommit.oid)
- Closing PR description read-back digest: $pr114BodyDigest
- Foundation Kernel baseline: tests/test_v8_candidate_receipt_kernel.py
- Proven contract: authoritative base/Candidate commit and tree read-back; one CandidateDiffRecordV1 Artifact; deterministic stop before Review; zero-call NO_REVIEW; one-call STANDARD; exact ReviewSubject reuse; persisted CandidateReceipt binding; AcceptedCandidateReceipt delivery identity.

Beta2 disposition: #114 evidence is admitted for the feature-complete preview only. It does not switch the default writer and is not production admission.
"@

$issue115Body = @"
$commonEvidence

Issue #115 Review and Repair read-back

- Issue: $($issues[115].url)
- State/closedAt: $($issues[115].state) / $($issues[115].closedAt)
- Exact body/comments read-back digest: $issue115Digest
- Existing comment count at read-back: $(@($issues[115].comments).Count)
- Closing PR: $($pr115.url)
- Closing merge SHA: $($pr115.mergeCommit.oid)
- Closing PR description read-back digest: $pr115BodyDigest
- Proven contract: bounded STRICT specialist selection; one same-Subject review_strong retry for invalid transport; complete ReviewFindingLedger and dispositions; RepairPacket and RepairDelta scope; repair_verify without reopening Formal Review; three-distinct-Candidate bound; durable restart-preserved receipt history and Slot release.

Beta2 disposition: #115 evidence is admitted for the feature-complete preview only. It does not perform cutover and is not GA evidence.
"@

$exitGateBody = @"
$commonEvidence

Beta2 exit decision: ADMITTED AS FEATURE-COMPLETE PREVIEW

1. Beta1 is metadata and tracker repair only and has no Lean V8 production admission.
2. tests/test_v8_candidate_receipt_kernel.py is the foundation Kernel baseline.
3. #113 consumes the merged persisted CandidateReceipt through read-only Kernel projection and does not construct, persist, or repair the receipt.
4. #114 proves authoritative exact base/Candidate commit/tree read-back, one complete CandidateDiffRecordV1 Artifact, deterministic stop-before-Review, NO_REVIEW/STANDARD bounds, exact Review reuse, and accepted-Candidate receipt binding.
5. #115 proves STRICT/review_strong bounds, complete Finding ledger/dispositions, packet/delta scope, repair_verify without full-review reopen, and Candidate/binding bounds.
6. #116, #117, and #137 are CLOSED by exact Issue read-back; isolated production V3 composition is covered by the successful full repository gate for $mainSha.
7. Beta2 is a feature-complete preview only. It does not switch the default writer or perform cutover.
8. Beta3 still requires Guard and Activation read-back. GA still requires a real public-API root Canary plus exact target, Activation, and default-writer read-back.

Issue closure read-back:
- #113: $($issues[113].url) / $($issues[113].closedAt)
- #114: $($issues[114].url) / $($issues[114].closedAt)
- #115: $($issues[115].url) / $($issues[115].closedAt)
- #116: $($issues[116].url) / $($issues[116].closedAt)
- #117: $($issues[117].url) / $($issues[117].closedAt)
- #137: $($issues[137].url) / $($issues[137].closedAt)
"@

$issue114Path = Join-Path $outputDirectory 'issue-114-comment.md'
$issue115Path = Join-Path $outputDirectory 'issue-115-comment.md'
$pr114Path = Join-Path $outputDirectory ("pr-$($pr114.number)-description-block.md")
$pr115Path = Join-Path $outputDirectory ("pr-$($pr115.number)-description-block.md")
$exitGatePath = Join-Path $outputDirectory 'beta2-exit-gate.md'
$manifestPath = Join-Path $outputDirectory 'destinations.json'

Write-Utf8 -Path $issue114Path -Content $issue114Body
Write-Utf8 -Path $issue115Path -Content $issue115Body
Write-Utf8 -Path $pr114Path -Content $issue114Body
Write-Utf8 -Path $pr115Path -Content $issue115Body
Write-Utf8 -Path $exitGatePath -Content $exitGateBody

$manifest = [ordered]@{
    generated_at_utc = $generatedAt
    exact_main_sha = $mainSha
    ci_url = $ci.url
    beta2_exit_gate = [ordered]@{
        destination_owner = 'docs/releases/v8.0.0-beta.2.md is owned by the master release plan'
        source_body_file = $exitGatePath
    }
    issue_114_comment = [ordered]@{
        destination_url = $issues[114].url
        body_file = $issue114Path
    }
    issue_115_comment = [ordered]@{
        destination_url = $issues[115].url
        body_file = $issue115Path
    }
    pr_114_description_block = [ordered]@{
        destination_url = $pr114.url
        existing_body_digest = $pr114BodyDigest
        append_only_body_file = $pr114Path
    }
    pr_115_description_block = [ordered]@{
        destination_url = $pr115.url
        existing_body_digest = $pr115BodyDigest
        append_only_body_file = $pr115Path
    }
}
Write-Utf8 -Path $manifestPath -Content (
    $manifest | ConvertTo-Json -Depth 10
)

Write-Output "Candidate Assurance Beta2 evidence directory: $outputDirectory"
Write-Output "Destination manifest: $manifestPath"
Get-Content -LiteralPath $manifestPath
~~~

Expected: the command exits zero only when all six Issues are closed, both
closing PRs and merge SHAs read back, focused/full pytest and package gates are
green, and one successful GWO CI run matches the exact main SHA. The five
Markdown files contain complete values rather than substitution tokens;
`destinations.json` maps each body to the exact Issue/PR URL and leaves
publication to the owning release-train step.

- [ ] **Step 6: Read back exact green main CI for the master release plan**

~~~powershell
$sha = git rev-parse origin/main
$run = gh run list --repo NOirBRight/github-work-orchestrator --commit $sha --workflow 'GWO CI' --status success --limit 1 --json databaseId,url,headSha,conclusion | ConvertFrom-Json
if ($run.Count -ne 1 -or $run.headSha -ne $sha -or $run.conclusion -ne 'success') {
    throw 'Beta2 evidence requires successful GWO CI readback for the exact main SHA.'
}
gh run view $run.databaseId --repo NOirBRight/github-work-orchestrator --json databaseId,url,headSha,conclusion,jobs
~~~

Expected: the evidence names the exact green main SHA and CI URL. The master GA delivery plan decides whether to publish the immutable Beta2 prerelease.

## Acceptance Coverage and Self-Review

- #114 authoritative reference, complete diff, private receipt, checks, Assurance, Standard/no-review Review, accepted receipt, Artifact reuse, capability proof, and #137 boundary are covered by Tasks 1–5 and named tests.
- #115 Strict selection, review_strong transport retry, complete ReviewFinding ledger/dispositions, RepairPacket, RepairDelta, bounded scope, no full-review reopen, Candidate budget, and restart preservation are covered by Tasks 6–9 and named tests.
- #113 depends only on the merged Task 1 foundation. It does not require #114's later CandidateGate work to merge.
- The shared generated manifest makes the package lane serial even though source ownership is disjoint: Task 1, then #113, then remaining #114, then #115. Only read-only review or docs-only commits with no package/manifest write may overlap that lane.
- Foundation does not define WatchdogCampaignSnapshot, KernelWatchdogReadback, stale/liveness observations, trusted-progress digests, or Watchdog scheduling.
- The exact persisted path is state["runs"][ticket]["candidate_receipt"], and CandidateReceipt.canonical() exposes candidate_tree_oid at its root.
- kernel_with_candidate_receipt returns exactly (kernel, effects, campaign, receipt), creates state before yielding, and keeps effects.executed inspectable.
- No remaining #114 task edits execution_kernel.py; only the post-#113 Task 8 budget adapter does.
- ReviewSubject repair fields are defined before Task 9 uses them.
- Every Python command uses py -3.13.
- Before handoff, run a placeholder-marker scan, an undefined-interface/name scan, git diff --check, and git status --short; confirm only the assigned plan file is modified by this planning task.
