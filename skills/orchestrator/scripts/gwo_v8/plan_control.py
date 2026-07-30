"""PlanControl: compile and activate one immutable PlanSpec v3 revision.

This module owns source readback, one Campaign Planning Pass, immutable
PlanSpec compilation, Ticket claims, and activation readback.  RuntimeGateway
owns every Runtime concern.  In particular, this module sees only its planning
subject plus opaque preflight/progress receipts; it has no provider, Profile,
session, Workspace, or command seam.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
import re
from typing import Any, Mapping, Protocol, Sequence

from ._canonical import CanonicalJsonError, canonical_bytes, digest_bytes, digest_value, load_canonical_json
from .runtime_gateway import CampaignPlanningSubject, PlanningPreflightReceipt, PlanningReceipt


_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_VERSIONED_IDENTIFIER = re.compile(r"^[a-z][a-z0-9]*(?:[.-][a-z0-9]+)*\.v[1-9][0-9]*$")
_CAPABILITY = re.compile(r"^[a-z][a-z0-9_]*(?:[.-][a-z0-9][a-z0-9_-]*)*$")
_AUTO_PREVIOUS = object()
_TRIAGE = frozenset({"needs-triage", "needs-info", "ready-for-agent", "ready-for-human", "wontfix"})
_POLICY_ROLES = ("campaign", "worker", "recovery_worker", "review")
_OPERATION_ROOTS = frozenset({"artifact", "ci", "git", "github", "repository", "workspace"})
_RESOURCE_ROOTS = frozenset({"artifact", "campaign", "candidate", "repository", "review", "target", "work-run"})


class PlanControlError(RuntimeError):
    """A named fail-closed PlanControl outcome."""

    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class CampaignHandle:
    """The stable opaque Campaign identity, independent of a Plan Revision."""

    repository: str
    campaign_key: str


@dataclass(frozen=True)
class DecisionFinding:
    code: str
    detail: str
    ticket_key: str | None = None


class PlanControlDecision(PlanControlError):
    """A Planning Pass named a durable human Decision instead of a Plan."""

    def __init__(self, snapshot_digest: str, findings: tuple[DecisionFinding, ...]):
        super().__init__("PLAN_CONTROL_DECISION_REQUIRED", "Campaign Planning requires a named Decision")
        self.snapshot_digest = snapshot_digest
        self.findings = findings
        self.decision_digest = digest_value(
            {
                "snapshot_digest": snapshot_digest,
                "findings": [finding.__dict__ for finding in findings],
            }
        )


@dataclass(frozen=True)
class PlanRevision:
    repository: str
    campaign_key: str
    snapshot_digest: str
    canonical_bytes: bytes
    digest: str

    @property
    def plan_spec(self) -> dict[str, Any]:
        value = load_canonical_json(self.canonical_bytes)
        assert type(value) is dict
        return value


@dataclass(frozen=True)
class ActivationReceipt:
    repository: str
    campaign_key: str
    revision_digest: str
    expected_previous_revision_digest: str | None
    writer_generation: str
    ticket_keys: tuple[str, ...]


@dataclass(frozen=True)
class TicketClaimProof:
    """Read-only proof of one repository-global Ticket claim."""

    ticket_key: str
    repository: str
    campaign_key: str
    plan_revision_digest: str


@dataclass(frozen=True)
class ActivePlanReadback:
    """Internal #110 seam reconstructed only from activated immutable facts."""

    handle: CampaignHandle
    current_revision_digest: str
    plan_spec_bytes: bytes
    activation_receipt: ActivationReceipt
    claim_proofs: tuple[TicketClaimProof, ...]


@dataclass(frozen=True)
class _PlanningAttempt:
    handle: CampaignHandle
    ticket_keys: tuple[str, ...]
    expected_previous_revision_digest: str | None
    snapshot_bytes: bytes
    snapshot_artifact_digest: str
    policy_witness_digest: str
    planning_request_artifact_digest: str
    subject: CampaignPlanningSubject
    planning_receipt_digest: str | None = None
    planning_output_artifact_digest: str | None = None
    intent_bytes: bytes | None = None
    revision: PlanRevision | None = None


class CampaignSnapshotSource(Protocol):
    """The GitHub/Ticket adapter, deliberately outside PlanControl's policy."""

    def snapshot(self, repository: str, ready_refs: tuple[str, ...]) -> Mapping[str, Any]: ...


class PlanningArtifacts(Protocol):
    """Digest-addressed host Artifact port shared with RuntimeGateway."""

    def put_canonical(self, value: Any) -> Any: ...

    def get(self, digest: str) -> Any: ...


class CampaignPlanningGateway(Protocol):
    """The intentionally tiny #111 caller surface used by PlanControl."""

    def planning_preflight(self, subject: CampaignPlanningSubject) -> PlanningPreflightReceipt: ...

    def progress(self, subject: CampaignPlanningSubject, preflight: PlanningPreflightReceipt) -> PlanningReceipt: ...


class PlanControlRepository(Protocol):
    """Durable facts required by PlanControl's claim and activation boundary."""

    def active_receipt(self, handle: CampaignHandle) -> ActivationReceipt | None: ...

    def read_attempt(self, handle: CampaignHandle, expected_previous_revision_digest: str | None) -> _PlanningAttempt | None: ...

    def save_attempt(self, attempt: _PlanningAttempt) -> _PlanningAttempt: ...

    def reserve_claims(self, handle: CampaignHandle, ticket_keys: tuple[str, ...], revision_digest: str) -> None: ...

    def publish_revision(self, revision: PlanRevision) -> None: ...

    def read_revision(self, digest: str) -> PlanRevision | None: ...

    def activate(self, receipt: ActivationReceipt) -> None: ...

    def read_activation(self, handle: CampaignHandle) -> ActivationReceipt | None: ...

    def read_claim_proofs(self) -> tuple[TicketClaimProof, ...]: ...


