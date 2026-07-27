"""GitHub-backed V3 immutable artifacts and repository-global control CAS."""

from __future__ import annotations

from typing import Any, Mapping, Protocol

from ._v3_canonical import digest, strict_json_bytes, strict_json_decode
from ._v3_types import (
    ActivationReceipt,
    CampaignHandle,
    Content,
    DecisionFinding,
    DIGEST_PATTERN,
    PlanControlError,
    PlanRevision,
    STATE_ACTIVATION_COMMITTED,
    STATE_CLAIMS_RESERVED,
    STATE_DECISION_REQUIRED,
    STATE_INTENT_ACCEPTED,
    STATE_PLAN_PUBLISHED,
    STATE_PLANNING_AMBIGUOUS,
    STATE_PLANNING_STARTED,
    WriterWitness,
)


class ContentClient(Protocol):
    """Neutral content/CAS transport implemented by the production GitHub host."""

    def read(self, repository: str, branch: str, path: str) -> Content | None: ...

    def compare_and_swap(
        self,
        repository: str,
        branch: str,
        path: str,
        content: bytes,
        *,
        expected_blob_sha: str | None,
        message: str,
    ) -> Content: ...


class WriterAuthority(Protocol):
    """Read-only witness for the already-established writer authority."""

    def read(self, repository: str) -> WriterWitness: ...


class PendingClaimAbandonmentAuthority(Protocol):
    """Future explicit-abandon proof boundary; claim release is not #109."""

    def authorize(
        self,
        *,
        repository: str,
        campaign_key: str,
        snapshot_digest: str,
        planning_action_id: str,
        writer_witness_digest: str,
        proof_digest: str,
    ) -> bool: ...


class CasRetry(RuntimeError):
    pass


class ClaimConflict(RuntimeError):
    def __init__(self, findings: tuple[DecisionFinding, ...]):
        super().__init__("Ticket claims overlap another Campaign")
        self.findings = findings


