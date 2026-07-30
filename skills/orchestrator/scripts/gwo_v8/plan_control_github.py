"""GitHub-CAS durable repository for PlanControl v3 state.

Every PlanControl transition is applied to one closed state document and
published with GitHub Contents compare-and-swap followed by exact readback.
The document intentionally includes in-flight Planning attempts and
non-executable reservations, not only activated Plans.
"""

from __future__ import annotations

import base64
from dataclasses import asdict
from typing import Any, Callable, Mapping, Protocol, TypeVar

from ._canonical import canonical_bytes, load_canonical_json
from .activation import GitHubContentClient
from .plan_control import (
    ActivationReceipt,
    CampaignHandle,
    InMemoryPlanRepository,
    PlanControlError,
    PlanRevision,
    PlanningReservation,
    TicketClaimProof,
    _PlanningAttempt,
    _SplitCampaignDecisionRecord,
)
from .runtime_gateway import CampaignPlanningSubject


_STATE_SCHEMA = "gwo.plan.github-state.v1"
_DEFAULT_PATH = ".gwo-v8/plan-control-v3.json"
_MAXIMUM_STATE_BYTES = 16_777_216
_T = TypeVar("_T")


class WriterGenerationReadback(Protocol):
    def read_current(self, repository: str) -> object: ...


def _exact(value: object, keys: set[str], label: str) -> dict[str, Any]:
    if type(value) is not dict or set(value) != keys:
        raise PlanControlError(
            "DURABLE_STATE_INVALID",
            f"Durable {label} has an unknown schema",
        )
    return value


def _text(value: object, label: str) -> str:
    if type(value) is not str or not value:
        raise PlanControlError(
            "DURABLE_STATE_INVALID",
            f"Durable {label} must be exact non-empty text",
        )
    return value


def _texts(value: object, label: str) -> tuple[str, ...]:
    if (
        type(value) is not list
        or any(type(item) is not str or not item for item in value)
        or len(set(value)) != len(value)
    ):
        raise PlanControlError(
            "DURABLE_STATE_INVALID",
            f"Durable {label} must be unique exact text",
        )
    return tuple(value)


def _bytes(value: object, label: str) -> bytes:
    if type(value) is not str:
        raise PlanControlError(
            "DURABLE_STATE_INVALID",
            f"Durable {label} is not base64 text",
        )
    try:
        return base64.b64decode(value, validate=True)
    except ValueError as error:
        raise PlanControlError(
            "DURABLE_STATE_INVALID",
            f"Durable {label} is not exact base64",
        ) from error


def _encoded(value: bytes) -> str:
    return base64.b64encode(value).decode("ascii")


def _revision_value(revision: PlanRevision) -> dict[str, Any]:
    return {
        "repository": revision.repository,
        "campaign_key": revision.campaign_key,
        "snapshot_digest": revision.snapshot_digest,
        "canonical_bytes_base64": _encoded(revision.canonical_bytes),
        "digest": revision.digest,
    }


def _revision_from(value: object) -> PlanRevision:
    item = _exact(
        value,
        {
            "repository",
            "campaign_key",
            "snapshot_digest",
            "canonical_bytes_base64",
            "digest",
        },
        "Plan Revision",
    )
    return PlanRevision(
        repository=_text(item["repository"], "Plan Revision repository"),
        campaign_key=_text(item["campaign_key"], "Plan Revision Campaign"),
        snapshot_digest=_text(
            item["snapshot_digest"],
            "Plan Revision snapshot digest",
        ),
        canonical_bytes=_bytes(
            item["canonical_bytes_base64"],
            "Plan Revision bytes",
        ),
        digest=_text(item["digest"], "Plan Revision digest"),
    )


def _activation_value(receipt: ActivationReceipt) -> dict[str, Any]:
    return {
        **asdict(receipt),
        "ready_refs": list(receipt.ready_refs),
        "ticket_keys": list(receipt.ticket_keys),
    }


def _activation_from(value: object) -> ActivationReceipt:
    fields = set(ActivationReceipt.__dataclass_fields__)
    item = _exact(value, fields, "Activation Receipt")
    return ActivationReceipt(
        **{
            **item,
            "ready_refs": _texts(
                item["ready_refs"],
                "Activation Receipt ready refs",
            ),
            "ticket_keys": _texts(
                item["ticket_keys"],
                "Activation Receipt Ticket keys",
            ),
        }
    )