class InMemoryPlanRepository:
    """Deterministic repository double with the same CAS/readback semantics."""

    def __init__(self, *, writer_generation: str):
        _text(writer_generation, "writer_generation")
        self.writer_generation = writer_generation
        self.attempts: dict[tuple[str, str, str | None], _PlanningAttempt] = {}
        self.claims: dict[str, str] = {}
        self._claim_campaigns: dict[str, tuple[str, str]] = {}
        self.revisions: dict[str, PlanRevision] = {}
        self.activations: dict[tuple[str, str], ActivationReceipt] = {}

    @staticmethod
    def _attempt_key(handle: CampaignHandle, previous: str | None) -> tuple[str, str, str | None]:
        return (handle.repository, handle.campaign_key, previous)

    def active_receipt(self, handle: CampaignHandle) -> ActivationReceipt | None:
        return self.activations.get((handle.repository, handle.campaign_key))

    def read_attempt(self, handle: CampaignHandle, expected_previous_revision_digest: str | None) -> _PlanningAttempt | None:
        return self.attempts.get(self._attempt_key(handle, expected_previous_revision_digest))

    def save_attempt(self, attempt: _PlanningAttempt) -> _PlanningAttempt:
        key = self._attempt_key(attempt.handle, attempt.expected_previous_revision_digest)
        existing = self.attempts.get(key)
        if existing is not None and existing.snapshot_bytes != attempt.snapshot_bytes:
            raise PlanControlError("PLANNING_ATTEMPT_IDENTITY_CONFLICT", "Campaign attempt changed its immutable snapshot")
        self.attempts[key] = attempt
        return attempt

    def reserve_claims(self, handle: CampaignHandle, ticket_keys: tuple[str, ...], revision_digest: str) -> None:
        owner = (handle.repository, handle.campaign_key)
        conflicts = sorted(
            key
            for key in ticket_keys
            if key in self._claim_campaigns and self._claim_campaigns[key] != owner
        )
        if conflicts:
            raise PlanControlError("TICKET_CLAIM_CONFLICT", "Ticket claims overlap: " + ", ".join(conflicts))
        for key in ticket_keys:
            self.claims[key] = revision_digest
            self._claim_campaigns[key] = owner

    def publish_revision(self, revision: PlanRevision) -> None:
        existing = self.revisions.get(revision.digest)
        if existing is not None and existing != revision:
            raise PlanControlError("PLAN_PUBLICATION_CONFLICT", "Plan revision digest resolves to different bytes")
        self.revisions[revision.digest] = revision

    def read_revision(self, digest: str) -> PlanRevision | None:
        return self.revisions.get(digest)

    def activate(self, receipt: ActivationReceipt) -> None:
        handle_key = (receipt.repository, receipt.campaign_key)
        current = self.activations.get(handle_key)
        current_digest = None if current is None else current.revision_digest
        if current_digest != receipt.expected_previous_revision_digest:
            if current == receipt:
                return
            raise PlanControlError("ACTIVATION_CAS_CONFLICT", "Campaign active revision differs from the expected previous revision")
        self.activations[handle_key] = receipt

    def read_activation(self, handle: CampaignHandle) -> ActivationReceipt | None:
        return self.active_receipt(handle)

    def read_claim_proofs(self) -> tuple[TicketClaimProof, ...]:
        proofs = []
        for ticket_key, revision_digest in self.claims.items():
            repository, campaign_key = self._claim_campaigns[ticket_key]
            proofs.append(
                TicketClaimProof(
                    ticket_key=ticket_key,
                    repository=repository,
                    campaign_key=campaign_key,
                    plan_revision_digest=revision_digest,
                )
            )
        return tuple(sorted(proofs, key=lambda proof: proof.ticket_key))