class GitHubV3Control:
    """One V3 control record is the sole repository-global claim authority."""

    def __init__(
        self,
        client: ContentClient,
        *,
        branch: str = "gwo-control",
        root: str = ".gwo/v3",
        cas_attempts: int = 8,
    ):
        self.client = client
        self.branch = branch
        self.root = root.rstrip("/")
        self.cas_attempts = cas_attempts

    def _repository_path(self, repository: str) -> str:
        return f"{self.root}/repositories/{digest(repository.encode())}.json"

    def _artifact_path(self, kind: str, content_digest: str) -> str:
        return f"{self.root}/{kind}/{content_digest}.json"

    def _empty_control(self, repository: str) -> dict[str, Any]:
        return {
            "schema_version": 3,
            "repository": repository,
            "campaigns": {},
            "claims": {},
        }

    def _read_control(self, repository: str) -> tuple[dict[str, Any], str | None]:
        blob = self.client.read(
            repository, self.branch, self._repository_path(repository)
        )
        if blob is None:
            return self._empty_control(repository), None
        value = strict_json_decode(blob.content)
        if (
            not isinstance(value, dict)
            or set(value)
            != {"schema_version", "repository", "campaigns", "claims"}
            or value["schema_version"] != 3
            or value["repository"] != repository
            or not isinstance(value["campaigns"], dict)
            or not isinstance(value["claims"], dict)
        ):
            raise PlanControlError(
                "DURABLE_CONTROL_INVALID", "V3 durable control is malformed"
            )
        return value, blob.blob_sha

    def _cas(
        self,
        repository: str,
        current_sha: str | None,
        value: dict[str, Any],
        message: str,
    ) -> None:
        content = strict_json_bytes(value)
        try:
            written = self.client.compare_and_swap(
                repository,
                self.branch,
                self._repository_path(repository),
                content,
                expected_blob_sha=current_sha,
                message=message,
            )
        except Exception as error:
            readback, _ = self._read_control(repository)
            if strict_json_bytes(readback) == content:
                return
            raise CasRetry(str(error)) from error
        if written.content != content:
            raise PlanControlError(
                "DURABLE_CONTROL_READBACK_MISMATCH",
                "V3 control CAS acknowledgement has different bytes",
            )
        readback, _ = self._read_control(repository)
        if strict_json_bytes(readback) != content:
            # A disjoint CAS may have advanced the global record after our
            # exact acknowledgement. The caller re-reads and proves its own
            # reservation/transition rather than treating that advance as loss.
            raise CasRetry("repository control advanced after exact CAS")

    def publish_artifact(
        self, repository: str, kind: str, content: bytes
    ) -> str:
        decoded = strict_json_decode(content)
        canonical = strict_json_bytes(decoded)
        content_digest = digest(canonical)
        path = self._artifact_path(kind, content_digest)
        current = self.client.read(repository, self.branch, path)
        if current is None:
            try:
                self.client.compare_and_swap(
                    repository,
                    self.branch,
                    path,
                    canonical,
                    expected_blob_sha=None,
                    message=f"Publish immutable V3 {kind} {content_digest}",
                )
            except Exception:
                current = self.client.read(repository, self.branch, path)
                if current is None or current.content != canonical:
                    raise PlanControlError(
                        "DURABLE_ARTIFACT_AMBIGUOUS",
                        f"V3 {kind} publication acknowledgement is ambiguous",
                    )
        readback = self.client.read(repository, self.branch, path)
        if readback is None or readback.content != canonical:
            raise PlanControlError(
                "DURABLE_ARTIFACT_READBACK_MISMATCH",
                f"V3 {kind} did not read back exact canonical bytes",
            )
        return content_digest

    def read_artifact(
        self, repository: str, kind: str, content_digest: str
    ) -> bytes:
        if not DIGEST_PATTERN.fullmatch(content_digest):
            raise PlanControlError(
                "DURABLE_ARTIFACT_INVALID", "artifact digest is invalid"
            )
        blob = self.client.read(
            repository,
            self.branch,
            self._artifact_path(kind, content_digest),
        )
        if blob is None or digest(blob.content) != content_digest:
            raise PlanControlError(
                "DURABLE_ARTIFACT_READBACK_MISMATCH",
                f"V3 {kind} bytes or digest do not match",
            )
        strict_json_decode(blob.content)
        return blob.content

    def campaign(self, handle: CampaignHandle) -> dict[str, Any] | None:
        control, _ = self._read_control(handle.repository)
        value = control["campaigns"].get(handle.campaign_key)
        if value is not None and not isinstance(value, dict):
            raise PlanControlError(
                "DURABLE_CONTROL_INVALID", "Campaign record is malformed"
            )
        return value

    def active_digest(self, handle: CampaignHandle) -> str | None:
        campaign = self.campaign(handle)
        if campaign is None:
            return None
        value = campaign.get("active_plan_digest")
        if value is None:
            return None
        if not isinstance(value, str) or not DIGEST_PATTERN.fullmatch(value):
            raise PlanControlError(
                "DURABLE_CONTROL_INVALID",
                "active Campaign digest is malformed",
            )
        return value

    def reserve_claims(
        self,
        *,
        handle: CampaignHandle,
        snapshot_digest: str,
        runtime_facts_digest: str,
        planning_action_id: str,
        expected_previous_revision_digest: str | None,
        ticket_keys: tuple[str, ...],
        witness: WriterWitness,
    ) -> dict[str, Any]:
        for _attempt in range(self.cas_attempts):
            control, sha = self._read_control(handle.repository)
            existing = control["campaigns"].get(handle.campaign_key)
            if (
                isinstance(existing, dict)
                and existing.get("snapshot_digest") == snapshot_digest
                and existing.get("planning_action_id") == planning_action_id
            ):
                self._verify_reservation(
                    control,
                    handle=handle,
                    record=existing,
                    snapshot_digest=snapshot_digest,
                    runtime_facts_digest=runtime_facts_digest,
                    planning_action_id=planning_action_id,
                    ticket_keys=ticket_keys,
                )
                return existing
            if (
                isinstance(existing, dict)
                and existing.get("state") != STATE_ACTIVATION_COMMITTED
            ):
                raise PlanControlError(
                    "CAMPAIGN_IN_PROGRESS_CONFLICT",
                    "a different Campaign snapshot is already reserved",
                )
            actual_previous = (
                None
                if not isinstance(existing, dict)
                else existing.get("active_plan_digest")
            )
            if actual_previous != expected_previous_revision_digest:
                raise PlanControlError(
                    "ACTIVATION_CAS_CONFLICT",
                    "active Campaign revision changed before reservation",
                )
            findings: list[DecisionFinding] = []
            for ticket_key in ticket_keys:
                claim = control["claims"].get(ticket_key)
                if claim is not None and not isinstance(claim, dict):
                    raise PlanControlError(
                        "DURABLE_CONTROL_INVALID",
                        "Ticket claim is malformed",
                    )
                if isinstance(claim, dict) and claim.get("campaign_key") != (
                    handle.campaign_key
                ):
                    findings.append(
                        DecisionFinding(
                            code="TICKET_CLAIM_CONFLICT",
                            detail="Ticket is claimed by another Campaign",
                            ticket_key=ticket_key,
                        )
                    )
            if findings:
                raise ClaimConflict(tuple(sorted(findings)))
            record = {
                "state": STATE_CLAIMS_RESERVED,
                "snapshot_digest": snapshot_digest,
                "runtime_facts_digest": runtime_facts_digest,
                "planning_action_id": planning_action_id,
                "expected_previous_revision_digest": (
                    expected_previous_revision_digest
                ),
                "active_plan_digest": actual_previous,
                "writer_generation": witness.writer_generation,
                "writer_witness_digest": witness.digest,
                "ticket_keys": list(ticket_keys),
            }
            desired = strict_json_decode(strict_json_bytes(control))
            desired["campaigns"][handle.campaign_key] = record
            for ticket_key in ticket_keys:
                desired["claims"][ticket_key] = {
                    "campaign_key": handle.campaign_key,
                    "snapshot_digest": snapshot_digest,
                    "state": "pending",
                }
            try:
                self._cas(
                    handle.repository,
                    sha,
                    desired,
                    f"Reserve V3 Campaign {handle.campaign_key}",
                )
            except CasRetry:
                continue
            readback = self.campaign(handle)
            if readback != record:
                raise PlanControlError(
                    "CLAIM_RESERVATION_READBACK_MISMATCH",
                    "pending Ticket claims did not read back exactly",
                )
            return record
        raise PlanControlError(
            "DURABLE_CAS_EXHAUSTED", "repository-global claim CAS was exhausted"
        )

    def _verify_reservation(
        self,
        control: dict[str, Any],
        *,
        handle: CampaignHandle,
        record: dict[str, Any],
        snapshot_digest: str,
        runtime_facts_digest: str,
        planning_action_id: str,
        ticket_keys: tuple[str, ...],
    ) -> None:
        base_fields = {
            "state",
            "snapshot_digest",
            "runtime_facts_digest",
            "planning_action_id",
            "expected_previous_revision_digest",
            "active_plan_digest",
            "writer_generation",
            "writer_witness_digest",
            "ticket_keys",
        }
        optional_fields = {
            "intent_digest",
            "decision_digest",
            "plan_digest",
            "receipt_digest",
        }
        state = record.get("state")
        states = {
            STATE_CLAIMS_RESERVED,
            STATE_PLANNING_STARTED,
            STATE_INTENT_ACCEPTED,
            STATE_DECISION_REQUIRED,
            STATE_PLANNING_AMBIGUOUS,
            STATE_PLAN_PUBLISHED,
            STATE_ACTIVATION_COMMITTED,
        }
        identity_matches = (
            set(record).issubset(base_fields | optional_fields)
            and base_fields.issubset(record)
            and state in states
            and record.get("snapshot_digest") == snapshot_digest
            and DIGEST_PATTERN.fullmatch(snapshot_digest)
            and record.get("runtime_facts_digest") == runtime_facts_digest
            and DIGEST_PATTERN.fullmatch(runtime_facts_digest)
            and record.get("planning_action_id") == planning_action_id
            and isinstance(planning_action_id, str)
            and planning_action_id.startswith("planning:")
            and record.get("ticket_keys") == list(ticket_keys)
            and bool(ticket_keys)
            and tuple(sorted(set(ticket_keys))) == ticket_keys
            and isinstance(record.get("writer_generation"), str)
            and bool(record.get("writer_generation"))
            and isinstance(record.get("writer_witness_digest"), str)
            and bool(
                DIGEST_PATTERN.fullmatch(record["writer_witness_digest"])
            )
        )
        required_by_state = {
            STATE_INTENT_ACCEPTED: {"intent_digest"},
            STATE_PLAN_PUBLISHED: {"intent_digest", "plan_digest"},
            STATE_DECISION_REQUIRED: {"decision_digest"},
            STATE_PLANNING_AMBIGUOUS: {"decision_digest"},
            STATE_ACTIVATION_COMMITTED: {
                "intent_digest",
                "plan_digest",
                "receipt_digest",
            },
        }
        required = required_by_state.get(state, set())
        optional_digests = {
            field: record.get(field)
            for field in optional_fields
            if field in record
        }
        expected_previous = record.get("expected_previous_revision_digest")
        active_plan = record.get("active_plan_digest")
        revision_identity_matches = (
            (
                expected_previous is None
                or (
                    isinstance(expected_previous, str)
                    and DIGEST_PATTERN.fullmatch(expected_previous)
                )
            )
            and (
                active_plan is None
                or (
                    isinstance(active_plan, str)
                    and DIGEST_PATTERN.fullmatch(active_plan)
                )
            )
            and (
                active_plan == record.get("plan_digest")
                if state == STATE_ACTIVATION_COMMITTED
                else active_plan == expected_previous
            )
        )
        if not identity_matches or any(
            not isinstance(record.get(field), str) for field in required
        ) or any(
            not isinstance(value, str) or not DIGEST_PATTERN.fullmatch(value)
            for value in optional_digests.values()
        ) or not revision_identity_matches:
            raise PlanControlError(
                "CLAIM_RESERVATION_READBACK_MISMATCH",
                "durable Campaign reservation identities are malformed",
            )
        claim_state = (
            "active" if state == STATE_ACTIVATION_COMMITTED else "pending"
        )
        for ticket_key in ticket_keys:
            if control["claims"].get(ticket_key) != {
                "campaign_key": handle.campaign_key,
                "snapshot_digest": snapshot_digest,
                "state": claim_state,
            }:
                raise PlanControlError(
                    "CLAIM_RESERVATION_READBACK_MISMATCH",
                    "durable Ticket claims differ from the reservation",
                )

    def _transition(
        self,
        handle: CampaignHandle,
        *,
        expected_states: set[str],
        state: str,
        fields: Mapping[str, Any] | None = None,
    ) -> tuple[dict[str, Any], bool]:
        fields = fields or {}
        for _attempt in range(self.cas_attempts):
            control, sha = self._read_control(handle.repository)
            record = control["campaigns"].get(handle.campaign_key)
            if not isinstance(record, dict):
                raise PlanControlError(
                    "CAMPAIGN_RESERVATION_MISSING",
                    "durable Campaign reservation is missing",
                )
            if record.get("state") == state and all(
                record.get(key) == value for key, value in fields.items()
            ):
                return record, False
            if record.get("state") not in expected_states:
                return record, False
            updated = {**record, **fields, "state": state}
            desired = strict_json_decode(strict_json_bytes(control))
            desired["campaigns"][handle.campaign_key] = updated
            try:
                self._cas(
                    handle.repository,
                    sha,
                    desired,
                    f"Advance V3 Campaign {handle.campaign_key} to {state}",
                )
            except CasRetry:
                continue
            readback = self.campaign(handle)
            if readback != updated:
                raise PlanControlError(
                    "DURABLE_TRANSITION_READBACK_MISMATCH",
                    f"Campaign state {state} did not read back exactly",
                )
            return updated, True
        raise PlanControlError(
            "DURABLE_CAS_EXHAUSTED", "Campaign transition CAS was exhausted"
        )

    def begin_planning(self, handle: CampaignHandle) -> bool:
        _record, changed = self._transition(
            handle,
            expected_states={STATE_CLAIMS_RESERVED},
            state=STATE_PLANNING_STARTED,
        )
        return changed

    def accept_intent(
        self, handle: CampaignHandle, intent_digest: str
    ) -> dict[str, Any]:
        record, _ = self._transition(
            handle,
            expected_states={STATE_PLANNING_STARTED},
            state=STATE_INTENT_ACCEPTED,
            fields={"intent_digest": intent_digest},
        )
        if (
            record.get("state") != STATE_INTENT_ACCEPTED
            or record.get("intent_digest") != intent_digest
        ):
            raise PlanControlError(
                "PLAN_INTENT_READBACK_MISMATCH",
                "Plan Intent was not durably accepted",
            )
        return record

    def record_decision(
        self,
        handle: CampaignHandle,
        decision_digest: str,
        *,
        ambiguous: bool,
    ) -> None:
        expected = {
            STATE_CLAIMS_RESERVED,
            STATE_PLANNING_STARTED,
            STATE_INTENT_ACCEPTED,
            STATE_PLAN_PUBLISHED,
        }
        record, _ = self._transition(
            handle,
            expected_states=expected,
            state=(
                STATE_PLANNING_AMBIGUOUS
                if ambiguous
                else STATE_DECISION_REQUIRED
            ),
            fields={"decision_digest": decision_digest},
        )
        expected_state = (
            STATE_PLANNING_AMBIGUOUS
            if ambiguous
            else STATE_DECISION_REQUIRED
        )
        if (
            record.get("state") != expected_state
            or record.get("decision_digest") != decision_digest
        ):
            raise PlanControlError(
                "DECISION_READBACK_MISMATCH",
                "Decision was not durably recorded",
            )

    def mark_plan_published(
        self, handle: CampaignHandle, plan_digest: str
    ) -> dict[str, Any]:
        record, _ = self._transition(
            handle,
            expected_states={STATE_INTENT_ACCEPTED},
            state=STATE_PLAN_PUBLISHED,
            fields={"plan_digest": plan_digest},
        )
        if (
            record.get("state") != STATE_PLAN_PUBLISHED
            or record.get("plan_digest") != plan_digest
        ):
            raise PlanControlError(
                "PLAN_READBACK_MISMATCH",
                "PlanSpec publication was not durably recorded",
            )
        return record

    def activate(
        self,
        *,
        handle: CampaignHandle,
        revision: PlanRevision,
        witness: WriterWitness,
    ) -> bytes:
        plan_bytes = self.read_artifact(
            handle.repository, "plans", revision.digest
        )
        if plan_bytes != revision.canonical_bytes:
            raise PlanControlError(
                "PLAN_READBACK_MISMATCH",
                "activation requires exact published PlanSpec bytes",
            )
        for _attempt in range(self.cas_attempts):
            control, sha = self._read_control(handle.repository)
            record = control["campaigns"].get(handle.campaign_key)
            if not isinstance(record, dict):
                raise PlanControlError(
                    "CAMPAIGN_RESERVATION_MISSING",
                    "Campaign reservation is missing at activation",
                )
            if record.get("state") == STATE_ACTIVATION_COMMITTED:
                receipt_digest = record.get("receipt_digest")
                if not isinstance(receipt_digest, str):
                    raise PlanControlError(
                        "ACTIVATION_RECEIPT_INVALID",
                        "committed activation has no Receipt digest",
                    )
                return self.read_artifact(
                    handle.repository, "receipts", receipt_digest
                )
            if record.get("state") != STATE_PLAN_PUBLISHED:
                raise PlanControlError(
                    "ACTIVATION_STATE_INVALID",
                    "Campaign is not ready for activation",
                )
            raw_ticket_keys = record.get("ticket_keys")
            if not isinstance(raw_ticket_keys, list):
                raise PlanControlError(
                    "ACTIVATION_IDENTITY_MISMATCH",
                    "activation Ticket identities are malformed",
                )
            ticket_keys = tuple(raw_ticket_keys)
            self._verify_reservation(
                control,
                handle=handle,
                record=record,
                snapshot_digest=revision.snapshot_digest,
                runtime_facts_digest=record.get("runtime_facts_digest", ""),
                planning_action_id=record.get("planning_action_id", ""),
                ticket_keys=ticket_keys,
            )
            if (
                record.get("plan_digest") != revision.digest
                or record.get("snapshot_digest") != revision.snapshot_digest
                or record.get("writer_generation") != witness.writer_generation
                or record.get("writer_witness_digest") != witness.digest
            ):
                raise PlanControlError(
                    "ACTIVATION_IDENTITY_MISMATCH",
                    "activation identities differ from the reservation",
                )
            expected_previous = record.get(
                "expected_previous_revision_digest"
            )
            if record.get("active_plan_digest") != expected_previous:
                raise PlanControlError(
                    "ACTIVATION_CAS_CONFLICT",
                    "active revision differs from expected previous revision",
                )
            plan_ticket_keys = tuple(
                sorted(item["key"] for item in revision.plan_spec["work"])
            )
            if ticket_keys != plan_ticket_keys:
                raise PlanControlError(
                    "TICKET_CLAIM_MISMATCH",
                    "PlanSpec work differs from reserved Ticket claims",
                )
            for ticket_key in ticket_keys:
                claim = control["claims"].get(ticket_key)
                if not isinstance(claim, dict) or claim != {
                    "campaign_key": handle.campaign_key,
                    "snapshot_digest": revision.snapshot_digest,
                    "state": "pending",
                }:
                    raise PlanControlError(
                        "TICKET_CLAIM_MISMATCH",
                        "activation requires exact pending Ticket claims",
                    )
            receipt = ActivationReceipt(
                repository=handle.repository,
                campaign_key=handle.campaign_key,
                revision_digest=revision.digest,
                expected_previous_revision_digest=expected_previous,
                writer_generation=witness.writer_generation,
                writer_witness_digest=witness.digest,
                snapshot_digest=revision.snapshot_digest,
                planning_action_id=record["planning_action_id"],
                ticket_keys=ticket_keys,
            )
            receipt_bytes = strict_json_bytes(receipt.as_value())
            receipt_digest = self.publish_artifact(
                handle.repository, "receipts", receipt_bytes
            )
            updated = {
                **record,
                "state": STATE_ACTIVATION_COMMITTED,
                "active_plan_digest": revision.digest,
                "receipt_digest": receipt_digest,
            }
            desired = strict_json_decode(strict_json_bytes(control))
            desired["campaigns"][handle.campaign_key] = updated
            for ticket_key in ticket_keys:
                desired["claims"][ticket_key] = {
                    "campaign_key": handle.campaign_key,
                    "snapshot_digest": revision.snapshot_digest,
                    "state": "active",
                }
            try:
                self._cas(
                    handle.repository,
                    sha,
                    desired,
                    f"Activate V3 Campaign {handle.campaign_key}",
                )
            except CasRetry:
                continue
            readback = self.campaign(handle)
            if readback != updated:
                raise PlanControlError(
                    "ACTIVATION_READBACK_MISMATCH",
                    "activation commit did not read back exactly",
                )
            return self.read_artifact(
                handle.repository, "receipts", receipt_digest
            )
        raise PlanControlError(
            "DURABLE_CAS_EXHAUSTED", "activation CAS was exhausted"
        )
