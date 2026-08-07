"""Durable journal and compare-and-swap values for V3 Batch delivery."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path
import re
from typing import Callable, Literal, Protocol

from ._canonical import digest_value
from .batch_integrator import (
    BatchDeliveryAction,
    BatchIntegratorError,
    DeliveryAttributionAmbiguous,
    DeliveryIdentityMismatch,
)


@dataclass(frozen=True)
class BatchJournalRecord:
    stable_action_id: str
    request_digest: str
    batch_id: str
    batch_sha: str
    phase: Literal[
        "prepared",
        "composed",
        "local_checked",
        "published",
        "hosted",
        "integrating",
        "complete",
        "wait",
        "decision",
        "blocked",
    ]
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
            receipt_digest=digest_value(
                {"kind": "hosted_result_receipt.v1", **body}
            ),
        )


class BatchDeliveryJournal(Protocol):
    """Persistence boundary consumed by the BatchIntegrator action loop."""

    def read_action(self, stable_action_id: str) -> BatchJournalRecord | None: ...

    def create_action(
        self, action: BatchDeliveryAction, request_digest: str, **kwargs: object
    ) -> BatchJournalRecord: ...

    def compare_and_swap_action(
        self,
        stable_action_id: str,
        *,
        expected_version: int,
        expected_phase: str,
        next_record: BatchJournalRecord,
    ) -> BatchJournalRecord: ...

    def read_hosted_result(
        self,
        stable_action_id: str,
        batch_sha: str,
        suite_id: str,
        provider_check_id: str,
    ) -> HostedResultReceipt | None: ...

    def read_terminal_hosted_result(
        self,
        stable_action_id: str,
        batch_sha: str,
        suite_id: str,
    ) -> HostedResultReceipt | None: ...

    def persist_hosted_result(
        self, receipt: HostedResultReceipt
    ) -> HostedResultReceipt: ...

    def persist_member_evidence(
        self,
        stable_action_id: str,
        ticket_key: str,
        candidate_sha: str,
        evidence_digests: tuple[str, ...],
        review_finding_ledger_digest: str,
    ) -> BatchJournalRecord: ...

    def acquire_integration_lease(
        self,
        repository: str,
        holder: str,
        writer_generation: str,
        activation_id: str,
    ) -> IntegrationLeaseReceipt: ...

    def release_integration_lease(
        self, repository: str, lease: IntegrationLeaseReceipt
    ) -> None: ...


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

    @contextmanager
    def _connection(self):
        connection = self._connect()
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connection() as connection:
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
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM v8_batch_delivery_actions WHERE stable_action_id=?",
                (stable_action_id,),
            ).fetchone()
        return None if row is None else BatchJournalRecord(**dict(row))

    def _insert_action_if_absent(self, record: BatchJournalRecord) -> None:
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO v8_batch_delivery_actions
                (stable_action_id, request_digest, batch_id, batch_sha, phase,
                 reason, retry_count, fallback_generation, state_json, version)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(stable_action_id) DO NOTHING
                """,
                tuple(record.body().values()),
            )
        existing = self.read_action(record.stable_action_id)
        if existing != record:
            raise DeliveryIdentityMismatch("batch action identity changed")

    def compare_and_swap_action(
        self,
        stable_action_id: str,
        *,
        expected_version: int,
        expected_phase: str,
        next_record: BatchJournalRecord,
    ) -> BatchJournalRecord:
        if (
            next_record.stable_action_id != stable_action_id
            or next_record.version != expected_version + 1
        ):
            raise DeliveryIdentityMismatch("batch action identity or version changed")
        current = self.read_action(stable_action_id)
        if current is not None and (
            current.request_digest != next_record.request_digest
            or current.batch_id != next_record.batch_id
            or current.batch_sha != next_record.batch_sha
        ):
            raise DeliveryIdentityMismatch("batch action identity changed")
        with self._connection() as connection:
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
        updated = self.read_action(stable_action_id)
        if updated is None:
            raise BatchIntegratorError(
                "BATCH_ACTION_CAS_CONFLICT",
                "action disappeared after compare-and-swap",
            )
        return updated

    def read_integration_lease(
        self, repository: str
    ) -> IntegrationLeaseReceipt | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM v8_batch_integration_leases WHERE repository=?",
                (repository,),
            ).fetchone()
        if row is None:
            return None
        receipt = IntegrationLeaseReceipt(**dict(row))
        self._validate_lease_digest(receipt)
        return receipt

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
        with self._connection() as connection:
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

    def release_integration_lease(
        self, repository: str, lease: IntegrationLeaseReceipt
    ) -> None:
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
        with self._connection() as connection:
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
    def _validate_lease_digest(receipt: IntegrationLeaseReceipt) -> None:
        if any(
            type(value) is not str or not value or "\x00" in value
            for value in receipt.body().values()
        ):
            raise DeliveryIdentityMismatch("integration lease identity is malformed")
        if (
            type(receipt.lease_digest) is not str
            or re.fullmatch(r"[0-9a-f]{64}", receipt.lease_digest) is None
        ):
            raise DeliveryIdentityMismatch("integration lease digest is malformed")
        expected = digest_value(
            {"kind": "integration-lease.v1", **receipt.body()}
        )
        if expected != receipt.lease_digest:
            raise DeliveryIdentityMismatch("integration lease digest mismatch")

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
        expected = digest_value(
            {"kind": "hosted_result_receipt.v1", **receipt.body()}
        )
        if expected != receipt.receipt_digest:
            raise DeliveryIdentityMismatch("hosted receipt digest mismatch")

    def _read_hosted_rows_for_identity(
        self, receipt: HostedResultReceipt
    ) -> list[HostedResultReceipt]:
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT * FROM v8_batch_hosted_receipts
                 WHERE stable_action_id=?
                    OR (batch_sha=? AND suite_id=? AND provider_check_id=?)
                """,
                (
                    receipt.stable_action_id,
                    receipt.batch_sha,
                    receipt.suite_id,
                    receipt.provider_check_id,
                ),
            ).fetchall()
        values = [HostedResultReceipt(**dict(row)) for row in rows]
        for value in values:
            self._validate_hosted_receipt_digest(value)
        return values

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
        if self._read_hosted_rows_for_identity(receipt):
            raise DeliveryIdentityMismatch("hosted receipt identity changed")
        with self._connection() as connection:
            try:
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
            except sqlite3.IntegrityError as error:
                raise DeliveryIdentityMismatch(
                    "hosted receipt identity changed"
                ) from error
        self.crash_hook("hosted_receipt_persisted")
        return receipt

    def read_hosted_result(
        self,
        stable_action_id: str,
        batch_sha: str,
        suite_id: str,
        provider_check_id: str,
    ) -> HostedResultReceipt | None:
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT * FROM v8_batch_hosted_receipts
                 WHERE stable_action_id=? AND batch_sha=? AND suite_id=?
                   AND provider_check_id=?
                """,
                (stable_action_id, batch_sha, suite_id, provider_check_id),
            ).fetchone()
        if row is None:
            return None
        receipt = HostedResultReceipt(**dict(row))
        self._validate_hosted_receipt_digest(receipt)
        return receipt

    def read_terminal_hosted_result(
        self,
        stable_action_id: str,
        batch_sha: str,
        suite_id: str,
    ) -> HostedResultReceipt | None:
        with self._connection() as connection:
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
            phase=phase,  # type: ignore[arg-type]
            reason=reason,
            retry_count=retry_count,
            fallback_generation=fallback_generation,
            state_json=state_json,
            version=0,
        )
        self._insert_action_if_absent(record)
        existing = self.read_action(action.stable_action_id)
        if existing is None:
            raise BatchIntegratorError(
                "BATCH_ACTION_PERSISTENCE_FAILED",
                "action was not readable after creation",
            )
        return existing

    def persist_member_evidence(
        self,
        stable_action_id: str,
        ticket_key: str,
        candidate_sha: str,
        evidence_digests: tuple[str, ...],
        review_finding_ledger_digest: str,
    ) -> BatchJournalRecord:
        record = self.read_action(stable_action_id)
        if record is None:
            raise BatchIntegratorError(
                "BATCH_ACTION_MISSING",
                "cannot preserve evidence for a missing Batch action",
            )
        try:
            state = json.loads(record.state_json or "{}")
        except json.JSONDecodeError as error:
            raise DeliveryIdentityMismatch(
                "journal action state is not canonical JSON"
            ) from error
        if not isinstance(state, dict):
            raise DeliveryIdentityMismatch("journal action state is not an object")
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

    def advance_action(
        self, record: BatchJournalRecord, *, phase: str, reason: str
    ) -> BatchJournalRecord:
        return self.compare_and_swap_action(
            record.stable_action_id,
            expected_version=record.version,
            expected_phase=record.phase,
            next_record=replace(
                record,
                phase=phase,  # type: ignore[arg-type]
                reason=reason,
                version=record.version + 1,
            ),
        )