class PlanControl:
    """One bounded Planning Pass followed by deterministic PlanSpec activation."""

    def __init__(self, *, source: CampaignSnapshotSource, artifacts: PlanningArtifacts, gateway: CampaignPlanningGateway, repository: PlanControlRepository, max_snapshot_bytes: int = 1_048_576):
        if type(max_snapshot_bytes) is not int or max_snapshot_bytes < 1:
            raise PlanControlError("PLAN_CONTROL_COMPOSITION_INVALID", "max_snapshot_bytes must be a positive integer")
        self._source = source
        self._artifacts = artifacts
        self._gateway = gateway
        self._repository = repository
        self._max_snapshot_bytes = max_snapshot_bytes

    def start(self, repository: str, ready_refs: Sequence[str], options: object = None, *, campaign_key: str | None = None, expected_previous_revision_digest: str | None | object = _AUTO_PREVIOUS) -> CampaignHandle:
        """Create or recover one immutable PlanSpec v3 Campaign revision.

        Runtime options are deliberately absent here: host composition binds
        them in RuntimeGateway's #111 preflight, outside PlanSpec.
        """

        if options is not None:
            raise PlanControlError("START_OPTIONS_INVALID", "Runtime options belong to RuntimeGateway host composition, not PlanControl")
        repository = _text(repository, "repository")
        refs = _ready_refs(ready_refs)
        key = campaign_key or "campaign:" + digest_value({"repository": repository, "ready_refs": list(refs)})[:24]
        handle = CampaignHandle(repository, _text(key, "campaign_key"))
        active = self._repository.active_receipt(handle)
        if expected_previous_revision_digest is _AUTO_PREVIOUS:
            if active is not None and active.ticket_keys == refs:
                self.read_active(handle)
                return handle
            expected = None if active is None else active.revision_digest
        else:
            expected = _optional_digest(expected_previous_revision_digest, "expected_previous_revision_digest")

        attempt = self._repository.read_attempt(handle, expected)
        if attempt is None:
            attempt = self._new_attempt(handle, refs, expected)
            attempt = self._repository.save_attempt(attempt)
        elif attempt.ticket_keys != refs:
            raise PlanControlError("PLANNING_ATTEMPT_IDENTITY_CONFLICT", "Campaign attempt has different selected Tickets")

        self._verify_attempt_artifacts(attempt)

        if attempt.intent_bytes is None:
            attempt = self._obtain_one_planning_intent(attempt)
            # A live semantic turn remains pending; its stable handle gives
            # #110 a durable continuation point without inventing a revision.
            if attempt.intent_bytes is None:
                return handle

        intent = _intent_from_bytes(attempt.intent_bytes, attempt.snapshot_bytes)
        findings = tuple(DecisionFinding(**item) for item in intent["decision_requirements"])
        if findings:
            raise PlanControlDecision(attempt.snapshot_artifact_digest, findings)

        revision = attempt.revision or _compile_plan(attempt.snapshot_bytes, attempt.snapshot_artifact_digest, attempt.intent_bytes, handle)
        if attempt.revision is None:
            attempt = self._repository.save_attempt(replace(attempt, revision=revision))

        # Both the complete immutable PlanSpec and the full Policy Witness are
        # digest-addressed facts.  RuntimeGateway and later permission work
        # can reconstruct authority from these exact bytes without accepting a
        # mutable PlanControl projection.
        plan_artifact_digest = _put_canonical(self._artifacts, revision.plan_spec)
        if plan_artifact_digest != revision.digest or _canonical(self._artifacts.get(plan_artifact_digest)) != revision.plan_spec:
            raise PlanControlError("PLAN_READBACK_INVALID", "PlanSpec Artifact does not read back at its revision digest")

        # Claims happen only after preflight and the sole semantic Planning
        # Pass, but before immutable Plan publication as required by #109.
        self._repository.reserve_claims(handle, refs, revision.digest)
        self._repository.publish_revision(revision)
        if self._repository.read_revision(revision.digest) != revision:
            raise PlanControlError("PLAN_READBACK_INVALID", "Published Plan Revision does not read back exactly")

        receipt = ActivationReceipt(
            repository=handle.repository,
            campaign_key=handle.campaign_key,
            revision_digest=revision.digest,
            expected_previous_revision_digest=expected,
            writer_generation=self._writer_generation(),
            ticket_keys=refs,
        )
        self._repository.activate(receipt)
        if self._repository.read_activation(handle) != receipt:
            raise PlanControlError("ACTIVATION_READBACK_INVALID", "Activation Receipt does not read back exactly")
        return handle

    def read_active(self, handle: CampaignHandle) -> ActivePlanReadback:
        """Read one active revision without mutable-source, claim, or Runtime work.

        ExecutionKernel consumes this internal port before it admits Work Runs.
        It deliberately has no transition or recovery behavior.
        """

        if type(handle) is not CampaignHandle:
            raise PlanControlError("ACTIVE_READBACK_INVALID", "active readback requires an exact CampaignHandle")
        receipt = self._repository.read_activation(handle)
        if receipt is None:
            raise PlanControlError("ACTIVATION_PENDING", "Campaign has no read-backed Activation Receipt")
        if receipt.repository != handle.repository or receipt.campaign_key != handle.campaign_key:
            raise PlanControlError("ACTIVATION_READBACK_INVALID", "Activation Receipt does not bind the CampaignHandle")
        revision = self._repository.read_revision(receipt.revision_digest)
        if revision is None or revision.repository != handle.repository or revision.campaign_key != handle.campaign_key or digest_bytes(revision.canonical_bytes) != receipt.revision_digest:
            raise PlanControlError("PLAN_READBACK_INVALID", "Activated Plan Revision does not read back exactly")
        _validate_plan_spec(revision.canonical_bytes)
        if _canonical(self._artifacts.get(receipt.revision_digest)) != revision.plan_spec:
            raise PlanControlError("PLAN_READBACK_INVALID", "PlanSpec Artifact does not read back exactly")
        self._verify_policy_witness(revision.plan_spec)
        proofs = self._repository.read_claim_proofs()
        active_proofs = tuple(
            proof
            for proof in proofs
            if proof.repository == handle.repository and proof.campaign_key == handle.campaign_key
        )
        if (
            tuple(proof.ticket_key for proof in active_proofs) != receipt.ticket_keys
            or any(proof.plan_revision_digest != receipt.revision_digest for proof in active_proofs)
        ):
            raise PlanControlError("TICKET_CLAIM_READBACK_INVALID", "Activated Ticket claims do not read back for the exact Plan Revision")
        return ActivePlanReadback(handle, receipt.revision_digest, revision.canonical_bytes, receipt, proofs)

    def _new_attempt(self, handle: CampaignHandle, refs: tuple[str, ...], expected: str | None) -> _PlanningAttempt:
        snapshot = _normalize_snapshot(self._source.snapshot(handle.repository, refs), handle.repository, refs)
        policy_witness = {key: value for key, value in snapshot["policy"].items() if key != "digest"}
        policy_artifact_digest = _put_canonical(self._artifacts, policy_witness)
        if policy_artifact_digest != snapshot["policy"]["digest"]:
            raise PlanControlError("POLICY_WITNESS_INVALID", "Policy Witness Artifact digest differs from its frozen witness digest")
        snapshot_bytes = canonical_bytes(snapshot)
        if len(snapshot_bytes) > self._max_snapshot_bytes:
            raise PlanControlError("SPLIT_CAMPAIGN_REQUIRED", "Campaign snapshot exceeds the configured Planning input bound")
        snapshot_ref = _put_canonical(self._artifacts, snapshot)
        if snapshot_ref != digest_bytes(snapshot_bytes):
            raise PlanControlError("SNAPSHOT_ARTIFACT_MISMATCH", "Snapshot Artifact digest differs from canonical snapshot bytes")
        policy_digest = policy_artifact_digest
        stable_action_id = "planning:" + digest_value({"handle": handle.__dict__, "snapshot_digest": snapshot_ref, "policy_witness_digest": policy_digest, "expected_previous_revision_digest": expected})
        provisional = CampaignPlanningSubject(
            repository=handle.repository,
            campaign_key=handle.campaign_key,
            campaign_handle=_handle_ref(handle),
            expected_previous_plan_revision_digest=expected,
            snapshot_artifact_digest=snapshot_ref,
            policy_witness_digest=policy_digest,
            planning_request_artifact_digest="0" * 64,
            stable_action_id=stable_action_id,
        )
        request_ref = _put_canonical(
            self._artifacts,
            {
                "schema_version": "gwo.runtime.prompt.v1",
                "subject_digest": provisional.prompt_binding_digest,
                "authority_digest": policy_digest,
                "payload": {
                    "schema_version": 1,
                    "snapshot_artifact_digest": snapshot_ref,
                    "policy_witness_digest": policy_digest,
                },
            },
        )
        subject = replace(provisional, planning_request_artifact_digest=request_ref)
        return _PlanningAttempt(handle, refs, expected, snapshot_bytes, snapshot_ref, policy_digest, request_ref, subject)

    def _obtain_one_planning_intent(self, attempt: _PlanningAttempt) -> _PlanningAttempt:
        # The preflight is intentionally before both claim reservation and
        # semantic progress.  Its receipt is opaque, but exact subject/action
        # binding is mechanically checked before it is consumed.
        preflight = self._gateway.planning_preflight(attempt.subject)
        _validate_preflight(preflight, attempt.subject)
        receipt = self._gateway.progress(attempt.subject, preflight)
        _validate_planning_receipt(receipt, attempt.subject)
        if receipt.status != "completed":
            return attempt
        output_digest = receipt.planning_output_artifact_digest
        assert output_digest is not None
        intent = _planning_payload(self._artifacts, output_digest, attempt.subject)
        normalized = _normalize_intent(intent, _snapshot_from_bytes(attempt.snapshot_bytes))
        return self._repository.save_attempt(
            replace(
                attempt,
                planning_receipt_digest=receipt.receipt_digest,
                planning_output_artifact_digest=output_digest,
                intent_bytes=canonical_bytes(normalized),
            )
        )

    def _verify_attempt_artifacts(self, attempt: _PlanningAttempt) -> None:
        """Read back the immutable snapshot and Policy Witness before use."""

        snapshot = _snapshot_from_bytes(attempt.snapshot_bytes)
        if digest_bytes(attempt.snapshot_bytes) != attempt.snapshot_artifact_digest:
            raise PlanControlError("SNAPSHOT_DIGEST_MISMATCH", "Attempt snapshot digest changed")
        if _canonical(self._artifacts.get(attempt.snapshot_artifact_digest)) != snapshot:
            raise PlanControlError("SNAPSHOT_READBACK_INVALID", "Snapshot Artifact does not read back exactly")
        witness = {key: value for key, value in snapshot["policy"].items() if key != "digest"}
        if digest_value(witness) != attempt.policy_witness_digest or _canonical(self._artifacts.get(attempt.policy_witness_digest)) != witness:
            raise PlanControlError("POLICY_WITNESS_INVALID", "Policy Witness Artifact does not read back exactly")

    def _verify_policy_witness(self, plan_spec: Mapping[str, Any]) -> None:
        policy = plan_spec.get("policy")
        if type(policy) is not dict:
            raise PlanControlError("POLICY_WITNESS_INVALID", "PlanSpec policy projection is invalid")
        digest = _digest(policy.get("digest"), "PlanSpec Policy Witness digest")
        witness = _canonical(self._artifacts.get(digest))
        if type(witness) is not dict or _normalize_policy({**witness, "digest": digest}) != {**witness, "digest": digest}:
            raise PlanControlError("POLICY_WITNESS_INVALID", "PlanSpec Policy Witness Artifact is invalid")

    def _writer_generation(self) -> str:
        value = getattr(self._repository, "writer_generation", None)
        return _text(value, "repository writer_generation")


