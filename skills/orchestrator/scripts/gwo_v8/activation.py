"""Local Phase 1 Plan publication and activation over a real SQLite Store."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import sqlite3
from typing import Any

from .compiler import CompiledPlan


class ActivationError(RuntimeError):
    """A fail-closed local activation error."""

    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class ActivationOutcome:
    status: str
    repository: str
    plan_digest: str
    writer_generation: str


@dataclass(frozen=True)
class PublishedPlan:
    repository: str
    plan_digest: str
    canonical_bytes: bytes
    compilation_record: dict[str, Any]
    writer_generation: str


class LocalPlanPublication:
    """Minimum local activation protocol; durable GitHub receipts start in #42."""

    def __init__(self, store_path: Path):
        self.store_path = Path(store_path)
        self.store_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS v8_plan_revisions (
                    repository TEXT NOT NULL,
                    plan_digest TEXT NOT NULL,
                    canonical_bytes BLOB NOT NULL,
                    compilation_record TEXT NOT NULL,
                    writer_generation TEXT NOT NULL,
                    PRIMARY KEY (repository, plan_digest)
                );
                CREATE TABLE IF NOT EXISTS v8_active_plans (
                    repository TEXT PRIMARY KEY,
                    plan_digest TEXT NOT NULL,
                    writer_generation TEXT NOT NULL
                );
                """
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.store_path)
        connection.row_factory = sqlite3.Row
        return connection

    def publish_and_activate(
        self,
        compiled_plan: CompiledPlan,
        *,
        expected_active_digest: str | None,
        writer_generation: str,
    ) -> ActivationOutcome:
        if not compiled_plan.has_valid_digest():
            raise ActivationError(
                "COMPILED_PLAN_DIGEST_MISMATCH",
                "CompiledPlan bytes do not match the Compiler digest",
            )
        if not isinstance(writer_generation, str) or not writer_generation:
            raise ActivationError(
                "WRITER_GENERATION_INVALID", "writer generation is required"
            )

        record_json = json.dumps(
            compiled_plan.compilation_record,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        with self._connect() as connection:
            existing = connection.execute(
                """
                SELECT canonical_bytes, compilation_record, writer_generation
                FROM v8_plan_revisions
                WHERE repository = ? AND plan_digest = ?
                """,
                (compiled_plan.repository, compiled_plan.digest),
            ).fetchone()
            if existing is not None and (
                bytes(existing["canonical_bytes"]) != compiled_plan.canonical_bytes
                or existing["compilation_record"] != record_json
                or existing["writer_generation"] != writer_generation
            ):
                raise ActivationError(
                    "PLAN_REVISION_CONFLICT",
                    "published Plan Revision content is immutable",
                )
            connection.execute(
                """
                INSERT OR IGNORE INTO v8_plan_revisions (
                    repository,
                    plan_digest,
                    canonical_bytes,
                    compilation_record,
                    writer_generation
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    compiled_plan.repository,
                    compiled_plan.digest,
                    compiled_plan.canonical_bytes,
                    record_json,
                    writer_generation,
                ),
            )
            active = connection.execute(
                """
                SELECT plan_digest, writer_generation
                FROM v8_active_plans
                WHERE repository = ?
                """,
                (compiled_plan.repository,),
            ).fetchone()
            current_digest = None if active is None else str(active["plan_digest"])
            if current_digest == compiled_plan.digest:
                if active["writer_generation"] != writer_generation:
                    raise ActivationError(
                        "WRITER_GENERATION_CONFLICT",
                        "active Plan Revision belongs to another writer generation",
                    )
            elif current_digest != expected_active_digest:
                raise ActivationError(
                    "ACTIVATION_CONFLICT",
                    "active Plan Revision does not match the expected digest",
                )
            else:
                connection.execute(
                    """
                    INSERT INTO v8_active_plans (
                        repository,
                        plan_digest,
                        writer_generation
                    ) VALUES (?, ?, ?)
                    ON CONFLICT(repository) DO UPDATE SET
                        plan_digest = excluded.plan_digest,
                        writer_generation = excluded.writer_generation
                    """,
                    (
                        compiled_plan.repository,
                        compiled_plan.digest,
                        writer_generation,
                    ),
                )
        return ActivationOutcome(
            status="active",
            repository=compiled_plan.repository,
            plan_digest=compiled_plan.digest,
            writer_generation=writer_generation,
        )

    def read_active(self, repository: str) -> PublishedPlan | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT
                    revisions.plan_digest,
                    revisions.canonical_bytes,
                    revisions.compilation_record,
                    active.writer_generation
                FROM v8_active_plans AS active
                JOIN v8_plan_revisions AS revisions
                  ON revisions.repository = active.repository
                 AND revisions.plan_digest = active.plan_digest
                WHERE active.repository = ?
                """,
                (repository,),
            ).fetchone()
        if row is None:
            return None
        return PublishedPlan(
            repository=repository,
            plan_digest=str(row["plan_digest"]),
            canonical_bytes=bytes(row["canonical_bytes"]),
            compilation_record=json.loads(row["compilation_record"]),
            writer_generation=str(row["writer_generation"]),
        )
