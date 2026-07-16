#!/usr/bin/env python3
"""Minimal fail-closed singleflight for sidebar-visible Task creation."""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import secrets
import sys
import tempfile
import time
import uuid
from pathlib import Path
from typing import Iterator


SCHEMA_VERSION = 2
LEGACY_SCHEMA_VERSION = 1
DEFAULT_TTL_SECONDS = 5 * 60
ACTIVE_FILE = "active-task-creation.json"
LOCK_FILE = "task-creation.lock"
STATES = {"creating", "uncertain"}
LEGACY_STATES = {
    "reserved",
    "invoking",
    "queued",
    "worktree-creating",
    "task-materialized",
    "bootstrap-ready",
    "preflight-ready",
    "activated",
    "creation-unknown",
    "failed",
    "cancelled",
}
OUTCOMES = {
    "task-materialized",
    "terminal-no-task",
    "cancelled-before-invoke",
}


class LeaseError(RuntimeError):
    """A fail-closed lease result with a stable machine-readable code."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _token_digest(token: str) -> str:
    if not token or not token.strip():
        raise LeaseError("OWNER_TOKEN_REQUIRED", "owner token must be non-empty")
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _identity_digest(value: str) -> str:
    if not value or not value.strip():
        raise LeaseError("IDENTITY_REQUIRED", "identity must be non-empty")
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _normalize_repository(repository: str) -> str:
    normalized = repository.strip().replace("\\", "/").removesuffix(".git")
    if normalized.startswith("https://github.com/"):
        normalized = normalized.removeprefix("https://github.com/")
    if normalized.startswith("git@github.com:"):
        normalized = normalized.removeprefix("git@github.com:")
    normalized = normalized.strip("/").lower()
    if normalized.count("/") != 1 or any(not part for part in normalized.split("/")):
        raise LeaseError(
            "INVALID_IDEMPOTENCY_INPUT",
            "repository must be a canonical owner/name GitHub repository",
        )
    return normalized


def _normalize_issue(issue: object) -> str:
    normalized = str(issue).strip().removeprefix("#")
    if not normalized.isdigit() or int(normalized) <= 0:
        raise LeaseError(
            "INVALID_IDEMPOTENCY_INPUT", "issue must be a positive number"
        )
    return str(int(normalized))


def _normalize_branch(branch: str) -> str:
    normalized = branch.strip()
    if not normalized or normalized.startswith("-") or ".." in normalized:
        raise LeaseError(
            "INVALID_IDEMPOTENCY_INPUT", "branch must be a non-empty Git ref name"
        )
    return normalized


def idempotency_key(repository: str, issue: object, branch: str) -> tuple[str, dict]:
    identity = {
        "repository": _normalize_repository(repository),
        "issue": _normalize_issue(issue),
        "branch": _normalize_branch(branch),
    }
    canonical = json.dumps(identity, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest(), identity


def _default_state_dir() -> Path:
    if os.name == "nt":
        root = Path(
            os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")
        )
    elif sys.platform == "darwin":
        root = Path.home() / "Library" / "Application Support"
    else:
        root = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state"))
    return root / "Codex" / "github-work-orchestrator" / "task-creation"


class LeaseStore:
    """One visible-Task creation admission record for the local OS user."""

    def __init__(self, state_dir: Path | str | None = None):
        self.state_dir = Path(state_dir) if state_dir else _default_state_dir()
        self.active_path = self.state_dir / ACTIVE_FILE
        self.lock_path = self.state_dir / LOCK_FILE

    @contextlib.contextmanager
    def _locked(self) -> Iterator[None]:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        handle = self.lock_path.open("a+b")
        try:
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"\0")
                handle.flush()
            deadline = time.monotonic() + 2.0
            while True:
                handle.seek(0)
                try:
                    if os.name == "nt":
                        import msvcrt

                        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl

                        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except OSError as error:
                    if time.monotonic() >= deadline:
                        raise LeaseError(
                            "HOST_SINGLEFLIGHT_BUSY",
                            "another process is updating the Task-creation guard",
                        ) from error
                    time.sleep(0.01)
            try:
                yield
            finally:
                handle.seek(0)
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()

    def _read(self) -> dict | None:
        if not self.active_path.is_file():
            return None
        try:
            record = json.loads(self.active_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise LeaseError(
                "LEASE_STATE_UNREADABLE",
                "the Task-creation guard is unreadable; reconcile without rewriting it",
            ) from error
        schema = record.get("schema_version")
        if schema not in {SCHEMA_VERSION, LEGACY_SCHEMA_VERSION}:
            raise LeaseError(
                "LEASE_STATE_UNSUPPORTED",
                "the Task-creation guard schema is unsupported",
            )
        valid_states = STATES if schema == SCHEMA_VERSION else LEGACY_STATES
        if record.get("state") not in valid_states:
            raise LeaseError(
                "LEASE_STATE_UNREADABLE",
                "the Task-creation guard has an invalid state",
            )
        required = {
            "lease_id",
            "idempotency_key",
            "repository",
            "issue",
            "branch",
            "owner_token_sha256",
            "revision",
            "created_at",
            "updated_at",
            "expires_at",
            "ttl_seconds",
        }
        if not required.issubset(record):
            raise LeaseError(
                "LEASE_STATE_UNREADABLE",
                "the Task-creation guard is missing required identity fields",
            )
        for key in (
            "lease_id",
            "idempotency_key",
            "repository",
            "issue",
            "branch",
            "owner_token_sha256",
        ):
            if not isinstance(record[key], str) or not record[key]:
                raise LeaseError(
                    "LEASE_STATE_UNREADABLE",
                    "the Task-creation guard has an invalid identity field",
                )
        if not isinstance(record["revision"], int) or record["revision"] < 1:
            raise LeaseError("LEASE_STATE_UNREADABLE", "invalid guard revision")
        if not isinstance(record["ttl_seconds"], int) or record["ttl_seconds"] <= 0:
            raise LeaseError("LEASE_STATE_UNREADABLE", "invalid guard ttl")
        try:
            for key in ("created_at", "updated_at", "expires_at"):
                float(record[key])
        except (TypeError, ValueError) as error:
            raise LeaseError("LEASE_STATE_UNREADABLE", "invalid guard timestamp") from error
        return record

    def _write(self, record: dict) -> None:
        payload = (json.dumps(record, indent=2, sort_keys=True) + "\n").encode(
            "utf-8"
        )
        descriptor, temporary_name = tempfile.mkstemp(
            prefix="task-creation-", suffix=".tmp", dir=self.state_dir
        )
        try:
            with os.fdopen(descriptor, "wb") as temporary:
                temporary.write(payload)
                temporary.flush()
                os.fsync(temporary.fileno())
            os.replace(temporary_name, self.active_path)
        finally:
            try:
                os.unlink(temporary_name)
            except FileNotFoundError:
                pass

    @staticmethod
    def _public(
        record: dict,
        *,
        idempotent: bool = False,
        creation_authorized: bool = False,
    ) -> dict:
        result = {
            key: value
            for key, value in record.items()
            if key != "owner_token_sha256"
        }
        result.update(
            idempotent=idempotent,
            creation_authorized=creation_authorized,
        )
        if record.get("schema_version") == LEGACY_SCHEMA_VERSION:
            result["legacy"] = True
        return result

    @staticmethod
    def _require_owner(record: dict, owner_token: str) -> None:
        if not secrets.compare_digest(
            record["owner_token_sha256"], _token_digest(owner_token)
        ):
            raise LeaseError(
                "OWNER_MISMATCH", "owner token does not own the active guard"
            )

    @staticmethod
    def _request_digest(record: dict, request_id: str | None) -> str | None:
        supplied = _identity_digest(request_id) if request_id else None
        stored = record.get("request_id_sha256")
        if stored is not None and supplied is None:
            raise LeaseError(
                "REQUEST_ID_REQUIRED",
                "the exact original native request identity is required",
            )
        if stored is None and supplied is not None:
            raise LeaseError(
                "REQUEST_ID_MISMATCH",
                "the active creation has no request receipt to match",
            )
        if stored is not None and not secrets.compare_digest(stored, supplied):
            raise LeaseError(
                "REQUEST_ID_MISMATCH",
                "request identity does not match the active creation",
            )
        return supplied

    @staticmethod
    def _validate_disposition(
        record: dict,
        *,
        outcome: str,
        task_id: str | None,
        worktree_state: str,
        evidence: str,
    ) -> dict:
        if outcome not in OUTCOMES:
            raise LeaseError("INVALID_OUTCOME", f"unsupported outcome: {outcome}")
        if not evidence or not evidence.strip():
            raise LeaseError(
                "RECONCILIATION_EVIDENCE_REQUIRED",
                "a private exact Task/worktree evidence reference is required",
            )
        if outcome == "task-materialized":
            if not task_id or worktree_state != "owned":
                raise LeaseError(
                    "RECONCILIATION_EVIDENCE_REQUIRED",
                    "materialization requires one exact Task and its owned worktree",
                )
        elif outcome == "terminal-no-task":
            if task_id or worktree_state not in {"absent", "clean-orphan"}:
                raise LeaseError(
                    "RECONCILIATION_EVIDENCE_REQUIRED",
                    "terminal no-Task evidence requires no Task and a safe worktree",
                )
        else:
            if (
                record.get("schema_version") != SCHEMA_VERSION
                or record.get("state") != "creating"
                or record.get("request_id_sha256") is not None
                or task_id
                or worktree_state not in {"absent", "clean-orphan"}
            ):
                raise LeaseError(
                    "RECONCILIATION_EVIDENCE_REQUIRED",
                    "pre-invocation cancellation requires no request, Task, or WIP",
                )
        result = {
            "outcome": outcome,
            "worktree_state": worktree_state,
            "evidence_sha256": hashlib.sha256(evidence.encode("utf-8")).hexdigest(),
        }
        if task_id:
            result["task_id_sha256"] = _identity_digest(task_id)
        return result

    def reserve(
        self,
        *,
        repository: str,
        issue: object,
        branch: str,
        owner_token: str,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
        now: float | None = None,
    ) -> dict:
        if ttl_seconds <= 0:
            raise LeaseError("INVALID_TTL", "ttl_seconds must be positive")
        current_time = time.time() if now is None else float(now)
        key, identity = idempotency_key(repository, issue, branch)
        owner_digest = _token_digest(owner_token)
        with self._locked():
            active = self._read()
            if active is not None:
                same_owner_and_key = (
                    active["idempotency_key"] == key
                    and secrets.compare_digest(
                        active["owner_token_sha256"], owner_digest
                    )
                )
                expired = current_time >= float(active["expires_at"])
                if (
                    active.get("schema_version") == SCHEMA_VERSION
                    and active["state"] == "creating"
                    and not expired
                    and same_owner_and_key
                ):
                    return self._public(active, idempotent=True)
                if (
                    active.get("schema_version") == LEGACY_SCHEMA_VERSION
                    or active["state"] == "uncertain"
                    or expired
                ):
                    raise LeaseError(
                        "RECONCILIATION_REQUIRED",
                        "the prior visible-Task creation must be reconciled; other lanes remain available",
                    )
                raise LeaseError(
                    "ACTIVE_CREATION_EXISTS",
                    "another visible-Task creation owns the host singleflight",
                )
            record = {
                "schema_version": SCHEMA_VERSION,
                "lease_id": uuid.uuid4().hex,
                "idempotency_key": key,
                **identity,
                "owner_token_sha256": owner_digest,
                "state": "creating",
                "revision": 1,
                "created_at": current_time,
                "updated_at": current_time,
                "expires_at": current_time + ttl_seconds,
                "ttl_seconds": ttl_seconds,
            }
            self._write(record)
            return self._public(record, creation_authorized=True)

    def inspect(self) -> dict | None:
        with self._locked():
            record = self._read()
            return None if record is None else self._public(record)

    def record_request(
        self, owner_token: str, request_id: str, *, now: float | None = None
    ) -> dict:
        current_time = time.time() if now is None else float(now)
        request_digest = _identity_digest(request_id)
        with self._locked():
            record = self._read()
            if record is None:
                raise LeaseError("NO_ACTIVE_LEASE", "no Task-creation guard exists")
            self._require_owner(record, owner_token)
            if record.get("schema_version") != SCHEMA_VERSION:
                raise LeaseError("RECONCILIATION_REQUIRED", "legacy guard must be reconciled")
            if record["state"] != "creating":
                raise LeaseError("RECONCILIATION_REQUIRED", "uncertain creation must be reconciled")
            existing = record.get("request_id_sha256")
            if existing and not secrets.compare_digest(existing, request_digest):
                raise LeaseError("REQUEST_ID_MISMATCH", "request identity changed")
            if existing:
                return self._public(record, idempotent=True)
            record["request_id_sha256"] = request_digest
            record["revision"] += 1
            record["updated_at"] = current_time
            self._write(record)
            return self._public(record)

    def mark_uncertain(
        self,
        owner_token: str,
        *,
        request_id: str | None = None,
        now: float | None = None,
    ) -> dict:
        current_time = time.time() if now is None else float(now)
        with self._locked():
            record = self._read()
            if record is None:
                raise LeaseError("NO_ACTIVE_LEASE", "no Task-creation guard exists")
            self._require_owner(record, owner_token)
            if record.get("schema_version") != SCHEMA_VERSION:
                raise LeaseError("RECONCILIATION_REQUIRED", "legacy guard must be reconciled")
            if request_id:
                request_digest = _identity_digest(request_id)
                existing = record.get("request_id_sha256")
                if existing and not secrets.compare_digest(existing, request_digest):
                    raise LeaseError("REQUEST_ID_MISMATCH", "request identity changed")
                record["request_id_sha256"] = request_digest
            if record["state"] == "uncertain":
                return self._public(record, idempotent=True)
            record["state"] = "uncertain"
            record["revision"] += 1
            record["updated_at"] = current_time
            self._write(record)
            return self._public(record)

    def release(
        self,
        owner_token: str | None,
        *,
        request_id: str | None = None,
        outcome: str,
        task_id: str | None,
        worktree_state: str,
        evidence: str,
        now: float | None = None,
    ) -> dict:
        current_time = time.time() if now is None else float(now)
        with self._locked():
            record = self._read()
            if record is None:
                raise LeaseError("NO_ACTIVE_LEASE", "no Task-creation guard exists")
            request_authenticated = False
            if owner_token:
                self._require_owner(record, owner_token)
                if request_id is not None:
                    self._request_digest(record, request_id)
                if (
                    record.get("schema_version") != SCHEMA_VERSION
                    or record["state"] == "uncertain"
                    or current_time >= float(record["expires_at"])
                ):
                    raise LeaseError(
                        "RECONCILIATION_REQUIRED",
                        "uncertain, expired, or legacy creation requires one post-restart reconciliation",
                    )
            else:
                if outcome != "task-materialized":
                    raise LeaseError(
                        "OWNER_TOKEN_REQUIRED",
                        "only an exact recorded native request can release a materialized Task without the owner token",
                    )
                if record.get("schema_version") != SCHEMA_VERSION:
                    raise LeaseError(
                        "OWNER_TOKEN_REQUIRED",
                        "legacy creation recovery still requires the original owner token",
                    )
                if not request_id:
                    raise LeaseError(
                        "REQUEST_ID_REQUIRED",
                        "the exact original native request identity is required",
                    )
                self._request_digest(record, request_id)
                request_authenticated = True
            disposition = self._validate_disposition(
                record,
                outcome=outcome,
                task_id=task_id,
                worktree_state=worktree_state,
                evidence=evidence,
            )
            result = self._public(record) | disposition | {"released_at": current_time}
            if request_authenticated:
                result["request_authenticated"] = True
            self.active_path.unlink()
            return result

    def reconcile(
        self,
        owner_token: str,
        *,
        host_restarted: bool,
        request_id: str | None,
        outcome: str,
        task_id: str | None,
        worktree_state: str,
        evidence: str,
        now: float | None = None,
    ) -> dict:
        current_time = time.time() if now is None else float(now)
        with self._locked():
            record = self._read()
            if record is None:
                raise LeaseError("NO_ACTIVE_LEASE", "no Task-creation guard exists")
            self._require_owner(record, owner_token)
            expired = current_time >= float(record["expires_at"])
            if not host_restarted or not evidence or not evidence.strip():
                raise LeaseError(
                    "RECONCILIATION_EVIDENCE_REQUIRED",
                    "one host restart and private exact evidence are required",
                )
            if (
                record.get("schema_version") == SCHEMA_VERSION
                and record["state"] != "uncertain"
                and not expired
            ):
                raise LeaseError(
                    "RECONCILIATION_NOT_REQUIRED",
                    "a nonexpired creating guard can finish through normal release",
                )
            self._request_digest(record, request_id)
            if outcome == "cancelled-before-invoke":
                raise LeaseError(
                    "INVALID_OUTCOME",
                    "post-restart reconciliation must prove Task materialization or terminal no-Task",
                )
            disposition = self._validate_disposition(
                record,
                outcome=outcome,
                task_id=task_id,
                worktree_state=worktree_state,
                evidence=evidence,
            )
            result = self._public(record) | disposition | {
                "released_at": current_time,
                "reconciled_after_restart": True,
            }
            self.active_path.unlink()
            return result


def _owner_token(
    arguments: argparse.Namespace, *, required: bool = True
) -> str | None:
    token = arguments.owner_token or os.environ.get(
        "GITHUB_WORK_ORCHESTRATOR_CREATION_OWNER_TOKEN"
    )
    if token:
        return token
    if not required:
        return None
    raise LeaseError(
        "OWNER_TOKEN_REQUIRED",
        "generate and retain an owner token before reserve, then pass --owner-token",
    )


def _add_owner(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--owner-token")


def _add_disposition(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--outcome", choices=sorted(OUTCOMES), required=True)
    parser.add_argument("--task-id")
    parser.add_argument("--worktree-state", required=True)
    parser.add_argument("--evidence", required=True)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", type=Path, default=_default_state_dir())
    subparsers = parser.add_subparsers(dest="command", required=True)

    reserve = subparsers.add_parser("reserve")
    reserve.add_argument("--repository", required=True)
    reserve.add_argument("--issue", required=True)
    reserve.add_argument("--branch", required=True)
    _add_owner(reserve)
    reserve.add_argument("--ttl-seconds", type=int, default=DEFAULT_TTL_SECONDS)

    request = subparsers.add_parser("record-request")
    _add_owner(request)
    request.add_argument("--request-id", required=True)

    uncertain = subparsers.add_parser("uncertain")
    _add_owner(uncertain)
    uncertain.add_argument("--request-id")

    release = subparsers.add_parser("release")
    _add_owner(release)
    release.add_argument("--request-id")
    _add_disposition(release)

    reconcile = subparsers.add_parser("reconcile")
    _add_owner(reconcile)
    reconcile.add_argument("--host-restarted", action="store_true")
    reconcile.add_argument("--request-id")
    _add_disposition(reconcile)

    subparsers.add_parser("inspect")
    return parser


def main() -> int:
    arguments = _parser().parse_args()
    store = LeaseStore(arguments.state_dir)
    try:
        if arguments.command == "reserve":
            result = store.reserve(
                repository=arguments.repository,
                issue=arguments.issue,
                branch=arguments.branch,
                owner_token=_owner_token(arguments),
                ttl_seconds=arguments.ttl_seconds,
            )
        elif arguments.command == "record-request":
            result = store.record_request(
                _owner_token(arguments), arguments.request_id
            )
        elif arguments.command == "uncertain":
            result = store.mark_uncertain(
                _owner_token(arguments), request_id=arguments.request_id
            )
        elif arguments.command == "release":
            result = store.release(
                _owner_token(arguments, required=False),
                request_id=arguments.request_id,
                outcome=arguments.outcome,
                task_id=arguments.task_id,
                worktree_state=arguments.worktree_state,
                evidence=arguments.evidence,
            )
        elif arguments.command == "reconcile":
            result = store.reconcile(
                _owner_token(arguments),
                host_restarted=arguments.host_restarted,
                request_id=arguments.request_id,
                outcome=arguments.outcome,
                task_id=arguments.task_id,
                worktree_state=arguments.worktree_state,
                evidence=arguments.evidence,
            )
        else:
            result = store.inspect()
        print(json.dumps({"ok": True, "lease": result}, sort_keys=True))
        return 0
    except LeaseError as error:
        print(
            json.dumps(
                {"ok": False, "error": error.code, "message": str(error)},
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