def _text(value: object, label: str) -> str:
    if type(value) is not str or not value:
        raise PlanControlError("PLAN_CONTROL_INVALID", f"{label} must be non-empty exact text")
    return value


def _digest(value: object, label: str) -> str:
    value = _text(value, label)
    if _DIGEST.fullmatch(value) is None:
        raise PlanControlError("PLAN_CONTROL_INVALID", f"{label} must be a SHA-256 digest")
    return value


def _optional_digest(value: object, label: str) -> str | None:
    return None if value is None else _digest(value, label)


def _canonical(value: Any, *, code: str = "PLAN_CONTROL_INVALID") -> Any:
    try:
        return load_canonical_json(canonical_bytes(value))
    except CanonicalJsonError as error:
        raise PlanControlError(code, "value is outside canonical JSON") from error


def _ready_refs(value: Sequence[str]) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise PlanControlError("READY_REFS_INVALID", "ready_refs must be a sequence")
    refs = tuple(_text(item, "ready_ref") for item in value)
    if not refs or len(set(refs)) != len(refs):
        raise PlanControlError("READY_REFS_INVALID", "ready_refs must be unique non-empty Ticket references")
    return tuple(sorted(refs))


def _put_canonical(artifacts: PlanningArtifacts, value: Any) -> str:
    reference = artifacts.put_canonical(_canonical(value))
    digest = getattr(reference, "digest", None)
    return _digest(digest, "Artifact digest")


def _handle_ref(handle: CampaignHandle) -> str:
    # The external API treats this as opaque while the Gateway needs a stable,
    # transport-safe identity to bind its independent campaign record.
    return "campaign-handle:" + digest_value(handle.__dict__)