def _planning_value(reservation: PlanningReservation) -> dict[str, Any]:
    return {
        **asdict(reservation),
        "ticket_keys": list(reservation.ticket_keys),
    }


def _planning_from(value: object) -> PlanningReservation:
    fields = set(PlanningReservation.__dataclass_fields__)
    item = _exact(value, fields, "Planning reservation")
    return PlanningReservation(
        **{
            **item,
            "ticket_keys": _texts(
                item["ticket_keys"],
                "Planning reservation Ticket keys",
            ),
        }
    )


def _attempt_value(attempt: _PlanningAttempt) -> dict[str, Any]:
    return {
        "handle": asdict(attempt.handle),
        "ready_refs": list(attempt.ready_refs),
        "ticket_keys": list(attempt.ticket_keys),
        "expected_previous_revision_digest": (
            attempt.expected_previous_revision_digest
        ),
        "snapshot_bytes_base64": _encoded(attempt.snapshot_bytes),
        "snapshot_artifact_digest": attempt.snapshot_artifact_digest,
        "policy_witness_digest": attempt.policy_witness_digest,
        "planning_request_artifact_digest": (
            attempt.planning_request_artifact_digest
        ),
        "subject": attempt.subject.canonical(),
        "compilation_record_artifact_digest": (
            attempt.compilation_record_artifact_digest
        ),
        "revision": (
            None if attempt.revision is None else _revision_value(attempt.revision)
        ),
    }


def _attempt_from(value: object) -> _PlanningAttempt:
    item = _exact(
        value,
        {
            "handle",
            "ready_refs",
            "ticket_keys",
            "expected_previous_revision_digest",
            "snapshot_bytes_base64",
            "snapshot_artifact_digest",
            "policy_witness_digest",
            "planning_request_artifact_digest",
            "subject",
            "compilation_record_artifact_digest",
            "revision",
        },
        "Planning attempt",
    )
    handle_value = _exact(
        item["handle"],
        {"repository", "campaign_key"},
        "Campaign handle",
    )
    subject_value = _exact(
        item["subject"],
        {
            "kind",
            "repository",
            "campaign_key",
            "campaign_handle",
            "expected_previous_plan_revision_digest",
            "snapshot_artifact_digest",
            "policy_witness_digest",
            "planning_request_artifact_digest",
            "stable_action_id",
        },
        "Planning subject",
    )
    if subject_value["kind"] != "campaign_planning":
        raise PlanControlError(
            "DURABLE_STATE_INVALID",
            "Durable Planning subject kind is invalid",
        )
    revision_value = item["revision"]
    return _PlanningAttempt(
        handle=CampaignHandle(
            _text(handle_value["repository"], "Campaign repository"),
            _text(handle_value["campaign_key"], "Campaign key"),
        ),
        ready_refs=_texts(item["ready_refs"], "Planning ready refs"),
        ticket_keys=_texts(item["ticket_keys"], "Planning Ticket keys"),
        expected_previous_revision_digest=item[
            "expected_previous_revision_digest"
        ],
        snapshot_bytes=_bytes(
            item["snapshot_bytes_base64"],
            "Planning snapshot bytes",
        ),
        snapshot_artifact_digest=_text(
            item["snapshot_artifact_digest"],
            "Planning snapshot digest",
        ),
        policy_witness_digest=_text(
            item["policy_witness_digest"],
            "Planning Policy Witness digest",
        ),
        planning_request_artifact_digest=_text(
            item["planning_request_artifact_digest"],
            "Planning request digest",
        ),
        subject=CampaignPlanningSubject(
            **{
                key: child
                for key, child in subject_value.items()
                if key != "kind"
            }
        ),
        compilation_record_artifact_digest=item[
            "compilation_record_artifact_digest"
        ],
        revision=(
            None if revision_value is None else _revision_from(revision_value)
        ),
    )


def _split_value(record: _SplitCampaignDecisionRecord) -> dict[str, Any]:
    return {
        "handle": asdict(record.handle),
        "ready_refs": list(record.ready_refs),
        "ticket_keys": list(record.ticket_keys),
        "expected_previous_revision_digest": (
            record.expected_previous_revision_digest
        ),
        "canonical_bytes_base64": _encoded(record.canonical_bytes),
        "digest": record.digest,
    }


