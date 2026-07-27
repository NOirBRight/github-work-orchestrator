"""Independent local V3 SQLite journal.

Only ``v3_*`` tables are created or accessed here. The journal is a rebuildable
local projection; repository-global claims and activation authority remain in
the durable GitHub control record.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import sqlite3

from ._v3_canonical import digest, strict_json_decode
from ._v3_types import (
    CampaignHandle,
    DIGEST_PATTERN,
    JournalRecord,
    PlanControlError,
    PlanRevision,
    STATE_ACTIVATION_COMMITTED,
    STATE_ACTIVE_LOCAL,
    STATE_CLAIMS_RESERVED,
    STATE_DECISION_REQUIRED,
    STATE_INTENT_ACCEPTED,
    STATE_PLAN_PUBLISHED,
    STATE_PLANNING_AMBIGUOUS,
    STATE_PLANNING_STARTED,
    STATE_SNAPSHOTTED,
)


class SQLiteV3Journal:
    def __init__(self, path: str | Path):
        self.path = str(path)
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS v3_campaign_journal (
                    repository TEXT NOT NULL,
                    campaign_key TEXT NOT NULL,
                    snapshot_digest TEXT NOT NULL,
                    state TEXT NOT NULL,
                    snapshot_bytes BLOB NOT NULL,
                    options_bytes BLOB NOT NULL,
                    options_digest TEXT NOT NULL,
                    planning_action_id TEXT NOT NULL,
                    expected_previous_revision_digest TEXT,
                    writer_generation TEXT,
                    writer_witness_digest TEXT,
                    intent_bytes BLOB,
                    intent_digest TEXT,
                    decision_bytes BLOB,
                    decision_digest TEXT,
                    plan_bytes BLOB,
                    plan_digest TEXT,
                    receipt_bytes BLOB,
                    receipt_digest TEXT,
                    PRIMARY KEY (repository, campaign_key, snapshot_digest)
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS v3_active_campaigns (
                    repository TEXT NOT NULL,
                    campaign_key TEXT NOT NULL,
                    plan_digest TEXT NOT NULL,
                    snapshot_digest TEXT NOT NULL,
                    receipt_bytes BLOB NOT NULL,
                    receipt_digest TEXT NOT NULL,
                    PRIMARY KEY (repository, campaign_key)
                )
                """
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        return connection

    def read(
        self, repository: str, campaign_key: str, snapshot_digest: str
    ) -> JournalRecord | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM v3_campaign_journal
                WHERE repository = ? AND campaign_key = ? AND snapshot_digest = ?
                """,
                (repository, campaign_key, snapshot_digest),
            ).fetchone()
        if row is None:
            return None
        record = JournalRecord(
            repository=row["repository"],
            campaign_key=row["campaign_key"],
            snapshot_digest=row["snapshot_digest"],
            state=row["state"],
            snapshot_bytes=bytes(row["snapshot_bytes"]),
            options_bytes=bytes(row["options_bytes"]),
            options_digest=row["options_digest"],
            planning_action_id=row["planning_action_id"],
            expected_previous_revision_digest=(
                row["expected_previous_revision_digest"]
            ),
            writer_generation=row["writer_generation"],
            writer_witness_digest=row["writer_witness_digest"],
            intent_bytes=(
                None if row["intent_bytes"] is None else bytes(row["intent_bytes"])
            ),
            intent_digest=row["intent_digest"],
            decision_bytes=(
                None
                if row["decision_bytes"] is None
                else bytes(row["decision_bytes"])
            ),
            decision_digest=row["decision_digest"],
            plan_bytes=(
                None if row["plan_bytes"] is None else bytes(row["plan_bytes"])
            ),
            plan_digest=row["plan_digest"],
            receipt_bytes=(
                None
                if row["receipt_bytes"] is None
                else bytes(row["receipt_bytes"])
            ),
            receipt_digest=row["receipt_digest"],
        )
        return self._validate_record(record)

    def save(self, record: JournalRecord) -> JournalRecord:
        self._validate_record(record)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                """
                SELECT snapshot_bytes, options_bytes, options_digest,
                       planning_action_id,
                       expected_previous_revision_digest, state
                FROM v3_campaign_journal
                WHERE repository = ? AND campaign_key = ? AND snapshot_digest = ?
                """,
                (
                    record.repository,
                    record.campaign_key,
                    record.snapshot_digest,
                ),
            ).fetchone()
            if existing is not None and (
                bytes(existing["snapshot_bytes"]) != record.snapshot_bytes
                or bytes(existing["options_bytes"]) != record.options_bytes
                or existing["options_digest"] != record.options_digest
                or existing["planning_action_id"] != record.planning_action_id
                or existing["expected_previous_revision_digest"]
                != record.expected_previous_revision_digest
            ):
                raise PlanControlError(
                    "JOURNAL_IDENTITY_CONFLICT",
                    "preplanning snapshot identity is immutable",
                )
            ranks = {
                STATE_SNAPSHOTTED: 0,
                STATE_CLAIMS_RESERVED: 1,
                STATE_PLANNING_STARTED: 2,
                STATE_INTENT_ACCEPTED: 3,
                STATE_PLAN_PUBLISHED: 4,
                STATE_DECISION_REQUIRED: 5,
                STATE_PLANNING_AMBIGUOUS: 5,
                STATE_ACTIVATION_COMMITTED: 6,
                STATE_ACTIVE_LOCAL: 7,
            }
            if (
                existing is not None
                and ranks[record.state] < ranks[existing["state"]]
            ):
                raise PlanControlError(
                    "JOURNAL_STATE_REGRESSION",
                    "V3 journal state cannot move backwards",
                )
            connection.execute(
                """
                INSERT INTO v3_campaign_journal (
                    repository, campaign_key, snapshot_digest, state,
                    snapshot_bytes, options_bytes, options_digest,
                    planning_action_id, expected_previous_revision_digest,
                    writer_generation, writer_witness_digest,
                    intent_bytes, intent_digest, decision_bytes, decision_digest,
                    plan_bytes, plan_digest, receipt_bytes, receipt_digest
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(repository, campaign_key, snapshot_digest) DO UPDATE SET
                    state = excluded.state,
                    writer_generation = excluded.writer_generation,
                    writer_witness_digest = excluded.writer_witness_digest,
                    intent_bytes = excluded.intent_bytes,
                    intent_digest = excluded.intent_digest,
                    decision_bytes = excluded.decision_bytes,
                    decision_digest = excluded.decision_digest,
                    plan_bytes = excluded.plan_bytes,
                    plan_digest = excluded.plan_digest,
                    receipt_bytes = excluded.receipt_bytes,
                    receipt_digest = excluded.receipt_digest
                """,
                (
                    record.repository,
                    record.campaign_key,
                    record.snapshot_digest,
                    record.state,
                    record.snapshot_bytes,
                    record.options_bytes,
                    record.options_digest,
                    record.planning_action_id,
                    record.expected_previous_revision_digest,
                    record.writer_generation,
                    record.writer_witness_digest,
                    record.intent_bytes,
                    record.intent_digest,
                    record.decision_bytes,
                    record.decision_digest,
                    record.plan_bytes,
                    record.plan_digest,
                    record.receipt_bytes,
                    record.receipt_digest,
                ),
            )
        result = self.read(
            record.repository, record.campaign_key, record.snapshot_digest
        )
        if result != record:
            raise PlanControlError(
                "JOURNAL_READBACK_MISMATCH", "V3 journal did not read back exactly"
            )
        return result

    @staticmethod
    def _validate_record(record: JournalRecord) -> JournalRecord:
        states = {
            STATE_SNAPSHOTTED,
            STATE_CLAIMS_RESERVED,
            STATE_PLANNING_STARTED,
            STATE_INTENT_ACCEPTED,
            STATE_DECISION_REQUIRED,
            STATE_PLANNING_AMBIGUOUS,
            STATE_PLAN_PUBLISHED,
            STATE_ACTIVATION_COMMITTED,
            STATE_ACTIVE_LOCAL,
        }
        if (
            record.state not in states
            or not record.planning_action_id
            or digest(record.snapshot_bytes) != record.snapshot_digest
        ):
            raise PlanControlError(
                "SNAPSHOT_DIGEST_MISMATCH",
                "V3 journal snapshot identity is invalid",
            )
        strict_json_decode(record.snapshot_bytes)
        if digest(record.options_bytes) != record.options_digest:
            raise PlanControlError(
                "RUNTIME_FACTS_DIGEST_MISMATCH",
                "V3 journal Runtime facts identity is invalid",
            )
        strict_json_decode(record.options_bytes)
        optional = (
            (
                "Plan Intent",
                record.intent_bytes,
                record.intent_digest,
                "PLAN_INTENT_READBACK_MISMATCH",
            ),
            (
                "Decision",
                record.decision_bytes,
                record.decision_digest,
                "DECISION_READBACK_MISMATCH",
            ),
            (
                "PlanSpec",
                record.plan_bytes,
                record.plan_digest,
                "PLAN_DIGEST_MISMATCH",
            ),
            (
                "Activation Receipt",
                record.receipt_bytes,
                record.receipt_digest,
                "ACTIVATION_RECEIPT_INVALID",
            ),
        )
        for label, content, content_digest, code in optional:
            if (content is None) != (content_digest is None):
                raise PlanControlError(
                    code, f"V3 journal {label} bytes and digest are incomplete"
                )
            if content is None:
                continue
            if (
                not isinstance(content_digest, str)
                or not DIGEST_PATTERN.fullmatch(content_digest)
                or digest(content) != content_digest
            ):
                raise PlanControlError(
                    code, f"V3 journal {label} digest is invalid"
                )
            strict_json_decode(content)
        return record

    def finalize(
        self,
        record: JournalRecord,
        revision: PlanRevision,
        receipt_bytes: bytes,
    ) -> JournalRecord:
        receipt_digest = digest(receipt_bytes)
        updated = self.save(
            replace(
                record,
                state=STATE_ACTIVE_LOCAL,
                plan_bytes=revision.canonical_bytes,
                plan_digest=revision.digest,
                receipt_bytes=receipt_bytes,
                receipt_digest=receipt_digest,
            )
        )
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT INTO v3_active_campaigns (
                    repository, campaign_key, plan_digest, snapshot_digest,
                    receipt_bytes, receipt_digest
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(repository, campaign_key) DO UPDATE SET
                    plan_digest = excluded.plan_digest,
                    snapshot_digest = excluded.snapshot_digest,
                    receipt_bytes = excluded.receipt_bytes,
                    receipt_digest = excluded.receipt_digest
                """,
                (
                    record.repository,
                    record.campaign_key,
                    revision.digest,
                    record.snapshot_digest,
                    receipt_bytes,
                    receipt_digest,
                ),
            )
        return updated

    def read_active(self, handle: CampaignHandle) -> tuple[str, str, bytes] | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT plan_digest, snapshot_digest, receipt_bytes, receipt_digest
                FROM v3_active_campaigns
                WHERE repository = ? AND campaign_key = ?
                """,
                (handle.repository, handle.campaign_key),
            ).fetchone()
        if row is None:
            return None
        receipt_bytes = bytes(row["receipt_bytes"])
        if (
            digest(receipt_bytes) != row["receipt_digest"]
            or not DIGEST_PATTERN.fullmatch(row["plan_digest"])
            or not DIGEST_PATTERN.fullmatch(row["snapshot_digest"])
        ):
            raise PlanControlError(
                "ACTIVATION_RECEIPT_INVALID",
                "local active Campaign identities are invalid",
            )
        strict_json_decode(receipt_bytes)
        return (
            row["plan_digest"],
            row["snapshot_digest"],
            receipt_bytes,
        )