def _frozen_ref(value: Any, label: str) -> dict[str, str]:
    if type(value) is not dict or set(value) != {"ref", "digest"}:
        raise PlanControlError("SNAPSHOT_INVALID", f"{label} must contain only ref and digest")
    return {"ref": _text(value["ref"], f"{label} ref"), "digest": _digest(value["digest"], f"{label} digest")}


def _versioned(value: object, *, roots: frozenset[str], label: str) -> str:
    value = _text(value, label)
    if _VERSIONED_IDENTIFIER.fullmatch(value) is None or value.split(".", 1)[0] not in roots:
        raise PlanControlError("POLICY_WITNESS_INVALID", f"{label} is not a permitted versioned policy identifier")
    return value


def _grants(value: Any, label: str) -> list[dict[str, str]]:
    if type(value) is not list:
        raise PlanControlError("POLICY_WITNESS_INVALID", f"{label} must be a list")
    grants = []
    for grant in value:
        if type(grant) is not dict or set(grant) != {"operation_id", "resource_id"}:
            raise PlanControlError("POLICY_WITNESS_INVALID", f"{label} contains an invalid grant")
        grants.append({"operation_id": _versioned(grant["operation_id"], roots=_OPERATION_ROOTS, label="operation_id"), "resource_id": _versioned(grant["resource_id"], roots=_RESOURCE_ROOTS, label="resource_id")})
    if len({(item["operation_id"], item["resource_id"]) for item in grants}) != len(grants):
        raise PlanControlError("POLICY_WITNESS_INVALID", f"{label} repeats a grant")
    return sorted(grants, key=lambda item: (item["operation_id"], item["resource_id"]))


def _facts(value: Any, *, label: str, validator) -> list[str]:
    if type(value) is not list:
        raise PlanControlError("POLICY_WITNESS_INVALID", f"{label} must be a list")
    facts = [validator(item) for item in value]
    if len(set(facts)) != len(facts):
        raise PlanControlError("POLICY_WITNESS_INVALID", f"{label} repeats a fact")
    return sorted(facts)


def _normalize_policy(value: Any) -> dict[str, Any]:
    expected = {"schema_version", "ref", "digest", "authority_grants", "allowed_capabilities", "exclusive_resources"}
    if type(value) is not dict or set(value) != expected or value["schema_version"] != 1:
        raise PlanControlError("POLICY_WITNESS_INVALID", "Policy Witness schema is invalid")
    raw_grants = value["authority_grants"]
    if type(raw_grants) is not dict or set(raw_grants) != set(_POLICY_ROLES):
        raise PlanControlError("POLICY_WITNESS_INVALID", "Policy Witness must supply every authority role")
    core = {
        "schema_version": 1,
        "ref": _text(value["ref"], "Policy Witness ref"),
        "authority_grants": {role: _grants(raw_grants[role], f"{role} grants") for role in _POLICY_ROLES},
        "allowed_capabilities": _facts(value["allowed_capabilities"], label="allowed_capabilities", validator=lambda item: _capability(item, "allowed_capabilities")),
        "exclusive_resources": _facts(value["exclusive_resources"], label="exclusive_resources", validator=lambda item: _versioned(item, roots=_RESOURCE_ROOTS, label="exclusive_resources")),
    }
    digest = _digest(value["digest"], "Policy Witness digest")
    if digest != digest_value(core):
        raise PlanControlError("POLICY_WITNESS_INVALID", "Policy Witness digest does not bind its exact facts")
    return {**core, "digest": digest}


def _capability(value: object, label: str) -> str:
    value = _text(value, label)
    if _CAPABILITY.fullmatch(value) is None:
        raise PlanControlError("POLICY_WITNESS_INVALID", f"{label} contains an invalid capability")
    return value


def _normalize_ticket(value: Any) -> dict[str, Any]:
    expected = {"key", "labels", "source", "contract", "native_blockers"}
    if type(value) is not dict or set(value) != expected:
        raise PlanControlError("SNAPSHOT_INVALID", "Ticket snapshot schema is invalid")
    key = _text(value["key"], "Ticket key")
    labels = value["labels"]
    if type(labels) is not list or any(type(label) is not str or not label for label in labels) or len(set(labels)) != len(labels):
        raise PlanControlError("TICKET_LABEL_INVALID", f"Ticket {key} labels are invalid")
    if "ready-for-agent" not in labels or set(labels).intersection(_TRIAGE - {"ready-for-agent"}):
        raise PlanControlError("TICKET_LABEL_INVALID", f"Ticket {key} is not ready-for-agent")
    contract = value["contract"]
    if type(contract) is not dict or set(contract) != {"title", "body"}:
        raise PlanControlError("TICKET_CONTRACT_MISSING", f"Ticket {key} lacks a complete frozen contract")
    blockers = value["native_blockers"]
    if type(blockers) is not list:
        raise PlanControlError("SNAPSHOT_INVALID", "native_blockers must be a list")
    canonical_blockers = []
    for blocker in blockers:
        if type(blocker) is not dict or set(blocker) != {"key", "state"}:
            raise PlanControlError("SNAPSHOT_INVALID", "native blocker schema is invalid")
        state = _text(blocker["state"], "native blocker state").lower()
        if state not in {"open", "closed"}:
            raise PlanControlError("SNAPSHOT_INVALID", "native blocker state is invalid")
        canonical_blockers.append({"key": _text(blocker["key"], "native blocker key"), "state": state})
    if len({item["key"] for item in canonical_blockers}) != len(canonical_blockers):
        raise PlanControlError("SNAPSHOT_INVALID", "native blockers repeat a Ticket")
    return {
        "key": key,
        "labels": sorted(labels),
        "source": _frozen_ref(value["source"], "Ticket source"),
        "contract": {"title": _text(contract["title"], "Ticket title"), "body": _text(contract["body"], "Ticket body")},
        "native_blockers": sorted(canonical_blockers, key=lambda item: item["key"]),
    }