def _split_from(value: object) -> _SplitCampaignDecisionRecord:
    item = _exact(
        value,
        {
            "handle",
            "ready_refs",
            "ticket_keys",
            "expected_previous_revision_digest",
            "canonical_bytes_base64",
            "digest",
        },
        "split-Campaign Decision",
    )
    handle = _exact(
        item["handle"],
        {"repository", "campaign_key"},
        "split-Campaign handle",
    )
    return _SplitCampaignDecisionRecord(
        handle=CampaignHandle(
            _text(handle["repository"], "split-Campaign repository"),
            _text(handle["campaign_key"], "split-Campaign key"),
        ),
        ready_refs=_texts(item["ready_refs"], "split-Campaign ready refs"),
        ticket_keys=_texts(item["ticket_keys"], "split-Campaign Ticket keys"),
        expected_previous_revision_digest=item[
            "expected_previous_revision_digest"
        ],
        canonical_bytes=_bytes(
            item["canonical_bytes_base64"],
            "split-Campaign Decision bytes",
        ),
        digest=_text(item["digest"], "split-Campaign Decision digest"),
    )


def _empty_state(repository: str, writer_generation: str) -> dict[str, Any]:
    return {
        "schema_version": _STATE_SCHEMA,
        "repository": repository,
        "writer_generation": writer_generation,
        "writer_fence": {
            "repository": repository,
            "writer_generation": writer_generation,
        },
        "attempts": [],
        "split_decisions": [],
        "runtime_assertions": [],
        "planning_reservations": [],
        "pending_reservations": [],
        "claims": [],
        "revisions": [],
        "activations": [],
    }


def _repo_value(
    repository: str,
    writer_generation: str,
    repo: InMemoryPlanRepository,
) -> dict[str, Any]:
    return {
        "schema_version": _STATE_SCHEMA,
        "repository": repository,
        "writer_generation": writer_generation,
        "writer_fence": {
            "repository": repository,
            "writer_generation": writer_generation,
        },
        "attempts": sorted(
            (_attempt_value(item) for item in repo.attempts.values()),
            key=lambda item: (
                item["handle"]["campaign_key"],
                item["expected_previous_revision_digest"] or "",
            ),
        ),
        "split_decisions": sorted(
            (_split_value(item) for item in repo.split_decisions.values()),
            key=lambda item: (
                item["handle"]["campaign_key"],
                item["expected_previous_revision_digest"] or "",
            ),
        ),
        "runtime_assertions": [
            {
                "campaign_key": campaign_key,
                "assertion": value,
            }
            for (_repository, campaign_key), value in sorted(
                repo.runtime_assertions.items()
            )
        ],
        "planning_reservations": sorted(
            (
                _planning_value(item)
                for item in repo.planning_reservations.values()
            ),
            key=lambda item: (
                item["campaign_key"],
                item["stable_action_id"],
            ),
        ),
        "pending_reservations": sorted(
            (
                _activation_value(item)
                for item in repo.pending_reservations.values()
            ),
            key=lambda item: (
                item["campaign_key"],
                item["revision_digest"],
            ),
        ),
        "claims": [
            {
                "ticket_key": ticket_key,
                "revision_digest": revision_digest,
                "campaign_key": repo._claim_campaigns[
                    (claim_repository, ticket_key)
                ],
            }
            for (claim_repository, ticket_key), revision_digest in sorted(
                repo.claims.items()
            )
        ],
        "revisions": sorted(
            (_revision_value(item) for item in repo.revisions.values()),
            key=lambda item: item["digest"],
        ),
        "activations": sorted(
            (_activation_value(item) for item in repo.activations.values()),
            key=lambda item: item["campaign_key"],
        ),
    }


