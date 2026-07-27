"""V3-only Campaign start control.

PlanControl persists a canonical preplanning snapshot, reserves Ticket claims
through one repository-global compare-and-swap record, invokes one semantic
Planning Pass, compiles provider-neutral PlanSpec v3 bytes, and activates only
after durable publication and receipt readback.

The module has no dependency on the V2 compiler, decoder, tables, or writer.
Production composition is lazy and private so importing the package performs
no I/O.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
import re
from typing import Any, Callable, Mapping, Protocol, Sequence

from ._v3_canonical import (
    deep_immutable as _deep_immutable,
    digest as _digest,
    strict_json_bytes as _strict_json_bytes,
    strict_json_decode as _strict_json_decode,
)
from ._v3_composition import (
    install_production_factory as _install_production_factory,
    production_control as _production_control,
)
from ._v3_github_control import (
    ClaimConflict as _ClaimConflict,
    ContentClient as _ContentClient,
    GitHubV3Control as _GitHubV3Control,
    WriterAuthority as _WriterAuthority,
)
from ._v3_journal import SQLiteV3Journal as _SQLiteV3Journal
from ._v3_plan_spec import (
    _compile_plan,
    _normalize_intent,
    _normalize_snapshot,
    _ready_refs,
    _require_string,
    _validate_options_for_tickets,
    _validate_plan_spec,
    _versioned_identifier,
)
from ._v3_types import (
    AUTO_PREVIOUS as _AUTO_PREVIOUS,
    DIGEST_PATTERN as _DIGEST,
    STATE_ACTIVATION_COMMITTED as _STATE_ACTIVATION_COMMITTED,
    STATE_ACTIVE_LOCAL as _STATE_ACTIVE_LOCAL,
    STATE_CLAIMS_RESERVED as _STATE_CLAIMS_RESERVED,
    STATE_DECISION_REQUIRED as _STATE_DECISION_REQUIRED,
    STATE_INTENT_ACCEPTED as _STATE_INTENT_ACCEPTED,
    STATE_PLAN_PUBLISHED as _STATE_PLAN_PUBLISHED,
    STATE_PLANNING_AMBIGUOUS as _STATE_PLANNING_AMBIGUOUS,
    STATE_PLANNING_STARTED as _STATE_PLANNING_STARTED,
    STATE_SNAPSHOTTED as _STATE_SNAPSHOTTED,
    ActivationReceipt as _ActivationReceipt,
    ActiveCampaign as _ActiveCampaign,
    CampaignHandle,
    Content as _Content,
    DecisionFinding,
    JournalRecord as _JournalRecord,
    PlanControlDecision,
    PlanControlError,
    PlanRevision as _PlanRevision,
    WriterWitness as _WriterWitness,
)

_TICKET_ROLES = frozenset(
    {"worker", "recovery_worker", "review_primary", "review_strong"}
)
_OPAQUE_PROFILE_REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}$")


@dataclass(frozen=True)
class CampaignStartOptions:
    """Provider-neutral Runtime Profile references persisted outside PlanSpec."""

    coordinator: str | None = None
    runtime_profile_overrides: tuple[tuple[str, str, str], ...] = ()

    @classmethod
    def from_value(
        cls, value: CampaignStartOptions | Mapping[str, Any] | None
    ) -> CampaignStartOptions:
        if value is None:
            return cls()
        if isinstance(value, cls):
            coordinator = value.coordinator
            raw = value.runtime_profile_overrides
        elif isinstance(value, Mapping):
            if not set(value).issubset(
                {"coordinator", "runtime_profile_overrides"}
            ):
                raise PlanControlError(
                    "START_OPTIONS_INVALID",
                    "only coordinator and runtime_profile_overrides are allowed",
                )
            coordinator = value.get("coordinator")
            raw = value.get("runtime_profile_overrides", ())
        else:
            raise PlanControlError(
                "START_OPTIONS_INVALID", "start options must be an object"
            )
        if coordinator is not None and (
            not isinstance(coordinator, str)
            or not _OPAQUE_PROFILE_REF.fullmatch(coordinator)
        ):
            raise PlanControlError(
                "START_OPTIONS_INVALID",
                "Coordinator Runtime Profile ref is invalid",
            )
        if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
            raise PlanControlError(
                "START_OPTIONS_INVALID",
                "runtime_profile_overrides must be a sequence",
            )
        normalized: list[tuple[str, str, str]] = []
        for item in raw:
            if isinstance(item, Mapping):
                if set(item) != {"ticket_key", "role", "profile_ref"}:
                    raise PlanControlError(
                        "START_OPTIONS_INVALID",
                        "each Runtime override has unsupported fields",
                    )
                item = (
                    item["ticket_key"],
                    item["role"],
                    item["profile_ref"],
                )
            if not isinstance(item, Sequence) or isinstance(item, (str, bytes)):
                raise PlanControlError(
                    "START_OPTIONS_INVALID",
                    "each Runtime override must be (ticket_key, role, profile_ref)",
                )
            triple = tuple(item)
            if len(triple) != 3 or any(
                not isinstance(part, str) or not part for part in triple
            ):
                raise PlanControlError(
                    "START_OPTIONS_INVALID",
                    "each Runtime override must contain three non-empty strings",
                )
            ticket_key, role, profile_ref = triple
            if role not in _TICKET_ROLES and not (
                role.startswith("specialist:")
                and _versioned_identifier(role.removeprefix("specialist:"))
            ):
                raise PlanControlError(
                    "START_OPTIONS_INVALID",
                    f"unsupported Ticket Runtime role: {role}",
                )
            if not _OPAQUE_PROFILE_REF.fullmatch(profile_ref):
                raise PlanControlError(
                    "START_OPTIONS_INVALID", "Runtime Profile ref is invalid"
                )
            normalized.append((ticket_key, role, profile_ref))
        if len({(item[0], item[1]) for item in normalized}) != len(normalized):
            raise PlanControlError(
                "START_OPTIONS_INVALID",
                "Runtime overrides repeat an exact Ticket and role",
            )
        return cls(
            coordinator=coordinator,
            runtime_profile_overrides=tuple(sorted(normalized)),
        )

    def as_value(self) -> dict[str, Any]:
        return {
            "coordinator": self.coordinator,
            "runtime_profile_overrides": [
                {
                    "ticket_key": ticket_key,
                    "role": role,
                    "profile_ref": profile_ref,
                }
                for ticket_key, role, profile_ref in sorted(
                    self.runtime_profile_overrides
                )
            ]
        }


class _CampaignSource(Protocol):
    def snapshot(
        self, repository: str, ready_refs: tuple[str, ...]
    ) -> Mapping[str, Any]: ...


class _PlanningPass(Protocol):
    def plan(
        self,
        snapshot: object,
        planning_action_id: str,
        *,
        coordinator_profile_ref: str | None,
    ) -> Mapping[str, Any]: ...


def _decision_bytes(
    *,
    repository: str,
    campaign_key: str,
    snapshot_digest: str,
    planning_action_id: str,
    findings: tuple[DecisionFinding, ...],
) -> bytes:
    return _strict_json_bytes(
        {
            "schema_version": 1,
            "repository": repository,
            "campaign_key": campaign_key,
            "snapshot_digest": snapshot_digest,
            "planning_action_id": planning_action_id,
            "findings": [finding.as_value() for finding in sorted(findings)],
        }
    )


def _decision_from_bytes(value: bytes) -> PlanControlDecision:
    payload = _strict_json_decode(value)
    expected = {
        "schema_version",
        "repository",
        "campaign_key",
        "snapshot_digest",
        "planning_action_id",
        "findings",
    }
    if (
        not isinstance(payload, dict)
        or set(payload) != expected
        or payload["schema_version"] != 1
        or not isinstance(payload["repository"], str)
        or not payload["repository"]
        or not isinstance(payload["campaign_key"], str)
        or not payload["campaign_key"]
        or not isinstance(payload["snapshot_digest"], str)
        or not _DIGEST.fullmatch(payload["snapshot_digest"])
        or not isinstance(payload["planning_action_id"], str)
        or not payload["planning_action_id"].startswith("planning:")
        or not isinstance(payload["findings"], list)
        or not payload["findings"]
    ):
        raise PlanControlError(
            "DECISION_READBACK_INVALID", "Decision record is malformed"
        )
    findings_list: list[DecisionFinding] = []
    for item in payload["findings"]:
        if (
            not isinstance(item, dict)
            or set(item) != {"code", "detail", "ticket_key"}
            or not isinstance(item["code"], str)
            or not item["code"]
            or not isinstance(item["detail"], str)
            or not item["detail"]
            or (
                item["ticket_key"] is not None
                and (
                    not isinstance(item["ticket_key"], str)
                    or not item["ticket_key"]
                )
            )
        ):
            raise PlanControlError(
                "DECISION_READBACK_INVALID",
                "Decision finding is malformed",
            )
        findings_list.append(
            DecisionFinding(
                code=item["code"],
                detail=item["detail"],
                ticket_key=item["ticket_key"],
            )
        )
    findings = tuple(sorted(findings_list))
    if tuple(findings_list) != findings or len(set(findings)) != len(findings):
        raise PlanControlError(
            "DECISION_READBACK_INVALID",
            "Decision findings are not canonical",
        )
    return PlanControlDecision(
        repository=payload["repository"],
        campaign_key=payload["campaign_key"],
        snapshot_digest=payload["snapshot_digest"],
        planning_action_id=payload["planning_action_id"],
        findings=findings,
        decision_digest=_digest(value),
    )


class _PlanControl:
    """The private PlanControl deep module."""

    def __init__(
        self,
        *,
        source: _CampaignSource,
        planner: _PlanningPass,
        journal: _SQLiteV3Journal,
        durable: _GitHubV3Control,
        writer: _WriterAuthority,
        max_snapshot_bytes: int = 1_000_000,
        checkpoint: Callable[[str], None] | None = None,
    ):
        if not isinstance(journal, _SQLiteV3Journal) or not isinstance(
            durable, _GitHubV3Control
        ):
            raise PlanControlError(
                "PLAN_CONTROL_COMPOSITION_INVALID",
                "PlanControl requires the independent V3 journal and GitHub CAS",
            )
        if max_snapshot_bytes < 1:
            raise PlanControlError(
                "PLAN_CONTROL_COMPOSITION_INVALID",
                "max_snapshot_bytes must be positive",
            )
        self.source = source
        self.planner = planner
        self.journal = journal
        self.durable = durable
        self.writer = writer
        self.max_snapshot_bytes = max_snapshot_bytes
        self.checkpoint = checkpoint

    def start(
        self,
        repository: str,
        ready_refs: Sequence[str],
        options: CampaignStartOptions | Mapping[str, Any] | None = None,
        *,
        _campaign_key: str | None = None,
        _expected_previous_revision_digest: str | None | object = _AUTO_PREVIOUS,
    ) -> CampaignHandle:
        repository = _require_string(repository, "repository")
        refs = _ready_refs(ready_refs)
        option_value = CampaignStartOptions.from_value(options)
        _validate_options_for_tickets(option_value, set(refs))
        campaign_key = _campaign_key or (
            "campaign:"
            + _digest(
                _strict_json_bytes(
                    {"repository": repository, "ready_refs": list(refs)}
                )
            )[:24]
        )
        handle = CampaignHandle(repository, campaign_key)
        if _expected_previous_revision_digest is _AUTO_PREVIOUS:
            campaign = self.durable.resume_campaign(handle, refs)
            if campaign is not None:
                record = self._restore_from_durable(
                    handle=handle,
                    ticket_keys=refs,
                    campaign=campaign,
                    requested_options=(
                        option_value if options is not None else None
                    ),
                )
                if record.state in {
                    _STATE_DECISION_REQUIRED,
                    _STATE_PLANNING_AMBIGUOUS,
                }:
                    self._raise_persisted_decision(record)
                return self._continue(record, campaign, handle)
        raw_snapshot = self.source.snapshot(repository, refs)
        # Canonicalize before any semantic call or durable claim.
        snapshot = _normalize_snapshot(raw_snapshot, repository, refs)
        snapshot_bytes = _strict_json_bytes(snapshot)
        snapshot_digest = _digest(snapshot_bytes)
        options_bytes = _strict_json_bytes(option_value.as_value())
        options_digest = _digest(options_bytes)
        expected_previous = (
            self.durable.active_digest(handle)
            if _expected_previous_revision_digest is _AUTO_PREVIOUS
            else _expected_previous_revision_digest
        )
        planning_action_id = (
            "planning:"
            + _digest(
                _strict_json_bytes(
                    {
                        "repository": repository,
                        "campaign_key": campaign_key,
                        "snapshot_digest": snapshot_digest,
                    }
                )
            )
        )
        record = _JournalRecord(
            repository=repository,
            campaign_key=campaign_key,
            snapshot_digest=snapshot_digest,
            state=_STATE_SNAPSHOTTED,
            snapshot_bytes=snapshot_bytes,
            options_bytes=options_bytes,
            options_digest=options_digest,
            planning_action_id=planning_action_id,
            expected_previous_revision_digest=expected_previous,
        )
        existing = self.journal.read(repository, campaign_key, snapshot_digest)
        record = self.journal.save(existing or record)
        if record.state in {
            _STATE_DECISION_REQUIRED,
            _STATE_PLANNING_AMBIGUOUS,
        }:
            self._raise_persisted_decision(record)
        self._checkpoint(_STATE_SNAPSHOTTED)
        self.durable.publish_artifact(repository, "snapshots", snapshot_bytes)
        runtime_facts_digest = self.durable.publish_artifact(
            repository, "runtime-facts", options_bytes
        )
        if runtime_facts_digest != options_digest:
            raise PlanControlError(
                "RUNTIME_FACTS_DIGEST_MISMATCH",
                "durable Runtime facts differ from the validated start options",
            )
        if len(snapshot_bytes) > self.max_snapshot_bytes:
            self._raise_decision(
                record,
                (
                    DecisionFinding(
                        code="SPLIT_CAMPAIGN_REQUIRED",
                        detail=(
                            f"snapshot has {len(snapshot_bytes)} bytes; "
                            f"limit is {self.max_snapshot_bytes}"
                        ),
                    ),
                ),
                durable_state=False,
            )
        witness = self._writer_witness(repository)
        try:
            campaign = self.durable.reserve_claims(
                handle=handle,
                snapshot_digest=snapshot_digest,
                runtime_facts_digest=runtime_facts_digest,
                planning_action_id=planning_action_id,
                expected_previous_revision_digest=expected_previous,
                ticket_keys=refs,
                witness=witness,
            )
        except _ClaimConflict as conflict:
            self._raise_decision(record, conflict.findings, durable_state=False)
        durable_state = campaign.get("state")
        if not isinstance(durable_state, str):
            raise PlanControlError(
                "DURABLE_CONTROL_INVALID",
                "reserved Campaign has no state",
            )
        record = self.journal.save(
            replace(
                record,
                state=(
                    record.state
                    if record.state == _STATE_ACTIVE_LOCAL
                    else durable_state
                ),
                writer_generation=campaign.get("writer_generation"),
                writer_witness_digest=campaign.get(
                    "writer_witness_digest"
                ),
            )
        )
        if durable_state == _STATE_CLAIMS_RESERVED:
            self._checkpoint(_STATE_CLAIMS_RESERVED)
        return self._continue(record, campaign, handle)

    def _restore_from_durable(
        self,
        *,
        handle: CampaignHandle,
        ticket_keys: tuple[str, ...],
        campaign: dict[str, Any],
        requested_options: CampaignStartOptions | None,
    ) -> _JournalRecord:
        snapshot_digest = campaign["snapshot_digest"]
        snapshot_bytes = self.durable.read_artifact(
            handle.repository, "snapshots", snapshot_digest
        )
        snapshot_value = _strict_json_decode(snapshot_bytes)
        if (
            not isinstance(snapshot_value, dict)
            or snapshot_value.get("schema_version") != 1
        ):
            raise PlanControlError(
                "SNAPSHOT_READBACK_MISMATCH",
                "durable Campaign snapshot schema is invalid",
            )
        raw_snapshot = {
            key: value
            for key, value in snapshot_value.items()
            if key != "schema_version"
        }
        snapshot = _normalize_snapshot(
            raw_snapshot, handle.repository, ticket_keys
        )
        if (
            _strict_json_bytes(snapshot) != snapshot_bytes
            or _digest(snapshot_bytes) != snapshot_digest
        ):
            raise PlanControlError(
                "SNAPSHOT_READBACK_MISMATCH",
                "durable Campaign snapshot identity changed",
            )

        options_digest = campaign["runtime_facts_digest"]
        options_bytes = self.durable.read_artifact(
            handle.repository, "runtime-facts", options_digest
        )
        persisted_options = CampaignStartOptions.from_value(
            _strict_json_decode(options_bytes)
        )
        if _strict_json_bytes(persisted_options.as_value()) != options_bytes:
            raise PlanControlError(
                "RUNTIME_FACTS_DIGEST_MISMATCH",
                "durable Campaign Runtime facts are not canonical",
            )
        if (
            requested_options is not None
            and requested_options != persisted_options
        ):
            raise PlanControlError(
                "START_OPTIONS_CONFLICT",
                "Campaign Runtime overrides differ from durable start facts",
            )

        intent_bytes = None
        intent_digest = campaign.get("intent_digest")
        if isinstance(intent_digest, str):
            intent_bytes = self.durable.read_artifact(
                handle.repository, "intents", intent_digest
            )
            intent = _normalize_intent(
                _strict_json_decode(intent_bytes), snapshot
            )
            if _strict_json_bytes(intent) != intent_bytes:
                raise PlanControlError(
                    "PLAN_INTENT_READBACK_MISMATCH",
                    "durable Plan Intent is not the validated canonical value",
                )

        decision_bytes = None
        decision_digest = campaign.get("decision_digest")
        if isinstance(decision_digest, str):
            decision_bytes = self.durable.read_artifact(
                handle.repository, "decisions", decision_digest
            )
            _decision_from_bytes(decision_bytes)

        plan_bytes = None
        plan_digest = campaign.get("plan_digest")
        if isinstance(plan_digest, str):
            plan_bytes = self.durable.read_artifact(
                handle.repository, "plans", plan_digest
            )
            _validate_plan_spec(plan_bytes)

        receipt_bytes = None
        receipt_digest = campaign.get("receipt_digest")
        if isinstance(receipt_digest, str):
            receipt_bytes = self.durable.read_artifact(
                handle.repository, "receipts", receipt_digest
            )
            self._receipt_from_bytes(receipt_bytes)

        restored = _JournalRecord(
            repository=handle.repository,
            campaign_key=handle.campaign_key,
            snapshot_digest=snapshot_digest,
            state=campaign["state"],
            snapshot_bytes=snapshot_bytes,
            options_bytes=options_bytes,
            options_digest=options_digest,
            planning_action_id=campaign["planning_action_id"],
            expected_previous_revision_digest=campaign[
                "expected_previous_revision_digest"
            ],
            writer_generation=campaign["writer_generation"],
            writer_witness_digest=campaign["writer_witness_digest"],
            intent_bytes=intent_bytes,
            intent_digest=intent_digest,
            decision_bytes=decision_bytes,
            decision_digest=decision_digest,
            plan_bytes=plan_bytes,
            plan_digest=plan_digest,
            receipt_bytes=receipt_bytes,
            receipt_digest=receipt_digest,
        )
        existing = self.journal.read(
            handle.repository, handle.campaign_key, snapshot_digest
        )
        if existing is not None and (
            existing.snapshot_bytes != restored.snapshot_bytes
            or existing.options_bytes != restored.options_bytes
            or existing.options_digest != restored.options_digest
            or existing.planning_action_id != restored.planning_action_id
            or existing.expected_previous_revision_digest
            != restored.expected_previous_revision_digest
        ):
            raise PlanControlError(
                "JOURNAL_IDENTITY_CONFLICT",
                "local Campaign identity differs from durable artifacts",
            )
        if existing is not None and existing.state in {
            _STATE_ACTIVE_LOCAL,
            _STATE_DECISION_REQUIRED,
            _STATE_PLANNING_AMBIGUOUS,
        }:
            return existing
        return self.journal.save(restored)

    def _continue(
        self,
        record: _JournalRecord,
        campaign: dict[str, Any],
        handle: CampaignHandle,
    ) -> CampaignHandle:
        state = campaign["state"]
        if state in {_STATE_DECISION_REQUIRED, _STATE_PLANNING_AMBIGUOUS}:
            decision_digest = campaign.get("decision_digest")
            if not isinstance(decision_digest, str):
                raise PlanControlError(
                    "DECISION_READBACK_INVALID",
                    "durable Decision has no digest",
                )
            decision_bytes = self.durable.read_artifact(
                handle.repository, "decisions", decision_digest
            )
            decision = _decision_from_bytes(decision_bytes)
            self._validate_decision_identity(decision, record)
            raise decision
        if state == _STATE_PLANNING_STARTED:
            self._raise_decision(
                record,
                (
                    DecisionFinding(
                        code="PLANNING_AMBIGUOUS",
                        detail=(
                            "Planning Pass may already have run; "
                            "a second invocation is forbidden"
                        ),
                    ),
                ),
                ambiguous=True,
            )
        if state == _STATE_CLAIMS_RESERVED:
            if not self.durable.begin_planning(handle):
                refreshed = self.durable.campaign(handle)
                if refreshed is None:
                    raise PlanControlError(
                        "CAMPAIGN_RESERVATION_MISSING",
                        "Campaign disappeared before planning",
                    )
                return self._continue(record, refreshed, handle)
            record = self.journal.save(
                replace(record, state=_STATE_PLANNING_STARTED)
            )
            self._checkpoint(_STATE_PLANNING_STARTED)
            snapshot_bytes = self._persisted_snapshot(record)
            planner_view = _deep_immutable(_strict_json_decode(snapshot_bytes))
            try:
                raw_intent = self.planner.plan(
                    planner_view,
                    record.planning_action_id,
                    coordinator_profile_ref=CampaignStartOptions.from_value(
                        _strict_json_decode(record.options_bytes)
                    ).coordinator,
                )
                # Copy the returned object immediately and reject non-JSON.
                returned_bytes = _strict_json_bytes(raw_intent)
            except Exception as error:
                self._raise_decision(
                    record,
                    (
                        DecisionFinding(
                            code="PLANNING_AMBIGUOUS",
                            detail=f"Planning reply was not durably accepted: {error}",
                        ),
                    ),
                    ambiguous=True,
                )
            self._checkpoint("PLANNING_REPLY_RECEIVED")
            # The semantic call is an untrusted boundary. Re-read the local and
            # durable preplanning snapshot before accepting any returned intent.
            snapshot_bytes = self._persisted_snapshot(record)
            snapshot = _strict_json_decode(snapshot_bytes)
            try:
                intent = _normalize_intent(
                    _strict_json_decode(returned_bytes), snapshot
                )
            except PlanControlError as error:
                self._raise_decision(
                    record,
                    (
                        DecisionFinding(
                            code=error.code,
                            detail=error.detail,
                        ),
                    ),
                )
            except Exception:
                self._raise_decision(
                    record,
                    (
                        DecisionFinding(
                            code="PLAN_INTENT_INVALID",
                            detail=(
                                "Planning reply could not be validated as "
                                "provider-neutral semantic intent"
                            ),
                        ),
                    ),
                )
            intent_bytes = _strict_json_bytes(intent)
            if intent["decision_requirements"]:
                findings = tuple(
                    DecisionFinding(
                        code=item["code"],
                        detail=item["detail"],
                        ticket_key=item["ticket_key"],
                    )
                    for item in intent["decision_requirements"]
                )
                self._raise_decision(record, findings)
            intent_digest = self.durable.publish_artifact(
                handle.repository, "intents", intent_bytes
            )
            campaign = self.durable.accept_intent(handle, intent_digest)
            record = self.journal.save(
                replace(
                    record,
                    state=_STATE_INTENT_ACCEPTED,
                    intent_bytes=intent_bytes,
                    intent_digest=intent_digest,
                )
            )
            self._checkpoint(_STATE_INTENT_ACCEPTED)
            state = _STATE_INTENT_ACCEPTED
        if state == _STATE_INTENT_ACCEPTED:
            intent_digest = campaign.get("intent_digest")
            if record.intent_bytes is None:
                if not isinstance(intent_digest, str):
                    raise PlanControlError(
                        "PLAN_INTENT_READBACK_MISMATCH",
                        "durable Campaign has no Plan Intent digest",
                    )
                intent_bytes = self.durable.read_artifact(
                    handle.repository, "intents", intent_digest
                )
                record = self.journal.save(
                    replace(
                        record,
                        intent_bytes=intent_bytes,
                        intent_digest=intent_digest,
                    )
                )
            revision = self._compile_from_journal(record, handle)
            plan_digest = self.durable.publish_artifact(
                handle.repository, "plans", revision.canonical_bytes
            )
            if plan_digest != revision.digest:
                raise PlanControlError(
                    "PLAN_DIGEST_MISMATCH",
                    "published PlanSpec digest differs from compilation",
                )
            self._checkpoint("PLAN_ARTIFACT_PUBLISHED")
            readback = self.durable.read_artifact(
                handle.repository, "plans", revision.digest
            )
            if readback != revision.canonical_bytes:
                raise PlanControlError(
                    "PLAN_READBACK_MISMATCH",
                    "published PlanSpec bytes differ",
                )
            self._checkpoint("PLAN_READ_BACK")
            campaign = self.durable.mark_plan_published(
                handle, revision.digest
            )
            record = self.journal.save(
                replace(
                    record,
                    state=_STATE_PLAN_PUBLISHED,
                    plan_bytes=revision.canonical_bytes,
                    plan_digest=revision.digest,
                )
            )
            self._checkpoint(_STATE_PLAN_PUBLISHED)
            state = _STATE_PLAN_PUBLISHED
        if state == _STATE_PLAN_PUBLISHED:
            revision = self._revision_from_record(record, handle)
            witness = self._writer_witness(handle.repository)
            if (
                witness.writer_generation != record.writer_generation
                or witness.digest != record.writer_witness_digest
            ):
                self._raise_decision(
                    record,
                    (
                        DecisionFinding(
                            code="WRITER_WITNESS_CHANGED",
                            detail=(
                                "writer authority changed after claim reservation"
                            ),
                        ),
                    ),
                )
            receipt_bytes = self.durable.activate(
                handle=handle, revision=revision, witness=witness
            )
            self._checkpoint(_STATE_ACTIVATION_COMMITTED)
            campaign = self.durable.campaign(handle)
            if campaign is None:
                raise PlanControlError(
                    "ACTIVATION_RECEIPT_INVALID",
                    "durable activation record disappeared",
                )
            receipt = self._receipt_from_bytes(receipt_bytes)
            self._validate_receipt_identity(
                receipt,
                handle=handle,
                revision=revision,
                campaign=campaign,
            )
            self._checkpoint("ACTIVATION_RECEIPT_READ_BACK")
            self.journal.finalize(record, revision, receipt_bytes)
            return handle
        if state == _STATE_ACTIVATION_COMMITTED:
            revision = self._revision_from_record(record, handle)
            receipt_digest = campaign.get("receipt_digest")
            if not isinstance(receipt_digest, str):
                raise PlanControlError(
                    "ACTIVATION_RECEIPT_INVALID",
                    "committed Campaign has no receipt digest",
                )
            receipt_bytes = self.durable.read_artifact(
                handle.repository, "receipts", receipt_digest
            )
            receipt = self._receipt_from_bytes(receipt_bytes)
            self._validate_receipt_identity(
                receipt,
                handle=handle,
                revision=revision,
                campaign=campaign,
            )
            self.journal.finalize(record, revision, receipt_bytes)
            return handle
        raise PlanControlError(
            "CAMPAIGN_STATE_INVALID", f"unsupported Campaign state {state}"
        )

    def _persisted_snapshot(self, record: _JournalRecord) -> bytes:
        local = self.journal.read(
            record.repository, record.campaign_key, record.snapshot_digest
        )
        if local is None or _digest(local.snapshot_bytes) != record.snapshot_digest:
            raise PlanControlError(
                "SNAPSHOT_DIGEST_MISMATCH",
                "local preplanning snapshot did not read back exactly",
            )
        durable = self.durable.read_artifact(
            record.repository, "snapshots", record.snapshot_digest
        )
        if durable != local.snapshot_bytes:
            raise PlanControlError(
                "SNAPSHOT_READBACK_MISMATCH",
                "durable and local preplanning snapshots differ",
            )
        return local.snapshot_bytes

    def _compile_from_journal(
        self, record: _JournalRecord, handle: CampaignHandle
    ) -> _PlanRevision:
        current = self.journal.read(
            record.repository, record.campaign_key, record.snapshot_digest
        )
        if (
            current is None
            or current.intent_bytes is None
            or current.intent_digest is None
        ):
            raise PlanControlError(
                "PLAN_INTENT_READBACK_MISMATCH",
                "validated Plan Intent is missing from the V3 journal",
            )
        snapshot_bytes = self._persisted_snapshot(current)
        durable_intent = self.durable.read_artifact(
            record.repository, "intents", current.intent_digest
        )
        if durable_intent != current.intent_bytes:
            raise PlanControlError(
                "PLAN_INTENT_READBACK_MISMATCH",
                "durable and local Plan Intent bytes differ",
            )
        revision = _compile_plan(
            snapshot_bytes=snapshot_bytes,
            snapshot_digest=current.snapshot_digest,
            intent_bytes=current.intent_bytes,
            intent_digest=current.intent_digest,
            handle=handle,
        )
        _validate_plan_spec(revision.canonical_bytes)
        if _digest(revision.canonical_bytes) != revision.digest:
            raise PlanControlError(
                "PLAN_DIGEST_MISMATCH", "PlanSpec authority root changed"
            )
        return revision

    def _revision_from_record(
        self, record: _JournalRecord, handle: CampaignHandle
    ) -> _PlanRevision:
        current = self.journal.read(
            record.repository, record.campaign_key, record.snapshot_digest
        )
        if current is None or current.plan_digest is None:
            campaign = self.durable.campaign(handle)
            if campaign is None or not isinstance(
                campaign.get("plan_digest"), str
            ):
                raise PlanControlError(
                    "PLAN_READBACK_MISMATCH",
                    "published Plan identity is missing",
                )
            plan_digest = campaign["plan_digest"]
            plan_bytes = self.durable.read_artifact(
                handle.repository, "plans", plan_digest
            )
            current = self.journal.save(
                replace(
                    record,
                    state=record.state,
                    plan_bytes=plan_bytes,
                    plan_digest=plan_digest,
                )
            )
        if current.plan_bytes is None or current.plan_digest is None:
            raise PlanControlError(
                "PLAN_READBACK_MISMATCH", "published Plan bytes are missing"
            )
        _validate_plan_spec(current.plan_bytes)
        if _digest(current.plan_bytes) != current.plan_digest:
            raise PlanControlError(
                "PLAN_DIGEST_MISMATCH", "published Plan digest changed"
            )
        return _PlanRevision(
            repository=handle.repository,
            campaign_key=handle.campaign_key,
            snapshot_digest=record.snapshot_digest,
            canonical_bytes=current.plan_bytes,
            digest=current.plan_digest,
        )

    def _receipt_from_bytes(self, value: bytes) -> _ActivationReceipt:
        payload = _strict_json_decode(value)
        expected = {
            "schema_version",
            "repository",
            "campaign_key",
            "revision_digest",
            "expected_previous_revision_digest",
            "writer_generation",
            "writer_witness_digest",
            "snapshot_digest",
            "planning_action_id",
            "ticket_keys",
        }
        if (
            not isinstance(payload, dict)
            or set(payload) != expected
            or payload["schema_version"] != 3
            or not isinstance(payload["repository"], str)
            or not payload["repository"]
            or not isinstance(payload["campaign_key"], str)
            or not payload["campaign_key"]
            or not isinstance(payload["writer_generation"], str)
            or not payload["writer_generation"]
            or not isinstance(payload["planning_action_id"], str)
            or not payload["planning_action_id"]
            or not isinstance(payload["ticket_keys"], list)
            or any(
                not isinstance(item, str) or not item
                for item in payload["ticket_keys"]
            )
            or len(set(payload["ticket_keys"])) != len(payload["ticket_keys"])
            or payload["ticket_keys"] != sorted(payload["ticket_keys"])
            or any(
                not isinstance(payload[field], str)
                or not _DIGEST.fullmatch(payload[field])
                for field in (
                    "revision_digest",
                    "writer_witness_digest",
                    "snapshot_digest",
                )
            )
            or (
                payload["expected_previous_revision_digest"] is not None
                and (
                    not isinstance(
                        payload["expected_previous_revision_digest"], str
                    )
                    or not _DIGEST.fullmatch(
                        payload["expected_previous_revision_digest"]
                    )
                )
            )
        ):
            raise PlanControlError(
                "ACTIVATION_RECEIPT_INVALID",
                "Activation Receipt schema is invalid",
            )
        return _ActivationReceipt(
            repository=payload["repository"],
            campaign_key=payload["campaign_key"],
            revision_digest=payload["revision_digest"],
            expected_previous_revision_digest=payload[
                "expected_previous_revision_digest"
            ],
            writer_generation=payload["writer_generation"],
            writer_witness_digest=payload["writer_witness_digest"],
            snapshot_digest=payload["snapshot_digest"],
            planning_action_id=payload["planning_action_id"],
            ticket_keys=tuple(payload["ticket_keys"]),
        )

    def _validate_receipt_identity(
        self,
        receipt: _ActivationReceipt,
        *,
        handle: CampaignHandle,
        revision: _PlanRevision,
        campaign: Mapping[str, Any],
    ) -> None:
        if (
            receipt.repository != handle.repository
            or receipt.campaign_key != handle.campaign_key
            or receipt.revision_digest != revision.digest
            or receipt.snapshot_digest != revision.snapshot_digest
            or receipt.expected_previous_revision_digest
            != campaign.get("expected_previous_revision_digest")
            or receipt.writer_generation != campaign.get("writer_generation")
            or receipt.writer_witness_digest
            != campaign.get("writer_witness_digest")
            or receipt.planning_action_id
            != campaign.get("planning_action_id")
            or list(receipt.ticket_keys) != campaign.get("ticket_keys")
            or campaign.get("state") != _STATE_ACTIVATION_COMMITTED
            or campaign.get("receipt_digest")
            != _digest(_strict_json_bytes(receipt.as_value()))
        ):
            raise PlanControlError(
                "ACTIVATION_RECEIPT_INVALID",
                "Activation Receipt identities do not match durable control",
            )

    def _writer_witness(self, repository: str) -> _WriterWitness:
        witness = self.writer.read(repository)
        if (
            witness.repository != repository
            or not witness.writer_generation
            or not _DIGEST.fullmatch(witness.digest)
            or not witness.v8_start_allowed
        ):
            raise PlanControlError(
                "WRITER_AUTHORITY_NOT_READY",
                "writer authority does not allow V8 Campaign start",
            )
        return witness

    def _raise_decision(
        self,
        record: _JournalRecord,
        findings: tuple[DecisionFinding, ...],
        *,
        ambiguous: bool = False,
        durable_state: bool = True,
    ) -> None:
        ordered = tuple(sorted(findings))
        decision_bytes = _decision_bytes(
            repository=record.repository,
            campaign_key=record.campaign_key,
            snapshot_digest=record.snapshot_digest,
            planning_action_id=record.planning_action_id,
            findings=ordered,
        )
        decision_digest = self.durable.publish_artifact(
            record.repository, "decisions", decision_bytes
        )
        state = (
            _STATE_PLANNING_AMBIGUOUS
            if ambiguous
            else _STATE_DECISION_REQUIRED
        )
        self.journal.save(
            replace(
                record,
                state=state,
                decision_bytes=decision_bytes,
                decision_digest=decision_digest,
            )
        )
        if durable_state:
            self.durable.record_decision(
                CampaignHandle(record.repository, record.campaign_key),
                decision_digest,
                ambiguous=ambiguous,
            )
        readback = self.durable.read_artifact(
            record.repository, "decisions", decision_digest
        )
        if readback != decision_bytes:
            raise PlanControlError(
                "DECISION_READBACK_MISMATCH",
                "Decision findings did not read back exactly",
            )
        decision = _decision_from_bytes(readback)
        self._validate_decision_identity(decision, record)
        raise decision

    def _raise_persisted_decision(self, record: _JournalRecord) -> None:
        if (
            record.decision_bytes is None
            or record.decision_digest is None
            or _digest(record.decision_bytes) != record.decision_digest
        ):
            raise PlanControlError(
                "DECISION_READBACK_MISMATCH",
                "persisted Decision bytes or digest are missing",
            )
        readback = self.durable.read_artifact(
            record.repository, "decisions", record.decision_digest
        )
        if readback != record.decision_bytes:
            raise PlanControlError(
                "DECISION_READBACK_MISMATCH",
                "local and durable Decision bytes differ",
            )
        decision = _decision_from_bytes(readback)
        self._validate_decision_identity(decision, record)
        raise decision

    @staticmethod
    def _validate_decision_identity(
        decision: PlanControlDecision,
        record: _JournalRecord,
    ) -> None:
        if (
            decision.repository != record.repository
            or decision.campaign_key != record.campaign_key
            or decision.snapshot_digest != record.snapshot_digest
            or decision.planning_action_id != record.planning_action_id
        ):
            raise PlanControlError(
                "DECISION_READBACK_INVALID",
                "Decision identities differ from the Campaign snapshot",
            )

    def _read_active(self, handle: CampaignHandle) -> _ActiveCampaign:
        active = self.journal.read_active(handle)
        if active is None:
            raise PlanControlError(
                "CAMPAIGN_NOT_ACTIVE",
                "Campaign has no locally finalized Activation Receipt",
            )
        plan_digest, snapshot_digest, receipt_bytes = active
        plan_bytes = self.durable.read_artifact(
            handle.repository, "plans", plan_digest
        )
        _validate_plan_spec(plan_bytes)
        revision = _PlanRevision(
            repository=handle.repository,
            campaign_key=handle.campaign_key,
            snapshot_digest=snapshot_digest,
            canonical_bytes=plan_bytes,
            digest=plan_digest,
        )
        campaign = self.durable.campaign(handle)
        if campaign is None:
            raise PlanControlError(
                "ACTIVATION_RECEIPT_INVALID",
                "durable active Campaign record is missing",
            )
        receipt = self._receipt_from_bytes(receipt_bytes)
        self._validate_receipt_identity(
            receipt,
            handle=handle,
            revision=revision,
            campaign=campaign,
        )
        return _ActiveCampaign(
            handle=handle,
            revision=revision,
            receipt=receipt,
        )

    def _checkpoint(self, boundary: str) -> None:
        if self.checkpoint is not None:
            self.checkpoint(boundary)

def start(
    repository: str,
    ready_refs: Sequence[str],
    options: CampaignStartOptions | None = None,
) -> CampaignHandle:
    """Start one V3 Campaign through lazy production PlanControl composition."""

    control = _production_control(repository)
    if not isinstance(control, _PlanControl):
        raise PlanControlError(
            "PLAN_CONTROL_NOT_CONFIGURED",
            "a real production V3 PlanControl composition is not installed",
        )
    if options is not None and not isinstance(options, CampaignStartOptions):
        raise PlanControlError(
            "START_OPTIONS_INVALID",
            "public start options must be CampaignStartOptions",
        )
    return control.start(repository, ready_refs, options)