def _normalize_snapshot(value: Any, repository: str, refs: tuple[str, ...]) -> dict[str, Any]:
    value = _canonical(value, code="SNAPSHOT_INVALID")
    expected = {"repository", "target_branch", "campaign_source", "policy", "tickets"}
    if type(value) is not dict or set(value) != expected or value["repository"] != repository or type(value["tickets"]) is not list:
        raise PlanControlError("SNAPSHOT_INVALID", "Campaign snapshot schema or repository is invalid")
    tickets = sorted((_normalize_ticket(ticket) for ticket in value["tickets"]), key=lambda item: item["key"])
    keys = tuple(ticket["key"] for ticket in tickets)
    if keys != refs:
        raise PlanControlError("SNAPSHOT_OMISSION", "Snapshot must contain every and only selected Tickets")
    selected = set(keys)
    external = sorted({blocker["key"] for ticket in tickets for blocker in ticket["native_blockers"] if blocker["state"] == "open" and blocker["key"] not in selected})
    if external:
        raise PlanControlError("EXTERNAL_BLOCKER_OPEN", "Selected Tickets have open external blockers: " + ", ".join(external))
    dependencies = {ticket["key"]: {blocker["key"] for blocker in ticket["native_blockers"] if blocker["state"] == "open" and blocker["key"] in selected} for ticket in tickets}
    _assert_acyclic(dependencies)
    return {
        "schema_version": 1,
        "repository": repository,
        "target_branch": _text(value["target_branch"], "target_branch"),
        "campaign_source": _frozen_ref(value["campaign_source"], "Campaign source"),
        "policy": _normalize_policy(value["policy"]),
        "tickets": tickets,
    }


def _assert_acyclic(dependencies: Mapping[str, set[str]]) -> None:
    visited: set[str] = set()
    visiting: set[str] = set()

    def visit(key: str) -> None:
        if key in visiting:
            raise PlanControlError("DEPENDENCY_CYCLE", "Ticket dependencies contain a cycle")
        if key in visited:
            return
        visiting.add(key)
        for dependency in sorted(dependencies[key]):
            visit(dependency)
        visiting.remove(key)
        visited.add(key)

    for key in sorted(dependencies):
        visit(key)


def _facts_by_ticket(value: Any, selected: set[str], label: str) -> dict[str, list[str]]:
    if type(value) is not dict or not set(value).issubset(selected):
        raise PlanControlError("PLAN_INTENT_INVALID", f"{label} names unselected work")
    result = {}
    for key in selected:
        facts = value.get(key, [])
        if type(facts) is not list or any(type(item) is not str or not item for item in facts) or len(set(facts)) != len(facts):
            raise PlanControlError("PLAN_INTENT_INVALID", f"{label} facts are invalid")
        result[key] = sorted(facts)
    return result


def _normalize_intent(value: Any, snapshot: dict[str, Any]) -> dict[str, Any]:
    value = _canonical(value, code="PLAN_INTENT_INVALID")
    expected = {"admitted_work", "dependency_additions", "exclusive_resources", "capability_requirements", "decision_requirements"}
    if type(value) is not dict or set(value) != expected:
        raise PlanControlError("PLAN_INTENT_INVALID", "Planning output contains unsupported fields")
    selected = {ticket["key"] for ticket in snapshot["tickets"]}
    admitted = value["admitted_work"]
    if type(admitted) is not list or set(admitted) != selected or len(admitted) != len(selected):
        raise PlanControlError("PLAN_INTENT_OMISSION", "Planning output must account for every selected Ticket")
    dependencies = {ticket["key"]: {blocker["key"] for blocker in ticket["native_blockers"] if blocker["state"] == "open" and blocker["key"] in selected} for ticket in snapshot["tickets"]}
    additions = []
    if type(value["dependency_additions"]) is not list:
        raise PlanControlError("PLAN_INTENT_INVALID", "dependency_additions must be a list")
    for item in value["dependency_additions"]:
        if type(item) is not dict or set(item) != {"from", "to", "reason"}:
            raise PlanControlError("PLAN_INTENT_INVALID", "dependency addition is invalid")
        source, target = _text(item["from"], "dependency source"), _text(item["to"], "dependency target")
        if source not in selected or target not in selected or source == target:
            raise PlanControlError("PLAN_INTENT_INVALID", "dependency addition leaves the selected Ticket set")
        dependencies[source].add(target)
        additions.append({"from": source, "to": target, "reason": _text(item["reason"], "dependency reason")})
    if len({(item["from"], item["to"]) for item in additions}) != len(additions):
        raise PlanControlError("PLAN_INTENT_INVALID", "dependency additions repeat an edge")
    _assert_acyclic(dependencies)
    exclusive = _facts_by_ticket(value["exclusive_resources"], selected, "exclusive_resources")
    capabilities = _facts_by_ticket(value["capability_requirements"], selected, "capability_requirements")
    policy = snapshot["policy"]
    if any(fact not in policy["exclusive_resources"] for facts in exclusive.values() for fact in facts):
        raise PlanControlError("EXCLUSIVE_RESOURCE_INVALID", "Planning output names an unknown Exclusive Resource")
    if any(fact not in policy["allowed_capabilities"] for facts in capabilities.values() for fact in facts):
        raise PlanControlError("CAPABILITY_INVALID", "Planning output names an unknown factual capability")
    if type(value["decision_requirements"]) is not list:
        raise PlanControlError("PLAN_INTENT_INVALID", "decision_requirements must be a list")
    findings = []
    for finding in value["decision_requirements"]:
        if type(finding) is not dict or set(finding) not in ({"code", "detail"}, {"code", "detail", "ticket_key"}):
            raise PlanControlError("PLAN_INTENT_INVALID", "Decision finding is invalid")
        ticket_key = finding.get("ticket_key")
        if ticket_key is not None and ticket_key not in selected:
            raise PlanControlError("PLAN_INTENT_INVALID", "Decision finding names unselected work")
        findings.append({"code": _text(finding["code"], "Decision code"), "detail": _text(finding["detail"], "Decision detail"), "ticket_key": ticket_key})
    if len({(item["code"], item["detail"], item["ticket_key"]) for item in findings}) != len(findings):
        raise PlanControlError("PLAN_INTENT_INVALID", "Decision findings repeat")
    return {
        "admitted_work": sorted(admitted),
        "dependency_additions": sorted(additions, key=lambda item: (item["from"], item["to"], item["reason"])),
        "exclusive_resources": {key: exclusive[key] for key in sorted(exclusive)},
        "capability_requirements": {key: capabilities[key] for key in sorted(capabilities)},
        "decision_requirements": sorted(findings, key=lambda item: (item["code"], item["ticket_key"] or "", item["detail"])),
    }