def _repo_from_state(
    value: object,
    repository: str,
    writer_generation: str,
) -> InMemoryPlanRepository:
    state = _exact(
        value,
        set(_empty_state(repository, writer_generation)),
        "PlanControl state",
    )
    if (
        state["schema_version"] != _STATE_SCHEMA
        or state["repository"] != repository
        or state["writer_generation"] != writer_generation
    ):
        raise PlanControlError(
            "DURABLE_STATE_INVALID",
            "Durable PlanControl state changed its repository or writer fence",
        )
    writer_fence = _exact(
        state["writer_fence"],
        {"repository", "writer_generation"},
        "writer fence",
    )
    if writer_fence != {
        "repository": repository,
        "writer_generation": writer_generation,
    }:
        raise PlanControlError(
            "DURABLE_STATE_INVALID",
            "Durable PlanControl writer fence changed identity",
        )
    list_fields = {
        "attempts",
        "split_decisions",
        "runtime_assertions",
        "planning_reservations",
        "pending_reservations",
        "claims",
        "revisions",
        "activations",
    }
    if any(type(state[field]) is not list for field in list_fields):
        raise PlanControlError(
            "DURABLE_STATE_INVALID",
            "Durable PlanControl state collections must be exact lists",
        )
    repo = InMemoryPlanRepository(writer_generation=writer_generation)
    try:
        for raw in state["attempts"]:
            attempt = _attempt_from(raw)
            if attempt.handle.repository != repository:
                raise PlanControlError(
                    "DURABLE_STATE_INVALID",
                    "Planning attempt belongs to another repository",
                )
            key = repo._attempt_key(
                attempt.handle,
                attempt.expected_previous_revision_digest,
            )
            if key in repo.attempts:
                raise PlanControlError(
                    "DURABLE_STATE_INVALID",
                    "Durable Planning attempts repeat an identity",
                )
            repo.attempts[key] = attempt
        for raw in state["split_decisions"]:
            decision = _split_from(raw)
            key = repo._attempt_key(
                decision.handle,
                decision.expected_previous_revision_digest,
            )
            if decision.handle.repository != repository or key in repo.split_decisions:
                raise PlanControlError(
                    "DURABLE_STATE_INVALID",
                    "Durable split-Campaign Decisions repeat or cross repositories",
                )
            repo.split_decisions[key] = decision
        for raw in state["runtime_assertions"]:
            item = _exact(
                raw,
                {"campaign_key", "assertion"},
                "Runtime assertion",
            )
            key = (
                repository,
                _text(item["campaign_key"], "Runtime assertion Campaign"),
            )
            if key in repo.runtime_assertions or type(item["assertion"]) is not dict:
                raise PlanControlError(
                    "DURABLE_STATE_INVALID",
                    "Durable Runtime assertion is duplicated or malformed",
                )
            repo.runtime_assertions[key] = item["assertion"]
        for raw in state["planning_reservations"]:
            reservation = _planning_from(raw)
            key = repo._planning_reservation_key(reservation)
            if reservation.repository != repository or key in repo.planning_reservations:
                raise PlanControlError(
                    "DURABLE_STATE_INVALID",
                    "Durable Planning reservations repeat or cross repositories",
                )
            repo.planning_reservations[key] = reservation
        for raw in state["pending_reservations"]:
            receipt = _activation_from(raw)
            key = repo._reservation_key(receipt)
            if receipt.repository != repository or key in repo.pending_reservations:
                raise PlanControlError(
                    "DURABLE_STATE_INVALID",
                    "Durable activation reservations repeat or cross repositories",
                )
            repo.pending_reservations[key] = receipt
        for raw in state["claims"]:
            item = _exact(
                raw,
                {"ticket_key", "revision_digest", "campaign_key"},
                "Ticket claim",
            )
            ticket_key = _text(item["ticket_key"], "Ticket claim key")
            key = (repository, ticket_key)
            if key in repo.claims:
                raise PlanControlError(
                    "DURABLE_STATE_INVALID",
                    "Durable Ticket claims repeat an identity",
                )
            repo.claims[key] = _text(
                item["revision_digest"],
                "Ticket claim revision",
            )
            repo._claim_campaigns[key] = _text(
                item["campaign_key"],
                "Ticket claim Campaign",
            )
        for raw in state["revisions"]:
            revision = _revision_from(raw)
            if (
                revision.repository != repository
                or revision.digest in repo.revisions
            ):
                raise PlanControlError(
                    "DURABLE_STATE_INVALID",
                    "Durable Plan Revisions repeat or cross repositories",
                )
            repo.revisions[revision.digest] = revision
        for raw in state["activations"]:
            receipt = _activation_from(raw)
            key = (receipt.repository, receipt.campaign_key)
            if receipt.repository != repository or key in repo.activations:
                raise PlanControlError(
                    "DURABLE_STATE_INVALID",
                    "Durable activations repeat or cross repositories",
                )
            repo.activations[key] = receipt
    except PlanControlError:
        raise
    except Exception as error:
        raise PlanControlError(
            "DURABLE_STATE_INVALID",
            "Durable PlanControl state cannot be reconstructed",
        ) from error
    if _repo_value(repository, writer_generation, repo) != state:
        raise PlanControlError(
            "DURABLE_STATE_INVALID",
            "Durable PlanControl state is not in exact canonical order",
        )
    return repo


