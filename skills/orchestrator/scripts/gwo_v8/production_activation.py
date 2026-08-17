"""Fail-closed composition for the explicitly authorized V8 activation."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Protocol

from ._canonical import (
    canonical_bytes,
    digest_bytes,
    digest_value,
    load_canonical_json,
)
from .activation import ActivationReceipt, DurablePlanRecord, PublishedPlan
from .compiler import CompiledPlan
from .cutover_guard import (
    CutoverGuardReceipt,
    CutoverSubject,
    EXPECTED_SOURCE_WRITER_GENERATION,
    RECEIPT_SCHEMA,
)
from .evidence import TypedEvidence
from .transition import (
    CanaryEvidenceControl,
    CanaryAcceptance,
    CurrentWriter,
    WriterCutoverController,
    WriterTransitionOutcome,
    WriterTransitionRecord,
)


_SHA40 = re.compile(r"^[0-9a-f]{40}$")
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_EXPECTED_WORKER_CAPACITY = 8
_EXPECTED_COORDINATOR_CAPACITY = 1
_DURABLE_CANARY_REF_PREFIXES = ("github://", "git://")
WRITER_TRANSITION = "v6.1 -> v8"


class ProductionActivationError(RuntimeError):
    """A typed, fail-closed rejection at the production activation boundary."""

    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code = code
        self.detail = detail


def _nonempty_text(value: object) -> bool:
    return type(value) is str and bool(value.strip())


def _is_digest(value: object) -> bool:
    return type(value) is str and _DIGEST.fullmatch(value) is not None


def _is_sha40(value: object) -> bool:
    return type(value) is str and _SHA40.fullmatch(value) is not None


def _durable_canary_ref(value: object) -> bool:
    return (
        type(value) is str
        and bool(value.removeprefix("github://").removeprefix("git://"))
        and value.startswith(_DURABLE_CANARY_REF_PREFIXES)
    )


def _reject(code: str, detail: str) -> None:
    raise ProductionActivationError(code, detail)


@dataclass(frozen=True, init=False)
class ProductionActivationAuthorization:
    """Exact owner approval identity for one named V8 activation."""

    run_id: str
    repository: str
    merged_main_sha: str
    merged_main_git_tree: str
    release_subject_digest: str
    evidence_root: str
    target_repository: str
    writer_transition: str
    target_writer_generation: str

    def __init__(
        self,
        run_id: str,
        repository: str,
        merged_main_sha: str | None = None,
        merged_main_git_tree: str | None = None,
        release_subject_digest: str | None = None,
        evidence_root: str | None = None,
        target_repository: str | None = None,
        writer_transition: str = WRITER_TRANSITION,
        target_writer_generation: str | None = None,
        *,
        source_main_sha: str | None = None,
        source_main_tree: str | None = None,
    ) -> None:
        merged_main_sha = _coalesced_identity(
            merged_main_sha, source_main_sha, "merged_main_sha"
        )
        merged_main_git_tree = _coalesced_identity(
            merged_main_git_tree, source_main_tree, "merged_main_git_tree"
        )
        object.__setattr__(self, "run_id", run_id)
        object.__setattr__(self, "repository", repository)
        object.__setattr__(self, "merged_main_sha", merged_main_sha)
        object.__setattr__(self, "merged_main_git_tree", merged_main_git_tree)
        object.__setattr__(self, "release_subject_digest", release_subject_digest)
        object.__setattr__(self, "evidence_root", evidence_root)
        object.__setattr__(self, "target_repository", target_repository)
        object.__setattr__(self, "writer_transition", writer_transition)
        object.__setattr__(self, "target_writer_generation", target_writer_generation)
        self.__post_init__()

    def __post_init__(self) -> None:
        for name in self.__dataclass_fields__:
            value = getattr(self, name)
            if not _nonempty_text(value):
                raise ValueError(
                    f"ProductionActivationAuthorization.{name} must be non-empty text"
                )
        if not _is_sha40(self.merged_main_sha):
            raise ValueError(
                "ProductionActivationAuthorization.merged_main_sha must be a "
                "lowercase 40-hex Git commit"
            )
        if not _is_sha40(self.merged_main_git_tree):
            raise ValueError(
                "ProductionActivationAuthorization.merged_main_git_tree must be a "
                "lowercase 40-hex Git tree"
            )
        if not _is_digest(self.release_subject_digest):
            raise ValueError(
                "ProductionActivationAuthorization.release_subject_digest must be a "
                "lowercase 64-hex digest"
            )
        if self.writer_transition != WRITER_TRANSITION:
            raise ValueError(
                "ProductionActivationAuthorization.writer_transition must equal "
                f"{WRITER_TRANSITION!r}"
            )

    @property
    def source_main_sha(self) -> str:
        """Compatibility alias for the merged-main commit identity."""

        return self.merged_main_sha

    @property
    def source_main_tree(self) -> str:
        """Compatibility alias for the merged-main tree identity."""

        return self.merged_main_git_tree

    def canonical_without_receipt_fields(self) -> dict[str, str]:
        return {
            "run_id": self.run_id,
            "repository": self.repository,
            "merged_main_sha": self.merged_main_sha,
            "merged_main_git_tree": self.merged_main_git_tree,
            "release_subject_digest": self.release_subject_digest,
            "evidence_root": self.evidence_root,
            "target_repository": self.target_repository,
            "writer_transition": self.writer_transition,
            "target_writer_generation": self.target_writer_generation,
        }

    def canonical(self) -> dict[str, str]:
        return self.canonical_without_receipt_fields()


def _coalesced_identity(
    canonical: str | None,
    legacy: str | None,
    name: str,
) -> str:
    if canonical is None:
        canonical = legacy
    elif legacy is not None and canonical != legacy:
        raise ValueError(f"{name} and its source_main alias must match")
    if canonical is None:
        raise ValueError(f"{name} is required")
    return canonical


@dataclass(frozen=True, init=False)
class ProductionActivationAuthorizationReceipt:
    """Durable readback of the exact owner approval identity."""

    run_id: str
    repository: str
    merged_main_sha: str
    merged_main_git_tree: str
    release_subject_digest: str
    evidence_root: str
    target_repository: str
    writer_transition: str
    target_writer_generation: str
    approval_ref: str
    receipt_digest: str

    def __init__(
        self,
        run_id: str,
        repository: str,
        merged_main_sha: str | None = None,
        merged_main_git_tree: str | None = None,
        release_subject_digest: str | None = None,
        evidence_root: str | None = None,
        target_repository: str | None = None,
        writer_transition: str = WRITER_TRANSITION,
        target_writer_generation: str | None = None,
        approval_ref: str | None = None,
        receipt_digest: str | None = None,
        *,
        source_main_sha: str | None = None,
        source_main_tree: str | None = None,
    ) -> None:
        merged_main_sha = _coalesced_identity(
            merged_main_sha, source_main_sha, "merged_main_sha"
        )
        merged_main_git_tree = _coalesced_identity(
            merged_main_git_tree, source_main_tree, "merged_main_git_tree"
        )
        object.__setattr__(self, "run_id", run_id)
        object.__setattr__(self, "repository", repository)
        object.__setattr__(self, "merged_main_sha", merged_main_sha)
        object.__setattr__(self, "merged_main_git_tree", merged_main_git_tree)
        object.__setattr__(self, "release_subject_digest", release_subject_digest)
        object.__setattr__(self, "evidence_root", evidence_root)
        object.__setattr__(self, "target_repository", target_repository)
        object.__setattr__(self, "writer_transition", writer_transition)
        object.__setattr__(self, "target_writer_generation", target_writer_generation)
        object.__setattr__(self, "approval_ref", approval_ref)
        object.__setattr__(self, "receipt_digest", receipt_digest)
        self.__post_init__()

    def __post_init__(self) -> None:
        for name in (
            "run_id",
            "repository",
            "evidence_root",
            "target_repository",
            "writer_transition",
            "target_writer_generation",
            "approval_ref",
        ):
            if not _nonempty_text(getattr(self, name)):
                raise ValueError(
                    f"ProductionActivationAuthorizationReceipt.{name} "
                    "must be non-empty text"
                )
        if not _is_sha40(self.merged_main_sha):
            raise ValueError(
                "ProductionActivationAuthorizationReceipt.merged_main_sha "
                "must be a lowercase 40-hex Git commit"
            )
        if not _is_sha40(self.merged_main_git_tree):
            raise ValueError(
                "ProductionActivationAuthorizationReceipt.merged_main_git_tree "
                "must be a lowercase 40-hex Git tree"
            )
        if not _is_digest(self.release_subject_digest):
            raise ValueError(
                "ProductionActivationAuthorizationReceipt.release_subject_digest "
                "must be a lowercase 64-hex digest"
            )
        if self.writer_transition != WRITER_TRANSITION:
            raise ValueError(
                "ProductionActivationAuthorizationReceipt.writer_transition must equal "
                f"{WRITER_TRANSITION!r}"
            )
        if not _is_digest(self.receipt_digest):
            raise ValueError(
                "ProductionActivationAuthorizationReceipt.receipt_digest "
                "must be a lowercase 64-hex digest"
            )

    @property
    def source_main_sha(self) -> str:
        return self.merged_main_sha

    @property
    def source_main_tree(self) -> str:
        return self.merged_main_git_tree

    def canonical_without_digest(self) -> dict[str, str]:
        return {
            "run_id": self.run_id,
            "repository": self.repository,
            "merged_main_sha": self.merged_main_sha,
            "merged_main_git_tree": self.merged_main_git_tree,
            "release_subject_digest": self.release_subject_digest,
            "evidence_root": self.evidence_root,
            "target_repository": self.target_repository,
            "writer_transition": self.writer_transition,
            "target_writer_generation": self.target_writer_generation,
            "approval_ref": self.approval_ref,
        }

    def canonical(self) -> dict[str, str]:
        value = self.canonical_without_digest()
        value["receipt_digest"] = self.receipt_digest
        return value

    def has_valid_digest(self) -> bool:
        return self.receipt_digest == digest_value(self.canonical_without_digest())


class ProductionActivationAuthorizationSource(Protocol):
    """Read the durable, typed approval/provenance receipt for an attempt."""

    def read(
        self,
        authorization: ProductionActivationAuthorization,
    ) -> ProductionActivationAuthorizationReceipt | None: ...


@dataclass(frozen=True)
class ProductionActivationRequest:
    """All immutable inputs consumed by one production activation request."""

    authorization: ProductionActivationAuthorization
    source_main_sha: str
    source_main_tree: str
    compiled_plan: CompiledPlan
    canary: CanaryAcceptance
    guard_subject: CutoverSubject
    guard_receipt: CutoverGuardReceipt | None
    worker_capacity: int
    coordinator_capacity: int

    def __post_init__(self) -> None:
        if type(self.authorization) is not ProductionActivationAuthorization:
            raise TypeError("request authorization has the wrong exact type")
        if not _nonempty_text(self.source_main_sha):
            raise ValueError("request source_main_sha must be non-empty text")
        if not _nonempty_text(self.source_main_tree):
            raise ValueError("request source_main_tree must be non-empty text")
        if not _is_sha40(self.source_main_sha):
            raise ValueError("request source_main_sha must be a lowercase 40-hex Git commit")
        if not _is_sha40(self.source_main_tree):
            raise ValueError("request source_main_tree must be a lowercase 40-hex Git tree")
        if type(self.compiled_plan) is not CompiledPlan:
            raise TypeError("request CompiledPlan has the wrong exact type")
        if type(self.canary) is not CanaryAcceptance:
            raise TypeError("request CanaryAcceptance has the wrong exact type")
        if type(self.guard_subject) is not CutoverSubject:
            raise TypeError("request CutoverSubject has the wrong exact type")
        if self.guard_receipt is not None and type(self.guard_receipt) is not CutoverGuardReceipt:
            raise TypeError("request Guard receipt has the wrong exact type")
        if type(self.worker_capacity) is not int or isinstance(
            self.worker_capacity, bool
        ):
            raise TypeError("request worker capacity must be an exact integer")
        if type(self.coordinator_capacity) is not int or isinstance(
            self.coordinator_capacity, bool
        ):
            raise TypeError("request coordinator capacity must be an exact integer")


@dataclass(frozen=True)
class ProductionActivationPlanIdentity:
    """Immutable bytes identifying the exact compiled Plan and metadata."""

    repository: str
    digest: str
    canonical_bytes: bytes
    compilation_record_bytes: bytes


@dataclass(frozen=True)
class ProductionActivationPreflight:
    """The read-only, identity-bound result immediately before execute."""

    request: ProductionActivationRequest
    authorization_receipt: ProductionActivationAuthorizationReceipt
    plan_identity: ProductionActivationPlanIdentity
    current_writer: CurrentWriter
    plan_digest: str
    canary_evidence_digest: str
    guard_receipt_digest: str
    checks: tuple[str, ...]


@dataclass(frozen=True)
class ProductionActivationComposition:
    """Live dependencies assembled around one typed activation request."""

    controller: WriterCutoverController
    canary_evidence_control: CanaryEvidenceControl

    def __post_init__(self) -> None:
        if type(self.controller) is not WriterCutoverController:
            raise TypeError(
                "ProductionActivationComposition.controller must be one exact "
                "WriterCutoverController"
            )
        if not callable(getattr(self.canary_evidence_control, "read", None)) or not callable(
            getattr(self.canary_evidence_control, "read_manifest", None)
        ):
            raise TypeError(
                "ProductionActivationComposition.canary_evidence_control must expose "
                "durable read operations"
            )


class ProductionActivationCompositionFactory(Protocol):
    """Host-owned live factory; production never substitutes test doubles."""

    def compose(
        self,
        *,
        authorization: ProductionActivationAuthorization,
        compiled_plan: CompiledPlan,
        canary: CanaryAcceptance,
        guard_subject: CutoverSubject,
        guard_receipt: CutoverGuardReceipt,
    ) -> ProductionActivationComposition: ...


class _NotCompleted:
    pass


class _PendingActivation:
    pass


class ProductionActivationFacade:
    """Compose existing Guard, publication, and transition protocols once."""

    def __init__(
        self,
        controller: WriterCutoverController,
        *,
        authorization_source: ProductionActivationAuthorizationSource | None = None,
        canary_evidence_control: CanaryEvidenceControl | None = None,
    ):
        self._controller = controller
        self._authorization_source = authorization_source
        self._canary_evidence_control = canary_evidence_control

    def preflight(
        self,
        request: ProductionActivationRequest,
    ) -> ProductionActivationPreflight:
        if type(request) is not ProductionActivationRequest:
            self._validate_common(request)
        plan_snapshot = self._snapshot_compiled_plan(
            self._capture_plan_identity(request.compiled_plan),
        )
        return self._preflight(request, plan_snapshot=plan_snapshot)

    def _preflight(
        self,
        request: ProductionActivationRequest,
        *,
        plan_snapshot: CompiledPlan,
    ) -> ProductionActivationPreflight:
        if type(plan_snapshot) is not CompiledPlan:
            _reject(
                "COMPILED_PLAN_IDENTITY_CHANGED",
                "preflight Plan snapshot has the wrong exact type",
            )
        plan_identity = self._capture_plan_identity(plan_snapshot)
        authorization_receipt = self._validate_common(
            request,
            compiled_plan=plan_snapshot,
        )
        current = self._read_current(request.authorization.target_repository)
        if current.writer_generation != EXPECTED_SOURCE_WRITER_GENERATION:
            if current.writer_generation != request.authorization.target_writer_generation:
                _reject(
                    "SOURCE_WRITER_INVALID",
                    "production activation requires the current V6.1 writer",
                )
            record = self._read_transition_record(
                request.authorization.target_repository,
                current.record_id,
            )
            if not self._pending_matches(
                request,
                current,
                record,
                compiled_plan=plan_snapshot,
            ):
                _reject(
                    "ACTIVATION_READBACK_INVALID",
                    "current pending cutover is not the exact activation request",
                )
        if (
            request.worker_capacity != _EXPECTED_WORKER_CAPACITY
            or request.coordinator_capacity != _EXPECTED_COORDINATOR_CAPACITY
        ):
            _reject(
                "CUTOVER_CAPACITY_INVALID",
                "production activation requires worker/coordinator capacity 8/1",
            )
        receipt = request.guard_receipt
        evidence_digest = request.canary.evidence_package_digest
        if receipt is None or evidence_digest is None:
            _reject(
                "ACTIVATION_REQUEST_INVALID",
                "preflight requires exact Guard and Canary evidence identities",
            )
        return ProductionActivationPreflight(
            request=request,
            authorization_receipt=authorization_receipt,
            plan_identity=plan_identity,
            current_writer=current,
            plan_digest=plan_snapshot.digest,
            canary_evidence_digest=evidence_digest,
            guard_receipt_digest=receipt.receipt_digest,
            checks=(
                "authorization_identity",
                "compiled_plan_digest",
                "canary_acceptance_and_evidence_identity",
                "guard_receipt_subject",
                "source_writer_v6.1",
                "capacity_8_1",
            ),
        )

    def execute(
        self,
        request: ProductionActivationRequest,
        *,
        authorization: ProductionActivationAuthorization,
        preflight: ProductionActivationPreflight | None = None,
    ) -> WriterTransitionOutcome:
        if (
            type(authorization) is not ProductionActivationAuthorization
            or type(request) is not ProductionActivationRequest
            or request.authorization != authorization
        ):
            _reject(
                "AUTHORIZATION_REQUEST_MISMATCH",
                "owner authorization does not exactly match the activation request",
            )

        if preflight is not None:
            if (
                type(preflight) is not ProductionActivationPreflight
                or preflight.request != request
            ):
                _reject(
                    "PREFLIGHT_REQUEST_MISMATCH",
                    "preflight is not bound to the exact activation request",
                )
            self._validate_plan_identity(
                request.compiled_plan,
                preflight.plan_identity,
            )
            compiled_plan = self._snapshot_compiled_plan(preflight.plan_identity)
            current_receipt = self._validate_common(
                request,
                compiled_plan=compiled_plan,
            )
            if current_receipt != preflight.authorization_receipt:
                _reject(
                    "AUTHORIZATION_PROVENANCE_STALE",
                    "authorization provenance changed after preflight",
                )
            current = self._read_current(request.authorization.target_repository)
            if current.writer_generation == authorization.target_writer_generation:
                completed = self._read_completed_outcome(
                    request,
                    compiled_plan=compiled_plan,
                )
                if not isinstance(completed, _NotCompleted):
                    if isinstance(completed, _PendingActivation):
                        completed = _NotCompleted()
                    else:
                        if type(completed) is not WriterTransitionOutcome:
                            _reject(
                                "ACTIVATION_READBACK_INVALID",
                                "completed activation readback has the wrong type",
                            )
                        return completed
        else:
            compiled_plan = self._snapshot_compiled_plan(
                self._capture_plan_identity(request.compiled_plan),
            )
            completed = self._read_completed_outcome(
                request,
                compiled_plan=compiled_plan,
            )
            if not isinstance(completed, _NotCompleted):
                if isinstance(completed, _PendingActivation):
                    completed = _NotCompleted()
                else:
                    if type(completed) is not WriterTransitionOutcome:
                        _reject(
                            "ACTIVATION_READBACK_INVALID",
                            "completed activation readback has the wrong type",
                        )
                    return completed

        final_preflight = self._preflight(
            request,
            plan_snapshot=compiled_plan,
        )
        compiled_plan = self._snapshot_compiled_plan(final_preflight.plan_identity)
        outcome = self._controller.cutover(
            compiled_plan,
            canary=request.canary,
            guard_subject=request.guard_subject,
            guard_receipt=request.guard_receipt,
            writer_generation=authorization.target_writer_generation,
            worker_capacity=request.worker_capacity,
            coordinator_capacity=request.coordinator_capacity,
        )
        self._validate_activation_readback(
            request,
            outcome,
            compiled_plan=compiled_plan,
        )
        return outcome

    def _validate_common(
        self,
        request: ProductionActivationRequest,
        *,
        compiled_plan: CompiledPlan | None = None,
    ) -> ProductionActivationAuthorizationReceipt:
        if type(request) is not ProductionActivationRequest:
            _reject(
                "ACTIVATION_REQUEST_INVALID",
                "activation request must be one exact immutable value",
            )
        authorization = request.authorization
        if type(authorization) is not ProductionActivationAuthorization:
            _reject(
                "ACTIVATION_REQUEST_INVALID",
                "activation request authorization has the wrong exact type",
            )
        authorization_receipt = self._read_authorization_receipt(authorization)
        plan = request.compiled_plan if compiled_plan is None else compiled_plan
        subject = request.guard_subject

        if (
            plan.repository != authorization.target_repository
            or subject.repository != authorization.target_repository
            or request.source_main_sha != authorization.merged_main_sha
            or request.source_main_tree != authorization.merged_main_git_tree
            or subject.source_commit != authorization.merged_main_sha
            or subject.target_writer_generation
            != authorization.target_writer_generation
            or authorization.target_repository != subject.repository
            or authorization.writer_transition != WRITER_TRANSITION
        ):
            _reject(
                "AUTHORIZATION_IDENTITY_MISMATCH",
                "authorization identity does not match the Plan or Guard subject",
            )

        try:
            plan_valid = plan.has_valid_digest()
            canonical_bytes(plan.compilation_record)
        except Exception as error:
            raise ProductionActivationError(
                "COMPILED_PLAN_DIGEST_MISMATCH",
                "CompiledPlan digest could not be validated",
            ) from error
        if not plan_valid:
            _reject(
                "COMPILED_PLAN_DIGEST_MISMATCH",
                "CompiledPlan bytes do not match the Compiler digest",
            )

        canary = request.canary
        if canary.accepted is not True or canary.blockers:
            _reject(
                "CANARY_NOT_ACCEPTED",
                "CanaryAcceptance is not an accepted, blocker-free result",
            )
        if (
            canary.repository != authorization.target_repository
            or not _is_digest(canary.evidence_package_digest)
            or not _nonempty_text(canary.manifest_ref)
            or type(canary.evidence_refs) is not tuple
            or not canary.evidence_refs
            or any(not _nonempty_text(item) for item in canary.evidence_refs)
            or len(set(canary.evidence_refs)) != len(canary.evidence_refs)
        ):
            _reject(
                "CANARY_EVIDENCE_IDENTITY_INVALID",
                "Canary evidence is not bound to the authorized repository and package identity",
            )
        self._validate_canary_readback(canary, authorization.target_repository)

        receipt = request.guard_receipt
        if receipt is None:
            _reject(
                "GUARD_RECEIPT_REQUIRED",
                "a fresh Guard receipt is required before activation",
            )
        if (
            receipt.schema != RECEIPT_SCHEMA
            or receipt.repository != authorization.target_repository
            or receipt.subject_digest != digest_value(subject.canonical())
            or receipt.receipt_digest
            != digest_value(receipt.canonical_without_digest())
            or receipt.source_writer_generation
            != EXPECTED_SOURCE_WRITER_GENERATION
            or receipt.target_writer_generation
            != authorization.target_writer_generation
            or receipt.store_generation != subject.store_generation
        ):
            _reject(
                "GUARD_RECEIPT_INVALID",
                "Guard receipt is not valid for the exact activation subject",
            )
        validator = getattr(self._controller, "guard", None)
        validate = getattr(validator, "validate_activation", None)
        if not callable(validate):
            _reject(
                "GUARD_RECEIPT_INVALID",
                "activation controller has no Guard activation validator",
            )
        try:
            validate(subject, receipt)
        except Exception as error:
            raise ProductionActivationError(
                "GUARD_RECEIPT_INVALID",
                "Guard receipt no longer validates against current readback",
            ) from error
        return authorization_receipt

    def _capture_plan_identity(
        self,
        plan: CompiledPlan,
    ) -> ProductionActivationPlanIdentity:
        try:
            return ProductionActivationPlanIdentity(
                repository=plan.repository,
                digest=plan.digest,
                canonical_bytes=bytes(plan.canonical_bytes),
                compilation_record_bytes=canonical_bytes(plan.compilation_record),
            )
        except Exception as error:
            raise ProductionActivationError(
                "COMPILED_PLAN_DIGEST_MISMATCH",
                "CompiledPlan metadata could not be canonically encoded",
            ) from error

    def _validate_plan_identity(
        self,
        plan: CompiledPlan,
        identity: ProductionActivationPlanIdentity,
    ) -> None:
        if type(identity) is not ProductionActivationPlanIdentity:
            _reject(
                "COMPILED_PLAN_IDENTITY_CHANGED",
                "preflight has no exact immutable Plan identity",
            )
        try:
            current_record_bytes = canonical_bytes(plan.compilation_record)
        except Exception as error:
            raise ProductionActivationError(
                "COMPILED_PLAN_IDENTITY_CHANGED",
                "CompiledPlan metadata changed outside the canonical JSON domain",
            ) from error
        if (
            plan.repository != identity.repository
            or plan.digest != identity.digest
            or bytes(plan.canonical_bytes) != identity.canonical_bytes
            or current_record_bytes != identity.compilation_record_bytes
            or digest_bytes(plan.canonical_bytes) != plan.digest
        ):
            _reject(
                "COMPILED_PLAN_IDENTITY_CHANGED",
                "CompiledPlan bytes or compilation metadata changed after preflight",
            )

    def _snapshot_compiled_plan(
        self,
        identity: ProductionActivationPlanIdentity,
    ) -> CompiledPlan:
        """Rebuild one detached Plan from the final canonical identity bytes."""

        if type(identity) is not ProductionActivationPlanIdentity:
            _reject(
                "COMPILED_PLAN_IDENTITY_CHANGED",
                "final preflight has no exact immutable Plan identity",
            )
        try:
            canonical_plan_bytes = bytes(identity.canonical_bytes)
            compilation_record_bytes = bytes(identity.compilation_record_bytes)
            compilation_record = load_canonical_json(compilation_record_bytes)
            if type(compilation_record) is not dict:
                raise ValueError("compilation record must be a JSON object")
            snapshot = CompiledPlan(
                repository=identity.repository,
                canonical_bytes=canonical_plan_bytes,
                digest=identity.digest,
                compilation_record=compilation_record,
            )
            if canonical_bytes(snapshot.compilation_record) != compilation_record_bytes:
                raise ValueError("snapshot bytes do not round-trip")
            return snapshot
        except Exception as error:
            raise ProductionActivationError(
                "COMPILED_PLAN_IDENTITY_CHANGED",
                "final preflight Plan snapshot could not be reconstructed",
            ) from error

    def _validate_canary_readback(
        self,
        canary: CanaryAcceptance,
        repository: str,
    ) -> None:
        control = self._canary_evidence_control
        if control is None:
            _reject(
                "CANARY_VERIFIER_REQUIRED",
                "a durable Canary evidence readback dependency is required",
            )
        read = getattr(control, "read", None)
        read_manifest = getattr(control, "read_manifest", None)
        if not callable(read) or not callable(read_manifest):
            _reject(
                "CANARY_EVIDENCE_READBACK_INVALID",
                "Canary evidence control has no typed readback operations",
            )

        manifest_ref = canary.manifest_ref
        evidence_refs = canary.evidence_refs
        if (
            not _durable_canary_ref(manifest_ref)
            or type(evidence_refs) is not tuple
            or not evidence_refs
            or any(not _durable_canary_ref(ref) for ref in evidence_refs)
            or len(set(evidence_refs)) != len(evidence_refs)
        ):
            _reject(
                "CANARY_EVIDENCE_READBACK_INVALID",
                "Canary manifest and evidence references must be durable refs",
            )

        try:
            manifest_bytes = read_manifest(manifest_ref)
        except Exception as error:
            raise ProductionActivationError(
                "CANARY_EVIDENCE_READBACK_INVALID",
                "Canary manifest readback is unavailable",
            ) from error
        if (
            type(manifest_bytes) is not bytes
            or digest_bytes(manifest_bytes) != canary.evidence_package_digest
        ):
            _reject(
                "CANARY_EVIDENCE_READBACK_INVALID",
                "Canary manifest bytes do not match the accepted package digest",
            )
        try:
            package = load_canonical_json(manifest_bytes)
        except Exception as error:
            raise ProductionActivationError(
                "CANARY_EVIDENCE_READBACK_INVALID",
                "Canary manifest is not an exact canonical package",
            ) from error
        if (
            type(package) is not dict
            or set(package)
            != {
                "repository",
                "node_keys",
                "hosted_ci_seconds",
                "coverage",
                "scenario_evidence",
                "candidate_evidence",
                "review_evidence",
                "evidence_refs",
            }
            or package.get("repository") != repository
            or package.get("repository") != canary.repository
            or package.get("evidence_refs") != list(evidence_refs)
        ):
            _reject(
                "CANARY_EVIDENCE_READBACK_INVALID",
                "Canary package repository or evidence identity is stale",
            )

        observed_refs: list[str] = []
        for section_name in (
            "scenario_evidence",
            "candidate_evidence",
            "review_evidence",
        ):
            section = package.get(section_name)
            if type(section) is not dict:
                _reject(
                    "CANARY_EVIDENCE_READBACK_INVALID",
                    "Canary package evidence sections are invalid",
                )
            for raw_evidence in section.values():
                if type(raw_evidence) is not dict:
                    _reject(
                        "CANARY_EVIDENCE_READBACK_INVALID",
                        "Canary package evidence envelope is invalid",
                    )
                try:
                    evidence = TypedEvidence(**raw_evidence)
                    observed = read(evidence.source_ref)
                except Exception as error:
                    raise ProductionActivationError(
                        "CANARY_EVIDENCE_READBACK_INVALID",
                        "Canary Evidence readback is unavailable",
                    ) from error
                if (
                    not evidence.has_valid_digest()
                    or not _durable_canary_ref(evidence.source_ref)
                    or evidence.source_ref in observed_refs
                    or evidence.source_ref not in evidence_refs
                    or type(observed) is not TypedEvidence
                    or observed != evidence
                ):
                    _reject(
                        "CANARY_EVIDENCE_READBACK_INVALID",
                        "Canary Evidence does not exactly read back from its durable ref",
                    )
                observed_refs.append(evidence.source_ref)
        if tuple(sorted(observed_refs)) != evidence_refs:
            _reject(
                "CANARY_EVIDENCE_READBACK_INVALID",
                "Canary package Evidence refs are incomplete or reordered",
            )

    def _read_authorization_receipt(
        self,
        authorization: ProductionActivationAuthorization,
    ) -> ProductionActivationAuthorizationReceipt:
        source = self._authorization_source
        if source is None:
            _reject(
                "AUTHORIZATION_PROVENANCE_REQUIRED",
                "an authoritative owner approval/provenance readback is required",
            )
        read = getattr(source, "read", None)
        if not callable(read):
            _reject(
                "AUTHORIZATION_PROVENANCE_INVALID",
                "authorization source has no typed readback operation",
            )
        try:
            receipt = read(authorization)
        except Exception as error:
            raise ProductionActivationError(
                "AUTHORIZATION_PROVENANCE_INVALID",
                "authorization provenance readback is unavailable",
            ) from error
        if (
            type(receipt) is not ProductionActivationAuthorizationReceipt
            or not receipt.has_valid_digest()
            or receipt.run_id != authorization.run_id
            or receipt.repository != authorization.repository
            or receipt.merged_main_sha != authorization.merged_main_sha
            or receipt.merged_main_git_tree != authorization.merged_main_git_tree
            or receipt.release_subject_digest != authorization.release_subject_digest
            or receipt.target_repository != authorization.target_repository
            or receipt.writer_transition != authorization.writer_transition
            or receipt.target_writer_generation
            != authorization.target_writer_generation
            or receipt.evidence_root != authorization.evidence_root
        ):
            _reject(
                "AUTHORIZATION_PROVENANCE_INVALID",
                "authorization provenance does not exactly match the approved identity",
            )
        return receipt

    def _read_current(self, repository: str) -> CurrentWriter:
        try:
            current = self._controller.transitions.read_current(repository)
        except Exception as error:
            raise ProductionActivationError(
                "SOURCE_WRITER_READBACK_INVALID",
                "current writer readback is unavailable",
            ) from error
        if (
            type(current) is not CurrentWriter
            or current.repository != repository
            or not _nonempty_text(current.record_id)
        ):
            _reject(
                "SOURCE_WRITER_READBACK_INVALID",
                "current writer readback has the wrong identity",
            )
        return current

    def _read_completed_outcome(
        self,
        request: ProductionActivationRequest,
        *,
        compiled_plan: CompiledPlan | None = None,
    ) -> WriterTransitionOutcome | _PendingActivation | _NotCompleted:
        plan = request.compiled_plan if compiled_plan is None else compiled_plan
        self._validate_common(request, compiled_plan=plan)
        repository = request.authorization.target_repository
        current = self._read_current(repository)
        if current.writer_generation != request.authorization.target_writer_generation:
            return _NotCompleted()
        record = self._read_transition_record(repository, current.record_id)
        if (
            record.kind == "cutover_pending"
            and record.status == "pending"
        ):
            if self._pending_matches(
                request,
                current,
                record,
                compiled_plan=plan,
            ):
                return _PendingActivation()
            _reject(
                "ACTIVATION_READBACK_INVALID",
                "current pending cutover is not the exact activation request",
            )
        if (
            record.kind != "cutover"
            or record.status != "cut_over"
            or record.writer_generation
            != request.authorization.target_writer_generation
            or record.plan_digest != plan.digest
            or record.canary_evidence_digest
            != request.canary.evidence_package_digest
            or record.worker_capacity != request.worker_capacity
            or record.coordinator_capacity != request.coordinator_capacity
        ):
            _reject(
                "ACTIVATION_READBACK_INVALID",
                "current target writer is not the exact requested activation",
            )
        outcome = WriterTransitionOutcome(
            status="cut_over",
            repository=repository,
            writer_generation=record.writer_generation,
            record_id=record.record_id,
            activation_id=record.activation_id,
            worker_capacity=record.worker_capacity,
            coordinator_capacity=record.coordinator_capacity,
        )
        self._validate_activation_readback(
            request,
            outcome,
            compiled_plan=plan,
        )
        return outcome

    def _read_transition_record(
        self,
        repository: str,
        record_id: str,
    ) -> WriterTransitionRecord:
        try:
            record = self._controller.transitions.read(repository, record_id)
        except Exception as error:
            raise ProductionActivationError(
                "ACTIVATION_READBACK_INVALID",
                "writer transition record readback is unavailable",
            ) from error
        if type(record) is not WriterTransitionRecord:
            _reject(
                "ACTIVATION_READBACK_INVALID",
                "writer transition readback has the wrong type",
            )
        return record

    def _pending_matches(
        self,
        request: ProductionActivationRequest,
        current: CurrentWriter,
        record: WriterTransitionRecord,
        *,
        compiled_plan: CompiledPlan | None = None,
    ) -> bool:
        plan = request.compiled_plan if compiled_plan is None else compiled_plan
        return (
            type(current) is CurrentWriter
            and type(record) is WriterTransitionRecord
            and record.repository == request.authorization.target_repository
            and current.record_id == record.record_id
            and current.writer_generation
            == request.authorization.target_writer_generation
            and record.kind == "cutover_pending"
            and record.status == "pending"
            and record.previous_writer_generation
            == EXPECTED_SOURCE_WRITER_GENERATION
            and record.writer_generation
            == request.authorization.target_writer_generation
            and record.activation_id is None
            and record.plan_digest == plan.digest
            and record.canary_evidence_digest
            == request.canary.evidence_package_digest
            and record.canary_evidence_refs == request.canary.evidence_refs
            and record.canary_manifest_ref == request.canary.manifest_ref
            and record.worker_capacity == 0
            and record.coordinator_capacity == 0
            and record.reason is None
        )

    def _validate_activation_readback(
        self,
        request: ProductionActivationRequest,
        outcome: WriterTransitionOutcome,
        *,
        compiled_plan: CompiledPlan | None = None,
    ) -> None:
        plan = request.compiled_plan if compiled_plan is None else compiled_plan
        if (
            type(outcome) is not WriterTransitionOutcome
            or outcome.status != "cut_over"
            or outcome.repository != request.authorization.target_repository
            or outcome.writer_generation
            != request.authorization.target_writer_generation
            or not _nonempty_text(outcome.record_id)
            or not _nonempty_text(outcome.activation_id)
            or outcome.worker_capacity != _EXPECTED_WORKER_CAPACITY
            or outcome.coordinator_capacity != _EXPECTED_COORDINATOR_CAPACITY
        ):
            _reject(
                "ACTIVATION_READBACK_INVALID",
                "WriterCutoverController did not return a complete cutover outcome",
            )

        repository = request.authorization.target_repository
        transitions = self._controller.transitions
        current = self._read_current(repository)
        if (
            current.writer_generation
            != request.authorization.target_writer_generation
            or current.record_id != outcome.record_id
        ):
            _reject(
                "DEFAULT_WRITER_READBACK_INVALID",
                "default writer readback does not name the requested cutover",
            )
        try:
            record = transitions.read(repository, outcome.record_id)
        except Exception as error:
            raise ProductionActivationError(
                "DEFAULT_WRITER_READBACK_INVALID",
                "writer transition record readback is unavailable",
            ) from error
        if (
            type(record) is not WriterTransitionRecord
            or record.repository != repository
            or record.kind != "cutover"
            or record.status != "cut_over"
            or record.activation_id != outcome.activation_id
            or record.writer_generation
            != request.authorization.target_writer_generation
            or record.plan_digest != plan.digest
            or record.canary_evidence_digest
            != request.canary.evidence_package_digest
            or record.canary_evidence_refs != request.canary.evidence_refs
            or record.canary_manifest_ref != request.canary.manifest_ref
            or record.worker_capacity != _EXPECTED_WORKER_CAPACITY
            or record.coordinator_capacity != _EXPECTED_COORDINATOR_CAPACITY
        ):
            _reject(
                "DEFAULT_WRITER_READBACK_INVALID",
                "writer transition record does not match the exact activation",
            )

        try:
            allows = transitions.allows(
                repository,
                request.authorization.target_writer_generation,
                outcome.activation_id,
            )
            allows_new_work = transitions.allows_new_work(
                repository,
                request.authorization.target_writer_generation,
                outcome.activation_id,
            )
            capacities = transitions.capacity_limits(
                repository,
                request.authorization.target_writer_generation,
                outcome.activation_id,
            )
        except Exception as error:
            raise ProductionActivationError(
                "DEFAULT_WRITER_READBACK_INVALID",
                "writer transition capability readback is unavailable",
            ) from error
        if allows is not True or allows_new_work is not True or capacities != (8, 1):
            _reject(
                "DEFAULT_WRITER_READBACK_INVALID",
                "default writer does not authorize the exact 8/1 activation",
            )

        publication = self._controller.publication
        try:
            active = publication.read_active(repository)
            receipt = publication.durable.read_current_activation(repository)
            durable_plan = publication.durable.read_plan(
                repository,
                plan.digest,
            )
        except Exception as error:
            raise ProductionActivationError(
                "ACTIVATION_READBACK_INVALID",
                "Activation Receipt or active Plan readback is unavailable",
            ) from error
        if (
            type(active) is not PublishedPlan
            or active.repository != repository
            or active.plan_digest != plan.digest
            or active.writer_generation
            != request.authorization.target_writer_generation
            or active.activation_id != outcome.activation_id
            or active.canonical_bytes != plan.canonical_bytes
            or active.compilation_record != plan.compilation_record
            or digest_bytes(active.canonical_bytes) != plan.digest
            or type(receipt) is not ActivationReceipt
            or receipt.repository != repository
            or receipt.plan_digest != plan.digest
            or receipt.writer_generation
            != request.authorization.target_writer_generation
            or receipt.activation_id != outcome.activation_id
            or type(durable_plan) is not DurablePlanRecord
            or durable_plan.repository != repository
            or durable_plan.plan_digest != plan.digest
            or durable_plan.canonical_bytes != plan.canonical_bytes
            or durable_plan.compilation_record
            != plan.compilation_record
        ):
            _reject(
                "ACTIVATION_READBACK_INVALID",
                "Activation Receipt and active Plan are not exact readbacks",
            )


__all__ = [
    "ProductionActivationAuthorization",
    "ProductionActivationComposition",
    "ProductionActivationCompositionFactory",
    "ProductionActivationAuthorizationReceipt",
    "ProductionActivationAuthorizationSource",
    "ProductionActivationError",
    "ProductionActivationFacade",
    "ProductionActivationPlanIdentity",
    "ProductionActivationPreflight",
    "ProductionActivationRequest",
    "WRITER_TRANSITION",
]
