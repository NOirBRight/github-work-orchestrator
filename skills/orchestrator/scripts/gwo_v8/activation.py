"""Durable Plan publication and crash-recoverable activation."""

from __future__ import annotations

import base64
from dataclasses import dataclass, replace
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import subprocess
from typing import Any, Callable, Protocol

from ._canonical import canonical_bytes, digest_value
from .compiler import CompiledPlan


class ActivationError(RuntimeError):
    """A fail-closed activation error with a stable machine code."""

    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code = code
        self.detail = detail


class ActivationCheckpointCrash(RuntimeError):
    """Test-only process crash injected after a committed protocol boundary."""


@dataclass(frozen=True)
class ActivationReceipt:
    schema_version: int
    repository: str
    writer_generation: str
    activation_id: str
    plan_digest: str
    expected_previous_digest: str | None
    plan_record_ref: str
    created_at: str

    def with_plan_digest(self, digest: str) -> ActivationReceipt:
        return replace(self, plan_digest=digest)

    def as_dict(self) -> dict[str, Any]:
        return {
            field: getattr(self, field)
            for field in self.__dataclass_fields__
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> ActivationReceipt:
        try:
            receipt = cls(
                schema_version=value["schema_version"],
                repository=value["repository"],
                writer_generation=value["writer_generation"],
                activation_id=value["activation_id"],
                plan_digest=value["plan_digest"],
                expected_previous_digest=value.get("expected_previous_digest"),
                plan_record_ref=value["plan_record_ref"],
                created_at=value["created_at"],
            )
        except (KeyError, TypeError) as error:
            raise ActivationError(
                "ACTIVATION_RECEIPT_INVALID",
                "durable Activation Receipt is malformed",
            ) from error
        if (
            receipt.schema_version != 1
            or not receipt.repository
            or not receipt.writer_generation
            or not receipt.activation_id
            or len(receipt.plan_digest) != 64
            or not receipt.plan_record_ref
            or not receipt.created_at
        ):
            raise ActivationError(
                "ACTIVATION_RECEIPT_INVALID",
                "durable Activation Receipt fields are invalid",
            )
        return receipt


@dataclass(frozen=True)
class DurablePlanRecord:
    repository: str
    plan_digest: str
    canonical_bytes: bytes
    compilation_record: dict[str, Any]
    record_ref: str


class DurablePlanControl(Protocol):
    """GitHub-shaped durable control record used by activation."""

    def plan_record_ref(self, repository: str, plan_digest: str) -> str: ...

    def publish_plan(self, record: DurablePlanRecord) -> None: ...

    def read_plan(
        self, repository: str, plan_digest: str
    ) -> DurablePlanRecord | None: ...

    def publish_activation(
        self,
        receipt: ActivationReceipt,
        *,
        expected_previous_digest: str | None,
    ) -> None: ...

    def read_activation(
        self, repository: str, activation_id: str
    ) -> ActivationReceipt | None: ...

    def read_current_activation(
        self, repository: str
    ) -> ActivationReceipt | None: ...


@dataclass(frozen=True)
class GitHubContent:
    content: bytes
    blob_sha: str


class GitHubContentClient(Protocol):
    """Minimal compare-and-swap surface over one GitHub control branch."""

    def read(
        self,
        repository: str,
        branch: str,
        path: str,
    ) -> GitHubContent | None: ...

    def compare_and_swap(
        self,
        repository: str,
        branch: str,
        path: str,
        content: bytes,
        *,
        expected_blob_sha: str | None,
        message: str,
    ) -> GitHubContent: ...


@dataclass(frozen=True)
class ActivationOutcome:
    status: str
    repository: str
    plan_digest: str
    writer_generation: str
    activation_id: str


@dataclass(frozen=True)
class PublishedPlan:
    repository: str
    plan_digest: str
    canonical_bytes: bytes
    compilation_record: dict[str, Any]
    writer_generation: str
    activation_id: str


class InMemoryDurablePlanControl:
    """Explicit contract fake for the GitHub durable control boundary."""

    def __init__(self, *, fail_once_after: set[str] | None = None):
        self._plans: dict[tuple[str, str], DurablePlanRecord] = {}
        self._receipts: dict[tuple[str, str], ActivationReceipt] = {}
        self._active: dict[str, str] = {}
        self._fail_once_after = set(fail_once_after or ())

    def _maybe_fail_after(self, operation: str) -> None:
        if operation not in self._fail_once_after:
            return
        self._fail_once_after.remove(operation)
        raise ActivationError(
            "DURABLE_STATE_AMBIGUOUS",
            f"durable {operation} committed but acknowledgement was lost",
        )

    def plan_record_ref(self, repository: str, plan_digest: str) -> str:
        return f"github-control://{repository}/plans/{plan_digest}"

    def publish_plan(self, record: DurablePlanRecord) -> None:
        key = (record.repository, record.plan_digest)
        existing = self._plans.get(key)
        if existing is not None and existing != record:
            raise ActivationError(
                "PLAN_REVISION_CONFLICT",
                "durable Plan Revision content is immutable",
            )
        self._plans[key] = record
        self._maybe_fail_after("publish_plan")

    def read_plan(
        self, repository: str, plan_digest: str
    ) -> DurablePlanRecord | None:
        return self._plans.get((repository, plan_digest))

    def publish_activation(
        self,
        receipt: ActivationReceipt,
        *,
        expected_previous_digest: str | None,
    ) -> None:
        key = (receipt.repository, receipt.activation_id)
        existing = self._receipts.get(key)
        if existing is not None:
            if existing != receipt:
                raise ActivationError(
                    "ACTIVATION_RECEIPT_IMMUTABLE",
                    "Activation Receipts cannot be rewritten",
                )
            return
        current = self._active.get(receipt.repository)
        if current != expected_previous_digest:
            raise ActivationError(
                "ACTIVATION_CONFLICT",
                "durable active Plan does not match the expected digest",
            )
        self._receipts[key] = receipt
        self._active[receipt.repository] = receipt.plan_digest
        self._maybe_fail_after("publish_activation")

    def read_activation(
        self, repository: str, activation_id: str
    ) -> ActivationReceipt | None:
        return self._receipts.get((repository, activation_id))

    def read_current_activation(
        self, repository: str
    ) -> ActivationReceipt | None:
        active_digest = self._active.get(repository)
        if active_digest is None:
            return None
        matches = [
            receipt
            for (candidate_repository, _activation_id), receipt in self._receipts.items()
            if candidate_repository == repository
            and receipt.plan_digest == active_digest
        ]
        if len(matches) != 1:
            raise ActivationError(
                "ACTIVATION_LOG_INVALID",
                "durable active Plan lacks one exact Activation Receipt",
            )
        return matches[0]

    def activation_count(self, repository: str) -> int:
        return sum(key[0] == repository for key in self._receipts)

    def plan_count(self, repository: str) -> int:
        return sum(key[0] == repository for key in self._plans)


class GitHubCliContentClient:
    """Production GitHub Contents client using authenticated ``gh api``."""

    def __init__(self, executable: str = "gh"):
        self.executable = executable

    def _run(
        self,
        args: list[str],
        *,
        input_text: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [self.executable, *args],
            input=input_text,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )

    def read(
        self,
        repository: str,
        branch: str,
        path: str,
    ) -> GitHubContent | None:
        result = self._run(
            [
                "api",
                "--method",
                "GET",
                f"repos/{repository}/contents/{path}",
                "-f",
                f"ref={branch}",
            ]
        )
        if result.returncode != 0:
            lowered = f"{result.stdout}\n{result.stderr}".casefold()
            if "404" in lowered or "not found" in lowered:
                return None
            raise ActivationError(
                "DURABLE_READ_FAILED",
                result.stderr.strip()
                or result.stdout.strip()
                or "GitHub control read failed",
            )
        try:
            payload = json.loads(result.stdout)
            encoded = str(payload["content"]).replace("\n", "")
            return GitHubContent(
                content=base64.b64decode(encoded, validate=True),
                blob_sha=str(payload["sha"]),
            )
        except (KeyError, ValueError, TypeError, json.JSONDecodeError) as error:
            raise ActivationError(
                "DURABLE_READ_INVALID",
                "GitHub returned an invalid control-branch blob",
            ) from error

    def compare_and_swap(
        self,
        repository: str,
        branch: str,
        path: str,
        content: bytes,
        *,
        expected_blob_sha: str | None,
        message: str,
    ) -> GitHubContent:
        request: dict[str, Any] = {
            "message": message,
            "content": base64.b64encode(content).decode("ascii"),
            "branch": branch,
        }
        if expected_blob_sha is not None:
            request["sha"] = expected_blob_sha
        result = self._run(
            [
                "api",
                "--method",
                "PUT",
                f"repos/{repository}/contents/{path}",
                "--input",
                "-",
            ],
            input_text=json.dumps(request),
        )
        if result.returncode != 0:
            raise ActivationError(
                "DURABLE_STATE_AMBIGUOUS",
                result.stderr.strip()
                or result.stdout.strip()
                or "GitHub control write acknowledgement is ambiguous",
            )
        readback = self.read(repository, branch, path)
        if readback is None or readback.content != content:
            raise ActivationError(
                "DURABLE_STATE_AMBIGUOUS",
                "GitHub control write did not read back exact bytes",
            )
        return readback


class GitHubDurablePlanControl:
    """Immutable Plan records plus one CAS-updated durable activation log."""

    def __init__(
        self,
        client: GitHubContentClient,
        *,
        branch: str = "gwo-control",
        root: str = ".gwo/v8",
    ):
        if not branch or not root:
            raise ActivationError(
                "DURABLE_CONTROL_CONFIG_INVALID",
                "GitHub control branch and root are required",
            )
        self.client = client
        self.branch = branch
        self.root = root.strip("/")

    def _plan_path(self, plan_digest: str) -> str:
        return f"{self.root}/plans/{plan_digest}.json"

    def _activation_path(self) -> str:
        return f"{self.root}/active-plan.json"

    def plan_record_ref(self, repository: str, plan_digest: str) -> str:
        return (
            f"github://{repository}/refs/heads/{self.branch}/"
            f"{self._plan_path(plan_digest)}"
        )

    @staticmethod
    def _plan_bytes(record: DurablePlanRecord) -> bytes:
        return canonical_bytes(
            {
                "schema_version": 1,
                "repository": record.repository,
                "plan_digest": record.plan_digest,
                "canonical_plan_base64": base64.b64encode(
                    record.canonical_bytes
                ).decode("ascii"),
                "compilation_record": record.compilation_record,
                "record_ref": record.record_ref,
            }
        )

    def publish_plan(self, record: DurablePlanRecord) -> None:
        path = self._plan_path(record.plan_digest)
        expected = self._plan_bytes(record)
        existing = self.client.read(record.repository, self.branch, path)
        if existing is not None:
            if existing.content != expected:
                raise ActivationError(
                    "PLAN_REVISION_CONFLICT",
                    "GitHub Plan Revision content is immutable",
                )
            return
        self.client.compare_and_swap(
            record.repository,
            self.branch,
            path,
            expected,
            expected_blob_sha=None,
            message=f"Publish GWO Plan {record.plan_digest}",
        )

    def read_plan(
        self, repository: str, plan_digest: str
    ) -> DurablePlanRecord | None:
        blob = self.client.read(
            repository,
            self.branch,
            self._plan_path(plan_digest),
        )
        if blob is None:
            return None
        try:
            payload = json.loads(blob.content)
            record = DurablePlanRecord(
                repository=str(payload["repository"]),
                plan_digest=str(payload["plan_digest"]),
                canonical_bytes=base64.b64decode(
                    payload["canonical_plan_base64"],
                    validate=True,
                ),
                compilation_record=payload["compilation_record"],
                record_ref=str(payload["record_ref"]),
            )
        except (KeyError, ValueError, TypeError, json.JSONDecodeError) as error:
            raise ActivationError(
                "PLAN_READBACK_INVALID",
                "GitHub Plan record is malformed",
            ) from error
        if record.repository != repository or record.plan_digest != plan_digest:
            raise ActivationError(
                "PLAN_READBACK_MISMATCH",
                "GitHub Plan record identity does not match its path",
            )
        return record

    def _read_activation_log(
        self,
        repository: str,
    ) -> tuple[dict[str, Any], str | None]:
        blob = self.client.read(
            repository,
            self.branch,
            self._activation_path(),
        )
        if blob is None:
            return (
                {
                    "schema_version": 1,
                    "repository": repository,
                    "active_plan_digest": None,
                    "receipts": [],
                },
                None,
            )
        try:
            payload = json.loads(blob.content)
        except json.JSONDecodeError as error:
            raise ActivationError(
                "ACTIVATION_LOG_INVALID",
                "GitHub activation log is malformed",
            ) from error
        if (
            payload.get("schema_version") != 1
            or payload.get("repository") != repository
            or not isinstance(payload.get("receipts"), list)
        ):
            raise ActivationError(
                "ACTIVATION_LOG_INVALID",
                "GitHub activation log identity is invalid",
            )
        return payload, blob.blob_sha

    def publish_activation(
        self,
        receipt: ActivationReceipt,
        *,
        expected_previous_digest: str | None,
    ) -> None:
        payload, blob_sha = self._read_activation_log(receipt.repository)
        existing = [
            ActivationReceipt.from_dict(item)
            for item in payload["receipts"]
            if isinstance(item, dict)
            and item.get("activation_id") == receipt.activation_id
        ]
        if existing:
            if len(existing) != 1 or existing[0] != receipt:
                raise ActivationError(
                    "ACTIVATION_RECEIPT_IMMUTABLE",
                    "GitHub Activation Receipt cannot be rewritten",
                )
            return
        if payload.get("active_plan_digest") != expected_previous_digest:
            raise ActivationError(
                "ACTIVATION_CONFLICT",
                "GitHub active Plan does not match the expected digest",
            )
        updated = {
            **payload,
            "active_plan_digest": receipt.plan_digest,
            "receipts": [*payload["receipts"], receipt.as_dict()],
        }
        self.client.compare_and_swap(
            receipt.repository,
            self.branch,
            self._activation_path(),
            canonical_bytes(updated),
            expected_blob_sha=blob_sha,
            message=f"Activate GWO Plan {receipt.plan_digest}",
        )

    def read_activation(
        self, repository: str, activation_id: str
    ) -> ActivationReceipt | None:
        payload, _blob_sha = self._read_activation_log(repository)
        matches = [
            ActivationReceipt.from_dict(item)
            for item in payload["receipts"]
            if isinstance(item, dict)
            and item.get("activation_id") == activation_id
        ]
        if len(matches) > 1:
            raise ActivationError(
                "ACTIVATION_LOG_INVALID",
                "GitHub activation log contains duplicate receipt identities",
            )
        return None if not matches else matches[0]

    def read_current_activation(
        self, repository: str
    ) -> ActivationReceipt | None:
        payload, _blob_sha = self._read_activation_log(repository)
        active_digest = payload.get("active_plan_digest")
        if active_digest is None:
            return None
        matches = [
            ActivationReceipt.from_dict(item)
            for item in payload["receipts"]
            if isinstance(item, dict)
            and item.get("plan_digest") == active_digest
        ]
        if len(matches) != 1:
            raise ActivationError(
                "ACTIVATION_LOG_INVALID",
                "GitHub active Plan lacks one exact Activation Receipt",
            )
        return matches[0]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _activation_id(
    repository: str,
    plan_digest: str,
    expected_previous_digest: str | None,
    writer_generation: str,
) -> str:
    identity = {
        "repository": repository,
        "plan_digest": plan_digest,
        "expected_previous_digest": expected_previous_digest,
        "writer_generation": writer_generation,
    }
    return f"activation:{digest_value(identity)[:24]}"


class LocalPlanPublication:
    """Hide durable GitHub commit plus local Store finalization."""

    def __init__(
        self,
        store_path: Path,
        *,
        durable: DurablePlanControl | None = None,
        checkpoint: Callable[[str], None] | None = None,
    ):
        self.store_path = Path(store_path)
        self.durable = durable or InMemoryDurablePlanControl()
        self._checkpoint = checkpoint or (lambda _name: None)
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
                    writer_generation TEXT NOT NULL,
                    activation_id TEXT
                );
                CREATE TABLE IF NOT EXISTS v8_pending_activations (
                    repository TEXT PRIMARY KEY,
                    plan_digest TEXT NOT NULL,
                    expected_previous_digest TEXT,
                    writer_generation TEXT NOT NULL,
                    activation_id TEXT NOT NULL,
                    receipt_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS v8_writer_generations (
                    repository TEXT PRIMARY KEY,
                    writer_generation TEXT NOT NULL
                );
                """
            )
            columns = {
                row["name"]
                for row in connection.execute(
                    "PRAGMA table_info(v8_active_plans)"
                ).fetchall()
            }
            if "activation_id" not in columns:
                connection.execute(
                    "ALTER TABLE v8_active_plans ADD COLUMN activation_id TEXT"
                )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.store_path)
        connection.row_factory = sqlite3.Row
        return connection

    @staticmethod
    def _record_json(compiled_plan: CompiledPlan) -> str:
        return json.dumps(
            compiled_plan.compilation_record,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )

    def _reserve_pending(
        self,
        compiled_plan: CompiledPlan,
        *,
        expected_active_digest: str | None,
        writer_generation: str,
    ) -> ActivationReceipt | None:
        record_json = self._record_json(compiled_plan)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            generation = connection.execute(
                """
                SELECT writer_generation
                FROM v8_writer_generations
                WHERE repository = ?
                """,
                (compiled_plan.repository,),
            ).fetchone()
            if generation is None:
                connection.execute(
                    """
                    INSERT INTO v8_writer_generations (
                        repository,
                        writer_generation
                    ) VALUES (?, ?)
                    """,
                    (compiled_plan.repository, writer_generation),
                )
            elif generation["writer_generation"] != writer_generation:
                raise ActivationError(
                    "WRITER_GENERATION_CONFLICT",
                    "repository lifecycle belongs to another writer generation",
                )

            active = connection.execute(
                """
                SELECT plan_digest, writer_generation, activation_id
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
                return None

            pending = connection.execute(
                """
                SELECT
                    plan_digest,
                    expected_previous_digest,
                    writer_generation,
                    activation_id,
                    receipt_json
                FROM v8_pending_activations
                WHERE repository = ?
                """,
                (compiled_plan.repository,),
            ).fetchone()
            if pending is not None:
                if (
                    pending["plan_digest"] != compiled_plan.digest
                    or pending["expected_previous_digest"]
                    != expected_active_digest
                    or pending["writer_generation"] != writer_generation
                ):
                    raise ActivationError(
                        "ACTIVATION_PENDING_CONFLICT",
                        "another Plan activation is already pending",
                    )
                return ActivationReceipt.from_dict(
                    json.loads(pending["receipt_json"])
                )

            if current_digest != expected_active_digest:
                raise ActivationError(
                    "ACTIVATION_CONFLICT",
                    "active Plan Revision does not match the expected digest",
                )
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
                    "Store Plan Revision content is immutable",
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
            activation_id = _activation_id(
                compiled_plan.repository,
                compiled_plan.digest,
                expected_active_digest,
                writer_generation,
            )
            receipt = ActivationReceipt(
                schema_version=1,
                repository=compiled_plan.repository,
                writer_generation=writer_generation,
                activation_id=activation_id,
                plan_digest=compiled_plan.digest,
                expected_previous_digest=expected_active_digest,
                plan_record_ref=self.durable.plan_record_ref(
                    compiled_plan.repository,
                    compiled_plan.digest,
                ),
                created_at=_now(),
            )
            connection.execute(
                """
                INSERT INTO v8_pending_activations (
                    repository,
                    plan_digest,
                    expected_previous_digest,
                    writer_generation,
                    activation_id,
                    receipt_json
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    compiled_plan.repository,
                    compiled_plan.digest,
                    expected_active_digest,
                    writer_generation,
                    activation_id,
                    canonical_bytes(receipt.as_dict()).decode("utf-8"),
                ),
            )
            return receipt

    def _active_outcome(
        self,
        repository: str,
        plan_digest: str,
        writer_generation: str,
    ) -> ActivationOutcome:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT activation_id
                FROM v8_active_plans
                WHERE repository = ? AND plan_digest = ?
                """,
                (repository, plan_digest),
            ).fetchone()
        if row is None or not row["activation_id"]:
            raise ActivationError(
                "ACTIVATION_RECEIPT_MISSING",
                "active Plan has no durable Activation Receipt identity",
            )
        return ActivationOutcome(
            status="active",
            repository=repository,
            plan_digest=plan_digest,
            writer_generation=writer_generation,
            activation_id=str(row["activation_id"]),
        )

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

        receipt = self._reserve_pending(
            compiled_plan,
            expected_active_digest=expected_active_digest,
            writer_generation=writer_generation,
        )
        if receipt is None:
            return self._active_outcome(
                compiled_plan.repository,
                compiled_plan.digest,
                writer_generation,
            )
        self._checkpoint("pending_reserved")

        durable_record = DurablePlanRecord(
            repository=compiled_plan.repository,
            plan_digest=compiled_plan.digest,
            canonical_bytes=compiled_plan.canonical_bytes,
            compilation_record=compiled_plan.compilation_record,
            record_ref=receipt.plan_record_ref,
        )
        self.durable.publish_plan(durable_record)
        self._checkpoint("plan_published")
        read_plan = self.durable.read_plan(
            compiled_plan.repository,
            compiled_plan.digest,
        )
        if read_plan != durable_record:
            raise ActivationError(
                "PLAN_READBACK_MISMATCH",
                "durable Plan record did not round-trip exact Compiler bytes",
            )
        self._checkpoint("plan_read_back")

        self.durable.publish_activation(
            receipt,
            expected_previous_digest=expected_active_digest,
        )
        self._checkpoint("receipt_published")
        read_receipt = self.durable.read_activation(
            compiled_plan.repository,
            receipt.activation_id,
        )
        if read_receipt != receipt:
            raise ActivationError(
                "ACTIVATION_READBACK_MISMATCH",
                "durable Activation Receipt did not round-trip exactly",
            )
        self._checkpoint("receipt_read_back")

        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            generation = connection.execute(
                """
                SELECT writer_generation
                FROM v8_writer_generations
                WHERE repository = ?
                """,
                (compiled_plan.repository,),
            ).fetchone()
            pending = connection.execute(
                """
                SELECT activation_id, plan_digest, writer_generation
                FROM v8_pending_activations
                WHERE repository = ?
                """,
                (compiled_plan.repository,),
            ).fetchone()
            if (
                generation is None
                or generation["writer_generation"] != writer_generation
                or pending is None
                or pending["activation_id"] != receipt.activation_id
                or pending["plan_digest"] != compiled_plan.digest
                or pending["writer_generation"] != writer_generation
            ):
                raise ActivationError(
                    "ACTIVATION_FINALIZE_CONFLICT",
                    "Store activation reservation changed before finalization",
                )
            connection.execute(
                """
                INSERT INTO v8_active_plans (
                    repository,
                    plan_digest,
                    writer_generation,
                    activation_id
                ) VALUES (?, ?, ?, ?)
                ON CONFLICT(repository) DO UPDATE SET
                    plan_digest = excluded.plan_digest,
                    writer_generation = excluded.writer_generation,
                    activation_id = excluded.activation_id
                """,
                (
                    compiled_plan.repository,
                    compiled_plan.digest,
                    writer_generation,
                    receipt.activation_id,
                ),
            )
            connection.execute(
                """
                DELETE FROM v8_pending_activations
                WHERE repository = ? AND activation_id = ?
                """,
                (compiled_plan.repository, receipt.activation_id),
            )
        return ActivationOutcome(
            status="active",
            repository=compiled_plan.repository,
            plan_digest=compiled_plan.digest,
            writer_generation=writer_generation,
            activation_id=receipt.activation_id,
        )

    def read_active(self, repository: str) -> PublishedPlan | None:
        with self._connect() as connection:
            pending = connection.execute(
                """
                SELECT activation_id
                FROM v8_pending_activations
                WHERE repository = ?
                """,
                (repository,),
            ).fetchone()
            if pending is not None:
                raise ActivationError(
                    "ACTIVATION_PENDING",
                    "new Admissions are fenced until durable activation finalizes",
                )
            row = connection.execute(
                """
                SELECT
                    revisions.plan_digest,
                    revisions.canonical_bytes,
                    revisions.compilation_record,
                    active.writer_generation,
                    active.activation_id
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
        if not row["activation_id"]:
            raise ActivationError(
                "ACTIVATION_RECEIPT_MISSING",
                "active Plan lacks a durable receipt identity",
            )
        published = PublishedPlan(
            repository=repository,
            plan_digest=str(row["plan_digest"]),
            canonical_bytes=bytes(row["canonical_bytes"]),
            compilation_record=json.loads(row["compilation_record"]),
            writer_generation=str(row["writer_generation"]),
            activation_id=str(row["activation_id"]),
        )
        self.assert_writer(
            repository,
            writer_generation=published.writer_generation,
            plan_digest=published.plan_digest,
            activation_id=published.activation_id,
        )
        return published

    def assert_writer(
        self,
        repository: str,
        *,
        writer_generation: str,
        plan_digest: str,
        activation_id: str,
    ) -> None:
        durable = self.durable.read_current_activation(repository)
        if (
            durable is None
            or durable.writer_generation != writer_generation
            or durable.plan_digest != plan_digest
            or durable.activation_id != activation_id
        ):
            raise ActivationError(
                "WRITER_GENERATION_FENCED",
                "durable control no longer authorizes this writer and Plan",
            )
