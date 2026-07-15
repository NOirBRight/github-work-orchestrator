#!/usr/bin/env python3
"""Fail-closed host-wide singleflight for sidebar Task creation."""

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


SCHEMA_VERSION = 1
DEFAULT_TTL_SECONDS = 15 * 60
ACTIVE_FILE = "active-task-creation.json"
LOCK_FILE = "task-creation.lock"

STATES = {
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
TERMINAL_STATES = {"activated", "failed", "cancelled"}
TRANSITIONS = {
    "reserved": {"invoking", "failed", "cancelled"},
    "invoking": {"queued", "creation-unknown", "failed", "cancelled"},
    "queued": {
        "worktree-creating",
        "task-materialized",
        "creation-unknown",
        "failed",
        "cancelled",
    },
    "worktree-creating": {
        "task-materialized",
        "creation-unknown",
        "failed",
        "cancelled",
    },
    "task-materialized": {"bootstrap-ready", "failed", "cancelled"},
    "bootstrap-ready": {"preflight-ready", "failed", "cancelled"},
    "preflight-ready": {"activated", "failed", "cancelled"},
    "activated": set(),
    "creation-unknown": set(),
    "failed": set(),
    "cancelled": set(),
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
    """One active Task-creation lease for the whole local Codex host."""

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
            handle.seek(0)
            try:
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as error:
                raise LeaseError(
                    "HOST_SINGLEFLIGHT_BUSY",
                    "another process is updating the host Task-creation lease",
                ) from error
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
                "the host Task-creation lease is unreadable; reconcile manually",
            ) from error
        if record.get("schema_version") != SCHEMA_VERSION:
            raise LeaseError(
                "LEASE_STATE_UNSUPPORTED",
                "the host Task-creation lease schema is unsupported",
            )
        if record.get("state") not in STATES:
            raise LeaseError(
                "LEASE_STATE_UNREADABLE",
                "the host Task-creation lease has an invalid state",
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
        if not required.issubset(record) or any(
            not isinstance(record[key], str)
            or not record[key]
            for key in (
                "lease_id",
                "idempotency_key",
                "repository",
                "issue",
                "branch",
                "owner_token_sha256",
            )
        ):
            raise LeaseError(
                "LEASE_STATE_UNREADABLE",
                "the host Task-creation lease is missing required identity fields",
            )
        if not isinstance(record["revision"], int) or record["revision"] < 1:
            raise LeaseError(
                "LEASE_STATE_UNREADABLE",
                "the host Task-creation lease has an invalid revision",
            )
        if not isinstance(record["ttl_seconds"], int) or record["ttl_seconds"] <= 0:
            raise LeaseError(
                "LEASE_STATE_UNREADABLE",
                "the host Task-creation lease has an invalid ttl",
            )
        try:
            for key in ("created_at", "updated_at", "expires_at"):
                float(record[key])
        except (TypeError, ValueError) as error:
            raise LeaseError(
                "LEASE_STATE_UNREADABLE",
                "the host Task-creation lease has invalid timestamps",
            ) from error
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
        return {
            key: value
            for key, value in record.items()
            if key != "owner_token_sha256"
        } | {
            "idempotent": idempotent,
            "creation_authorized": creation_authorized,
        }

    @staticmethod
    def _require_owner(record: dict, owner_token: str) -> None:
        if not secrets.compare_digest(
            record["owner_token_sha256"], _token_digest(owner_token)
        ):
            raise LeaseError(
                "OWNER_MISMATCH", "owner token does not own the active lease"
            )

    @staticmethod
    def _require_unexpired(record: dict, current_time: float) -> None:
        if current_time >= float(record["expires_at"]):
            raise LeaseError(
                "EXPIRED_CREATION_REQUIRES_RECONCILIATION",
                "the active lease expired; restart and reconcile the exact "
                "request, Task, and worktree before any mutation",
            )

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
                self._require_unexpired(active, current_time)
                if (
                    active["idempotency_key"] == key
                    and secrets.compare_digest(
                        active["owner_token_sha256"], owner_digest
                    )
                ):
                    return self._public(active, idempotent=True)
                raise LeaseError(
                    "ACTIVE_CREATION_EXISTS",
                    "another Task creation owns the host-wide singleflight lease",
                )

            record = {
                "schema_version": SCHEMA_VERSION,
                "lease_id": uuid.uuid4().hex,
                "idempotency_key": key,
                **identity,
                "owner_token_sha256": owner_digest,
                "state": "reserved",
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

    def transition(
        self,
        owner_token: str,
        new_state: str,
        *,
        expected_state: str | None = None,
        request_id: str | None = None,
        recovery_path: bool = False,
        now: float | None = None,
    ) -> dict:
        if new_state not in STATES:
            raise LeaseError("INVALID_STATE", f"unknown state: {new_state}")
        current_time = time.time() if now is None else float(now)
        with self._locked():
            record = self._read()
            if record is None:
                raise LeaseError("NO_ACTIVE_LEASE", "no Task-creation lease exists")
            self._require_owner(record, owner_token)
            self._require_unexpired(record, current_time)
            current_state = record["state"]
            request_digest = None
            if request_id:
                request_digest = hashlib.sha256(request_id.encode("utf-8")).hexdigest()
            if new_state == "queued" and request_digest is None:
                raise LeaseError(
                    "REQUEST_ID_REQUIRED",
                    "queued state requires the exact native client request identity",
                )
            existing_request_digest = record.get("request_id_sha256")
            if (
                request_digest is not None
                and existing_request_digest is not None
                and not secrets.compare_digest(
                    existing_request_digest, request_digest
                )
            ):
                raise LeaseError(
                    "REQUEST_ID_MISMATCH",
                    "request identity does not match the active creation",
                )
            if expected_state is not None and current_state != expected_state:
                raise LeaseError(
                    "STATE_MISMATCH",
                    f"expected {expected_state}, found {current_state}",
                )
            if current_state == new_state:
                return self._public(record, idempotent=True)
            recovery_transition = (
                current_state == "task-materialized"
                and new_state == "preflight-ready"
            )
            if recovery_path and not recovery_transition:
                raise LeaseError(
                    "INVALID_RECOVERY_TRANSITION",
                    "recovery_path is limited to task-materialized -> preflight-ready",
                )
            if new_state not in TRANSITIONS[current_state] and not (
                recovery_transition and recovery_path
            ):
                raise LeaseError(
                    "INVALID_TRANSITION",
                    f"cannot transition from {current_state} to {new_state}",
                )
            record["state"] = new_state
            record["updated_at"] = current_time
            record["expires_at"] = current_time + record["ttl_seconds"]
            record["revision"] += 1
            if new_state == "queued":
                record["request_id_sha256"] = request_digest
            if recovery_transition:
                record["recovery_path"] = True
            self._write(record)
            return self._public(record)

    def reconcile(
        self,
        owner_token: str,
        *,
        host_restarted: bool,
        request_id: str | None,
        request_state: str,
        task_state: str,
        worktree_state: str,
        evidence: str,
        now: float | None = None,
    ) -> dict:
        current_time = time.time() if now is None else float(now)
        with self._locked():
            record = self._read()
            if record is None:
                raise LeaseError("NO_ACTIVE_LEASE", "no Task-creation lease exists")
            self._require_owner(record, owner_token)
            if record["state"] in TERMINAL_STATES:
                raise LeaseError(
                    "RECONCILIATION_NOT_REQUIRED",
                    "terminal leases are immutable and may only be released by their owner",
                )
            expired = current_time >= float(record["expires_at"])
            if record["state"] not in {"invoking", "creation-unknown"} and not expired:
                raise LeaseError(
                    "RECONCILIATION_NOT_REQUIRED",
                    "only invoking, creation-unknown, or expired leases may be reconciled",
                )
            if not host_restarted or not evidence.strip():
                raise LeaseError(
                    "RECONCILIATION_EVIDENCE_REQUIRED",
                    "restart confirmation and a private evidence reference are required",
                )
            stored_request_digest = record.get("request_id_sha256")
            if stored_request_digest is not None:
                if not request_id:
                    raise LeaseError(
                        "REQUEST_ID_REQUIRED",
                        "reconciliation requires the exact original request identity",
                    )
                request_digest = hashlib.sha256(
                    request_id.encode("utf-8")
                ).hexdigest()
                if not secrets.compare_digest(stored_request_digest, request_digest):
                    raise LeaseError(
                        "REQUEST_ID_MISMATCH",
                        "reconciliation request does not match the active creation",
                    )
            elif request_id:
                raise LeaseError(
                    "REQUEST_ID_MISMATCH",
                    "the active creation has no native request receipt to match",
                )

            terminal_request_states = {"cancelled", "failed", "terminal-no-task"}
            if request_state == "no-receipt-terminal" and stored_request_digest is None:
                if task_state != "absent" or worktree_state not in {
                    "absent",
                    "clean-orphan",
                }:
                    raise LeaseError(
                        "RECONCILIATION_EVIDENCE_REQUIRED",
                        "no-receipt terminal recovery requires no Task and an "
                        "absent or clean orphan worktree",
                    )
                reconciled_state = "failed"
            elif (
                request_state == "no-receipt-materialized"
                and stored_request_digest is None
            ):
                if task_state != "materialized" or worktree_state != "owned":
                    raise LeaseError(
                        "RECONCILIATION_EVIDENCE_REQUIRED",
                        "no-receipt recovery requires one exact real Task and owned worktree",
                    )
                reconciled_state = "task-materialized"
            elif request_state in terminal_request_states and stored_request_digest:
                if task_state != "absent" or worktree_state not in {
                    "absent",
                    "clean-orphan",
                }:
                    raise LeaseError(
                        "RECONCILIATION_EVIDENCE_REQUIRED",
                        "terminal recovery requires no real Task and an absent "
                        "or clean orphan worktree",
                    )
                reconciled_state = (
                    "cancelled" if request_state == "cancelled" else "failed"
                )
            elif request_state == "materialized" and stored_request_digest:
                if task_state != "materialized" or worktree_state != "owned":
                    raise LeaseError(
                        "RECONCILIATION_EVIDENCE_REQUIRED",
                        "materialized recovery requires the exact real Task and owned worktree",
                    )
                reconciled_state = "task-materialized"
            else:
                raise LeaseError(
                    "RECONCILIATION_EVIDENCE_REQUIRED",
                    "ambiguous request, Task, or worktree state cannot clear the lease",
                )

            record["state"] = reconciled_state
            record["updated_at"] = current_time
            record["expires_at"] = current_time + record["ttl_seconds"]
            record["revision"] += 1
            record["reconciliation"] = {
                "host_restarted": True,
                "request_state": request_state,
                "task_state": task_state,
                "worktree_state": worktree_state,
                "evidence_sha256": hashlib.sha256(
                    evidence.encode("utf-8")
                ).hexdigest(),
            }
            self._write(record)
            return self._public(record)

    def release(self, owner_token: str, *, now: float | None = None) -> dict:
        current_time = time.time() if now is None else float(now)
        with self._locked():
            record = self._read()
            if record is None:
                raise LeaseError("NO_ACTIVE_LEASE", "no Task-creation lease exists")
            self._require_owner(record, owner_token)
            if record["state"] not in TERMINAL_STATES:
                self._require_unexpired(record, current_time)
                raise LeaseError(
                    "NON_TERMINAL_LEASE",
                    "release requires activated, failed, or cancelled state",
                )
            record["released_at"] = current_time
            self.active_path.unlink()
            return self._public(record)


def _owner_token(arguments: argparse.Namespace, *, generate: bool = False) -> str:
    token = arguments.owner_token or os.environ.get(
        "GITHUB_WORK_ORCHESTRATOR_CREATION_OWNER_TOKEN"
    )
    if token:
        return token
    if generate:
        return secrets.token_urlsafe(24)
    raise LeaseError(
        "OWNER_TOKEN_REQUIRED",
        "set --owner-token or GITHUB_WORK_ORCHESTRATOR_CREATION_OWNER_TOKEN",
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", type=Path, default=_default_state_dir())
    subparsers = parser.add_subparsers(dest="command", required=True)

    reserve = subparsers.add_parser("reserve")
    reserve.add_argument("--repository", required=True)
    reserve.add_argument("--issue", required=True)
    reserve.add_argument("--branch", required=True)
    reserve.add_argument("--owner-token")
    reserve.add_argument("--ttl-seconds", type=int, default=DEFAULT_TTL_SECONDS)

    transition = subparsers.add_parser("transition")
    transition.add_argument("--owner-token")
    transition.add_argument("--state", choices=sorted(STATES), required=True)
    transition.add_argument("--expected-state", choices=sorted(STATES))
    transition.add_argument("--request-id")
    transition.add_argument("--recovery-path", action="store_true")

    reconcile = subparsers.add_parser("reconcile")
    reconcile.add_argument("--owner-token")
    reconcile.add_argument("--host-restarted", action="store_true")
    reconcile.add_argument("--request-id")
    reconcile.add_argument("--request-state", required=True)
    reconcile.add_argument("--task-state", required=True)
    reconcile.add_argument("--worktree-state", required=True)
    reconcile.add_argument("--evidence", required=True)

    release = subparsers.add_parser("release")
    release.add_argument("--owner-token")

    subparsers.add_parser("inspect")
    return parser


def main() -> int:
    arguments = _parser().parse_args()
    store = LeaseStore(arguments.state_dir)
    try:
        if arguments.command == "reserve":
            token = _owner_token(arguments, generate=True)
            result = store.reserve(
                repository=arguments.repository,
                issue=arguments.issue,
                branch=arguments.branch,
                owner_token=token,
                ttl_seconds=arguments.ttl_seconds,
            )
            result["owner_token"] = token
        elif arguments.command == "transition":
            result = store.transition(
                _owner_token(arguments),
                arguments.state,
                expected_state=arguments.expected_state,
                request_id=arguments.request_id,
                recovery_path=arguments.recovery_path,
            )
        elif arguments.command == "reconcile":
            result = store.reconcile(
                _owner_token(arguments),
                host_restarted=arguments.host_restarted,
                request_id=arguments.request_id,
                request_state=arguments.request_state,
                task_state=arguments.task_state,
                worktree_state=arguments.worktree_state,
                evidence=arguments.evidence,
            )
        elif arguments.command == "release":
            result = store.release(_owner_token(arguments))
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