class GitHubPlanRepository:
    """Complete PlanControlRepository backed by one GitHub CAS document."""

    def __init__(
        self,
        client: GitHubContentClient,
        *,
        repository: str,
        branch: str,
        writer_generation: str,
        writer_control: WriterGenerationReadback,
        path: str = _DEFAULT_PATH,
        maximum_state_bytes: int = _MAXIMUM_STATE_BYTES,
    ):
        if any(
            type(value) is not str or not value
            for value in (repository, branch, writer_generation, path)
        ):
            raise PlanControlError(
                "PLAN_CONTROL_COMPOSITION_INVALID",
                "GitHub PlanControl repository identity is incomplete",
            )
        if type(maximum_state_bytes) is not int or maximum_state_bytes < 1:
            raise PlanControlError(
                "PLAN_CONTROL_COMPOSITION_INVALID",
                "GitHub PlanControl state bound must be positive",
            )
        self.client = client
        self.repository = repository
        self.branch = branch
        self.writer_generation = writer_generation
        self.writer_control = writer_control
        self.path = path.strip("/")
        self.maximum_state_bytes = maximum_state_bytes

    def _assert_repository(self, repository: str) -> None:
        if repository != self.repository:
            raise PlanControlError(
                "DURABLE_REPOSITORY_MISMATCH",
                "GitHub PlanControl repository is fixed at composition",
            )

    def _assert_writer(self) -> None:
        try:
            current = self.writer_control.read_current(self.repository)
        except Exception as error:
            raise PlanControlError(
                "WRITER_FENCE_READBACK_INVALID",
                "GitHub writer-generation fence is not readable",
            ) from error
        if (
            getattr(current, "repository", None) != self.repository
            or getattr(current, "writer_generation", None)
            != self.writer_generation
            or type(getattr(current, "record_id", None)) is not str
            or not current.record_id
        ):
            raise PlanControlError(
                "WRITER_FENCE_CONFLICT",
                "Configured writer generation does not own the GitHub control state",
            )

    def _read(self) -> tuple[InMemoryPlanRepository, str | None, bytes | None]:
        self._assert_writer()
        try:
            content = self.client.read(
                self.repository,
                self.branch,
                self.path,
            )
        except Exception as error:
            raise PlanControlError(
                "DURABLE_STATE_UNAVAILABLE",
                "GitHub PlanControl state cannot be read",
            ) from error
        if content is None:
            return (
                InMemoryPlanRepository(
                    writer_generation=self.writer_generation
                ),
                None,
                None,
            )
        if (
            type(content.content) is not bytes
            or type(content.blob_sha) is not str
            or not content.blob_sha
            or len(content.content) > self.maximum_state_bytes
        ):
            raise PlanControlError(
                "DURABLE_STATE_INVALID",
                "GitHub PlanControl content receipt is malformed or oversized",
            )
        try:
            state = load_canonical_json(content.content)
            repo = _repo_from_state(
                state,
                self.repository,
                self.writer_generation,
            )
        except PlanControlError:
            raise
        except Exception as error:
            raise PlanControlError(
                "DURABLE_STATE_INVALID",
                "GitHub PlanControl state is not canonical JSON",
            ) from error
        return repo, content.blob_sha, content.content

    def _mutate(
        self,
        operation: str,
        callback: Callable[[InMemoryPlanRepository], _T],
    ) -> _T:
        repo, blob_sha, before = self._read()
        result = callback(repo)
        rendered = canonical_bytes(
            _repo_value(self.repository, self.writer_generation, repo)
        )
        if len(rendered) > self.maximum_state_bytes:
            raise PlanControlError(
                "DURABLE_STATE_TOO_LARGE",
                "GitHub PlanControl state exceeds its configured bound",
            )
        if before == rendered:
            return result
        try:
            written = self.client.compare_and_swap(
                self.repository,
                self.branch,
                self.path,
                rendered,
                expected_blob_sha=blob_sha,
                message=f"GWO PlanControl {operation}",
            )
            if written.content != rendered:
                raise PlanControlError(
                    "DURABLE_STATE_READBACK_INVALID",
                    "GitHub CAS receipt returned different PlanControl bytes",
                )
        except PlanControlError:
            raise
        except Exception as error:
            try:
                recovered = self.client.read(
                    self.repository,
                    self.branch,
                    self.path,
                )
            except Exception:
                recovered = None
            if recovered is None or recovered.content != rendered:
                raise PlanControlError(
                    "DURABLE_CAS_CONFLICT",
                    "GitHub PlanControl CAS did not commit the exact transition",
                ) from error
        try:
            readback = self.client.read(
                self.repository,
                self.branch,
                self.path,
            )
        except Exception as error:
            raise PlanControlError(
                "DURABLE_STATE_READBACK_INVALID",
                "GitHub PlanControl transition cannot be read back",
            ) from error
        if readback is None or readback.content != rendered:
            raise PlanControlError(
                "DURABLE_STATE_READBACK_INVALID",
                "GitHub PlanControl transition did not read back exactly",
            )
        return result

    def _read_repo(self) -> InMemoryPlanRepository:
        return self._read()[0]

    def active_receipt(self, handle: CampaignHandle) -> ActivationReceipt | None:
        self._assert_repository(handle.repository)
        return self._read_repo().active_receipt(handle)

    def read_attempt(
        self,
        handle: CampaignHandle,
        expected_previous_revision_digest: str | None,
    ) -> _PlanningAttempt | None:
        self._assert_repository(handle.repository)
        return self._read_repo().read_attempt(
            handle,
            expected_previous_revision_digest,
        )

    def save_attempt(self, attempt: _PlanningAttempt) -> _PlanningAttempt:
        self._assert_repository(attempt.handle.repository)
        return self._mutate(
            "save Planning attempt",
            lambda repo: repo.save_attempt(attempt),
        )

    def read_split_decision(
        self,
        handle: CampaignHandle,
        expected_previous_revision_digest: str | None,
    ) -> _SplitCampaignDecisionRecord | None:
        self._assert_repository(handle.repository)
        return self._read_repo().read_split_decision(
            handle,
            expected_previous_revision_digest,
        )

    def save_split_decision(
        self,
        decision: _SplitCampaignDecisionRecord,
    ) -> _SplitCampaignDecisionRecord:
        self._assert_repository(decision.handle.repository)
        return self._mutate(
            "save split-Campaign Decision",
            lambda repo: repo.save_split_decision(decision),
        )

    def read_runtime_assertion(
        self,
        handle: CampaignHandle,
    ) -> Mapping[str, Any] | None:
        self._assert_repository(handle.repository)
        return self._read_repo().read_runtime_assertion(handle)

    def save_runtime_assertion(
        self,
        handle: CampaignHandle,
        assertion: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        self._assert_repository(handle.repository)
        return self._mutate(
            "bind Runtime assertion",
            lambda repo: repo.save_runtime_assertion(handle, assertion),
        )

    def reserve_planning(self, reservation: PlanningReservation) -> None:
        self._assert_repository(reservation.repository)
        self._mutate(
            "reserve Planning",
            lambda repo: repo.reserve_planning(reservation),
        )

    def release_planning(self, reservation: PlanningReservation) -> None:
        self._assert_repository(reservation.repository)
        self._mutate(
            "release Planning",
            lambda repo: repo.release_planning(reservation),
        )

    def reserve_claims(self, receipt: ActivationReceipt) -> None:
        self._assert_repository(receipt.repository)
        self._mutate(
            "reserve activation claims",
            lambda repo: repo.reserve_claims(receipt),
        )

    def publish_revision(self, revision: PlanRevision) -> None:
        self._assert_repository(revision.repository)
        self._mutate(
            "publish Plan Revision",
            lambda repo: repo.publish_revision(revision),
        )

    def read_revision(self, digest: str) -> PlanRevision | None:
        return self._read_repo().read_revision(digest)

    def activate(self, receipt: ActivationReceipt) -> None:
        self._assert_repository(receipt.repository)
        self._mutate(
            "activate Plan Revision",
            lambda repo: repo.activate(receipt),
        )

    def finalize_claims(self, receipt: ActivationReceipt) -> None:
        self._assert_repository(receipt.repository)
        self._mutate(
            "finalize Ticket claims",
            lambda repo: repo.finalize_claims(receipt),
        )

    def read_activation(
        self,
        handle: CampaignHandle,
    ) -> ActivationReceipt | None:
        self._assert_repository(handle.repository)
        return self._read_repo().read_activation(handle)

    def read_claim_proofs(
        self,
        handle: CampaignHandle,
        revision_digest: str,
    ) -> tuple[TicketClaimProof, ...]:
        self._assert_repository(handle.repository)
        return self._read_repo().read_claim_proofs(handle, revision_digest)