def _snapshot_from_bytes(payload: bytes) -> dict[str, Any]:
    try:
        value = load_canonical_json(payload)
    except CanonicalJsonError as error:
        raise PlanControlError("SNAPSHOT_READBACK_INVALID", "Snapshot Artifact bytes are not canonical") from error
    if type(value) is not dict:
        raise PlanControlError("SNAPSHOT_READBACK_INVALID", "Snapshot Artifact is not an object")
    return value


def _intent_from_bytes(payload: bytes, snapshot_bytes: bytes) -> dict[str, Any]:
    try:
        value = load_canonical_json(payload)
    except CanonicalJsonError as error:
        raise PlanControlError("PLAN_INTENT_READBACK_INVALID", "Plan Intent bytes are not canonical") from error
    return _normalize_intent(value, _snapshot_from_bytes(snapshot_bytes))


def _authority(policy_digest: str, grants: list[dict[str, str]]) -> dict[str, Any]:
    core = {"policy_witness_digest": policy_digest, "grants": grants}
    return {**core, "subtree_digest": digest_value(core)}


def _compile_plan(snapshot_bytes: bytes, snapshot_digest: str, intent_bytes: bytes, handle: CampaignHandle) -> PlanRevision:
    if digest_bytes(snapshot_bytes) != snapshot_digest:
        raise PlanControlError("SNAPSHOT_DIGEST_MISMATCH", "Frozen snapshot digest changed before compilation")
    snapshot = _snapshot_from_bytes(snapshot_bytes)
    intent = _intent_from_bytes(intent_bytes, snapshot_bytes)
    if canonical_bytes(intent) != intent_bytes:
        raise PlanControlError("PLAN_INTENT_READBACK_INVALID", "Validated Plan Intent changed before compilation")
    policy = snapshot["policy"]
    selected = {ticket["key"] for ticket in snapshot["tickets"]}
    dependencies = {ticket["key"]: {blocker["key"] for blocker in ticket["native_blockers"] if blocker["state"] == "open" and blocker["key"] in selected} for ticket in snapshot["tickets"]}
    for addition in intent["dependency_additions"]:
        dependencies[addition["from"]].add(addition["to"])
    work = []
    for ticket in snapshot["tickets"]:
        key = ticket["key"]
        work.append(
            {
                "key": key,
                "source": ticket["source"],
                "contract": ticket["contract"],
                "depends_on": sorted(dependencies[key]),
                "exclusive_resources": intent["exclusive_resources"][key],
                "capabilities": intent["capability_requirements"][key],
                "authority": {
                    "policy_witness_digest": policy["digest"],
                    "worker": _authority(policy["digest"], policy["authority_grants"]["worker"]),
                    "recovery_worker": _authority(policy["digest"], policy["authority_grants"]["recovery_worker"]),
                    "review": _authority(policy["digest"], policy["authority_grants"]["review"]),
                },
            }
        )
    plan = {
        "schema_version": 3,
        "repository": handle.repository,
        "target_branch": snapshot["target_branch"],
        "campaign": {"key": handle.campaign_key, "source": snapshot["campaign_source"], "authority": _authority(policy["digest"], policy["authority_grants"]["campaign"])},
        "policy": {"ref": policy["ref"], "digest": policy["digest"]},
        "work": work,
    }
    payload = canonical_bytes(plan)
    _validate_plan_spec(payload)
    return PlanRevision(handle.repository, handle.campaign_key, snapshot_digest, payload, digest_bytes(payload))


