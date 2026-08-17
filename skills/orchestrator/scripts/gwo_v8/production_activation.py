"""Fail-closed composition for the explicitly authorized V8 activation."""

from __future__ import annotations

from dataclasses import dataclass
import re

from ._canonical import digest_bytes, digest_value
from .activation import ActivationReceipt, DurablePlanRecord, PublishedPlan
from .compiler import CompiledPlan
from .cutover_guard import (
    CutoverGuardReceipt,
    CutoverSubject,
    EXPECTED_SOURCE_WRITER_GENERATION,
    RECEIPT_SCHEMA,
)
from .transition import (
    CanaryAcceptance,
    CurrentWriter,
    WriterCutoverController,
    WriterTransitionOutcome,
    WriterTransitionRecord,
)


_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_EXPECTED_WORKER_CAPACITY = 8
_EXPECTED_COORDINATOR_CAPACITY = 1


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


def _reject(code: str, detail: str) -> None:
    raise ProductionActivationError(code, detail)


@dataclass(frozen=True)
class ProductionActivationAuthorization:
    """The exact owner authorization identity for one activation attempt."""

    run_id: str
    repository: str
    source_main_sha: str
    source_main_tree: str
    target_writer_generation: str
    evidence_root: str

    def __post_init__(self) -> None:
        for name in self.__dataclass_fields__:
            value = getattr(self, name)
            if not _nonempty_text(value):
                raise ValueError(
                    f"ProductionActivationAuthorization.{name} must be non-empty text"
                )


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
class ProductionActivationPreflight:
    """The read-only, identity-bound result immediately before execute."""

    request: ProductionActivationRequest
    current_writer: CurrentWriter
    plan_digest: str
    canary_evidence_digest: str
    guard_receipt_digest: str
    checks: tuple[str, ...]


class _NotCompleted:
    pass


class ProductionActivationFacade:
    """Compose existing Guard, publication, and transition protocols once."""

    def __init__(self, controller: WriterCutoverController):
        self._controller = controller

    def preflight(
        self,
        request: ProductionActivationRequest,
    ) -> ProductionActivationPreflight:
        self._validate_common(request)
        current = self._read_current(request.authorization.repository)
        if current.writer_generation != EXPECTED_SOURCE_WRITER_GENERATION:
            _reject(
                "SOURCE_WRITER_INVALID",
                "production activation requires the current V6.1 writer",
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
            current_writer=current,
            plan_digest=request.compiled_plan.digest,
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
            current = self._read_current(request.authorization.repository)
            if current.writer_generation == authorization.target_writer_generation:
                completed = self._read_completed_outcome(request)
                if not isinstance(completed, _NotCompleted):
                    if type(completed) is not WriterTransitionOutcome:
                        _reject(
                            "ACTIVATION_READBACK_INVALID",
                            "completed activation readback has the wrong type",
                        )
                    return completed
            self.preflight(request)
        else:
            completed = self._read_completed_outcome(request)
            if not isinstance(completed, _NotCompleted):
                if type(completed) is not WriterTransitionOutcome:
                    _reject(
                        "ACTIVATION_READBACK_INVALID",
                        "completed activation readback has the wrong type",
                    )
                return completed
            self.preflight(request)

        outcome = self._controller.cutover(
            request.compiled_plan,
            canary=request.canary,
            guard_subject=request.guard_subject,
            guard_receipt=request.guard_receipt,
            writer_generation=authorization.target_writer_generation,
            worker_capacity=request.worker_capacity,
            coordinator_capacity=request.coordinator_capacity,
        )
        self._validate_activation_readback(request, outcome)
        return outcome

    def _validate_common(self, request: ProductionActivationRequest) -> None:
        if type(request) is not ProductionActivationRequest:
            _reject(
                "ACTIVATION_REQUEST_INVALID",
                "activation request must be one exact immutable value",
            )
        authorization = request.authorization
        plan = request.compiled_plan
        subject = request.guard_subject

        if (
            plan.repository != authorization.repository
            or subject.repository != authorization.repository
            or request.source_main_sha != authorization.source_main_sha
            or request.source_main_tree != authorization.source_main_tree
            or subject.source_commit != authorization.source_main_sha
            or subject.target_writer_generation
            != authorization.target_writer_generation
        ):
            _reject(
                "AUTHORIZATION_IDENTITY_MISMATCH",
                "authorization identity does not match the Plan or Guard subject",
            )

        try:
            plan_valid = plan.has_valid_digest()
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
            canary.repository != authorization.repository
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

        receipt = request.guard_receipt
        if receipt is None:
            _reject(
                "GUARD_RECEIPT_REQUIRED",
                "a fresh Guard receipt is required before activation",
            )
        if (
            receipt.schema != RECEIPT_SCHEMA
            or receipt.repository != authorization.repository
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
    ) -> WriterTransitionOutcome | _NotCompleted:
        self._validate_common(request)
        repository = request.authorization.repository
        current = self._read_current(repository)
        if current.writer_generation != request.authorization.target_writer_generation:
            return _NotCompleted()
        try:
            record = self._controller.transitions.read(repository, current.record_id)
        except Exception as error:
            raise ProductionActivationError(
                "ACTIVATION_READBACK_INVALID",
                "completed writer transition readback is unavailable",
            ) from error
        if (
            type(record) is not WriterTransitionRecord
            or record.kind != "cutover"
            or record.status != "cut_over"
            or record.writer_generation
            != request.authorization.target_writer_generation
            or record.plan_digest != request.compiled_plan.digest
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
        self._validate_activation_readback(request, outcome)
        return outcome

    def _validate_activation_readback(
        self,
        request: ProductionActivationRequest,
        outcome: WriterTransitionOutcome,
    ) -> None:
        if (
            type(outcome) is not WriterTransitionOutcome
            or outcome.status != "cut_over"
            or outcome.repository != request.authorization.repository
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

        repository = request.authorization.repository
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
            or record.plan_digest != request.compiled_plan.digest
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
                request.compiled_plan.digest,
            )
        except Exception as error:
            raise ProductionActivationError(
                "ACTIVATION_READBACK_INVALID",
                "Activation Receipt or active Plan readback is unavailable",
            ) from error
        if (
            type(active) is not PublishedPlan
            or active.repository != repository
            or active.plan_digest != request.compiled_plan.digest
            or active.writer_generation
            != request.authorization.target_writer_generation
            or active.activation_id != outcome.activation_id
            or active.canonical_bytes != request.compiled_plan.canonical_bytes
            or active.compilation_record != request.compiled_plan.compilation_record
            or digest_bytes(active.canonical_bytes) != request.compiled_plan.digest
            or type(receipt) is not ActivationReceipt
            or receipt.repository != repository
            or receipt.plan_digest != request.compiled_plan.digest
            or receipt.writer_generation
            != request.authorization.target_writer_generation
            or receipt.activation_id != outcome.activation_id
            or type(durable_plan) is not DurablePlanRecord
            or durable_plan.repository != repository
            or durable_plan.plan_digest != request.compiled_plan.digest
            or durable_plan.canonical_bytes != request.compiled_plan.canonical_bytes
            or durable_plan.compilation_record
            != request.compiled_plan.compilation_record
        ):
            _reject(
                "ACTIVATION_READBACK_INVALID",
                "Activation Receipt and active Plan are not exact readbacks",
            )


__all__ = [
    "ProductionActivationAuthorization",
    "ProductionActivationError",
    "ProductionActivationFacade",
    "ProductionActivationPreflight",
    "ProductionActivationRequest",
]