def _validate_plan_spec(payload: bytes) -> None:
    try:
        plan = load_canonical_json(payload)
    except CanonicalJsonError as error:
        raise PlanControlError("PLANSPEC_V3_INVALID", "PlanSpec is not canonical") from error
    if type(plan) is not dict or set(plan) != {"schema_version", "repository", "target_branch", "campaign", "policy", "work"} or plan["schema_version"] != 3:
        raise PlanControlError("PLANSPEC_V3_INVALID", "PlanSpec v3 top-level schema is invalid")
    campaign = plan["campaign"]
    policy = plan["policy"]
    if (
        type(campaign) is not dict
        or set(campaign) != {"key", "source", "authority"}
        or _frozen_ref(campaign["source"], "PlanSpec campaign source") != campaign["source"]
        or type(policy) is not dict
        or set(policy) != {"ref", "digest"}
        or _text(policy["ref"], "PlanSpec policy ref") != policy["ref"]
        or _digest(policy["digest"], "PlanSpec policy digest") != policy["digest"]
    ):
        raise PlanControlError("PLANSPEC_V3_INVALID", "PlanSpec Campaign or Policy projection is invalid")
    if type(plan["work"]) is not list or not plan["work"]:
        raise PlanControlError("PLANSPEC_V3_INVALID", "PlanSpec v3 has no work manifest")
    forbidden = {"provider", "model", "cli", "profile", "session", "binding", "capacity", "permission", "check", "review", "recovery", "integration"}
    if any(forbidden.intersection(item) for item in plan["work"] if type(item) is dict):
        raise PlanControlError("PLANSPEC_V3_INVALID", "PlanSpec contains a Runtime or lifecycle field")
    keys = [item.get("key") for item in plan["work"] if type(item) is dict]
    if keys != sorted(set(keys)):
        raise PlanControlError("PLANSPEC_V3_INVALID", "PlanSpec work manifests are not canonically unique")
    dependencies: dict[str, set[str]] = {}
    for item in plan["work"]:
        expected = {"key", "source", "contract", "depends_on", "exclusive_resources", "capabilities", "authority"}
        if type(item) is not dict or set(item) != expected or _text(item["key"], "PlanSpec work key") != item["key"] or _frozen_ref(item["source"], "PlanSpec work source") != item["source"]:
            raise PlanControlError("PLANSPEC_V3_INVALID", "PlanSpec work manifest schema is invalid")
        contract = item["contract"]
        if type(contract) is not dict or set(contract) != {"title", "body"} or any(type(contract[key]) is not str or not contract[key] for key in contract):
            raise PlanControlError("PLANSPEC_V3_INVALID", "PlanSpec work contract is invalid")
        for field in ("depends_on", "exclusive_resources", "capabilities"):
            facts = item[field]
            if type(facts) is not list or any(type(value) is not str or not value for value in facts) or facts != sorted(set(facts)):
                raise PlanControlError("PLANSPEC_V3_INVALID", f"PlanSpec work {field} is invalid")
        authority = item["authority"]
        if type(authority) is not dict or set(authority) != {"policy_witness_digest", "worker", "recovery_worker", "review"} or authority["policy_witness_digest"] != policy["digest"]:
            raise PlanControlError("PLANSPEC_V3_INVALID", "PlanSpec work authority envelope is invalid")
        for role in ("worker", "recovery_worker", "review"):
            if authority[role] != _authority(policy["digest"], authority[role].get("grants", []) if type(authority[role]) is dict else []):
                raise PlanControlError("PLANSPEC_V3_INVALID", "PlanSpec authority subtree is invalid")
        dependencies[item["key"]] = set(item["depends_on"])
    if campaign["authority"] != _authority(policy["digest"], campaign["authority"].get("grants", []) if type(campaign["authority"]) is dict else []):
        raise PlanControlError("PLANSPEC_V3_INVALID", "PlanSpec Campaign authority subtree is invalid")
    _assert_acyclic(dependencies)


def _validate_preflight(receipt: object, subject: CampaignPlanningSubject) -> None:
    if getattr(receipt, "subject_digest", None) != subject.digest or getattr(receipt, "stable_action_id", None) != subject.stable_action_id:
        raise PlanControlError("RUNTIME_PREFLIGHT_INVALID", "RuntimeGateway preflight receipt does not bind the exact Planning subject")
    _digest(getattr(receipt, "receipt_digest", None), "RuntimeGateway preflight receipt digest")


def _validate_planning_receipt(receipt: object, subject: CampaignPlanningSubject) -> None:
    if getattr(receipt, "subject_digest", None) != subject.digest or getattr(receipt, "stable_action_id", None) != subject.stable_action_id:
        raise PlanControlError("RUNTIME_PLANNING_RECEIPT_INVALID", "RuntimeGateway planning receipt does not bind the exact Planning subject")
    status = getattr(receipt, "status", None)
    if type(status) is not str or status not in {"running", "parked", "completed"}:
        raise PlanControlError("RUNTIME_PLANNING_RECEIPT_INVALID", "RuntimeGateway planning receipt has an invalid lifecycle")
    _digest(getattr(receipt, "receipt_digest", None), "RuntimeGateway planning receipt digest")
    output = getattr(receipt, "planning_output_artifact_digest", None)
    if status == "completed":
        _digest(output, "RuntimeGateway planning output Artifact digest")
    elif output is not None:
        raise PlanControlError("RUNTIME_PLANNING_RECEIPT_INVALID", "Incomplete Planning receipt cannot name output")


def _planning_payload(artifacts: PlanningArtifacts, output_digest: str, subject: CampaignPlanningSubject) -> Any:
    try:
        output = _canonical(artifacts.get(output_digest), code="RUNTIME_PLANNING_OUTPUT_INVALID")
    except KeyError as error:
        raise PlanControlError("RUNTIME_PLANNING_OUTPUT_INVALID", "Planning output Artifact is missing") from error
    expected = {"schema_version", "subject_digest", "stable_action_id", "authority_digest", "payload"}
    if type(output) is not dict or set(output) != expected or output["schema_version"] != "gwo.runtime.output.v1" or output["subject_digest"] != subject.digest or output["stable_action_id"] != subject.stable_action_id or output["authority_digest"] != subject.authority_digest:
        raise PlanControlError("RUNTIME_PLANNING_OUTPUT_INVALID", "Planning output Artifact does not bind its exact subject and authority")
    return output["payload"]


_default_control: PlanControl | None = None


def _install_start_control(control: PlanControl) -> None:
    """Host composition hook; intentionally not a semantic workflow operation."""

    if type(control) is not PlanControl:
        raise TypeError("control must be an exact PlanControl")
    global _default_control
    _default_control = control


def start(repository: str, ready_refs: Sequence[str], options: object = None) -> CampaignHandle:
    """Start one Campaign through the host-composed PlanControl boundary."""

    if _default_control is None:
        raise PlanControlError("PLAN_CONTROL_NOT_CONFIGURED", "Host composition has not installed PlanControl")
    return _default_control.start(repository, ready_refs, options)
