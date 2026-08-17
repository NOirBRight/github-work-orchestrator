"""One-shot, run-root-bound fault injection for the V8 root Canary.

The proxy is deliberately a small durable adapter.  It owns neither Campaign
state nor workflow transitions; it only records an exact command response and
can terminate once after that response is durable.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import threading
import time
from typing import Callable, Iterator, Mapping


_DIGEST_RE = re.compile(r"[0-9a-f]{64}\Z")
_REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
_JOURNAL_LOCKS_GUARD = threading.Lock()
_JOURNAL_LOCKS: dict[str, threading.RLock] = {}


@dataclass(frozen=True, slots=True)
class FaultRequest:
    role: str
    point: str
    stable_action_id: str
    payload_digest: str
    command: tuple[str, ...]
    # The default preserves the old positional constructor shape.  FaultProxy
    # execution rejects the empty compatibility value; production always
    # supplies the active Plan Revision digest and persists it in replay.
    plan_revision_digest: str = ""

    def __post_init__(self) -> None:
        for value, label in (
            (self.role, "role"),
            (self.point, "point"),
            (self.stable_action_id, "stable action identity"),
            (self.payload_digest, "payload digest"),
        ):
            if (
                type(value) is not str
                or not value
                or "\x00" in value
                or "\r" in value
                or "\n" in value
            ):
                raise ValueError(f"FAULT_REQUEST_{label.upper().replace(' ', '_')}_INVALID")
        if type(self.plan_revision_digest) is not str or "\x00" in self.plan_revision_digest:
            raise ValueError("FAULT_REQUEST_PLAN_IDENTITY_INVALID")
        if self.plan_revision_digest and _DIGEST_RE.fullmatch(self.plan_revision_digest) is None:
            raise ValueError("FAULT_REQUEST_PLAN_IDENTITY_INVALID")
        if (
            type(self.command) is not tuple
            or not self.command
            or any(
                type(item) is not str
                or not item
                or "\x00" in item
                or "\r" in item
                or "\n" in item
                for item in self.command
            )
        ):
            raise ValueError("FAULT_REQUEST_COMMAND_INVALID")


class FaultProxyProcessExit(RuntimeError):
    """The external proxy exited after persisting an effect and before ack."""

    exit_code = 75


def _canonical_bytes(value: object) -> bytes:
    try:
        rendered = json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
    except (TypeError, ValueError) as error:
        raise ValueError("FAULT_PROXY_VALUE_NOT_CANONICAL") from error
    return (rendered + "\n").encode("utf-8")


def _sha(value: object) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _command_digest(command: tuple[str, ...]) -> str:
    return _sha({"kind": "fault-proxy-command.v1", "command": list(command)})


def _response_digest(request: FaultRequest, response: object) -> str:
    return _sha(
        {
            "kind": "fault-proxy-response.v2",
            "role": request.role,
            "point": request.point,
            "stable_action_id": request.stable_action_id,
            "plan_revision_digest": request.plan_revision_digest,
            "payload_digest": request.payload_digest,
            "command": list(request.command),
            "response": response,
        }
    )


def _is_reparse(path: Path) -> bool:
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return False
    if stat.S_ISLNK(info.st_mode):
        return True
    return bool(getattr(info, "st_file_attributes", 0) & _REPARSE_POINT)


def _absolute_path(path: Path) -> Path:
    value = Path(path)
    if not value.is_absolute():
        value = Path.cwd() / value
    return value


def _check_path_components(path: Path, *, allow_missing: bool) -> None:
    """Reject symlink/reparse components before every journal file access."""

    current = _absolute_path(path)
    parts = current.parts
    if not parts:
        raise ValueError("ROOT_CANARY_FAULT_PATH_INVALID")
    if _is_reparse(current):
        raise ValueError("ROOT_CANARY_FAULT_PATH_REPARSE")
    cursor = Path(parts[0])
    for index, part in enumerate(parts[1:], start=1):
        cursor /= part
        try:
            exists = cursor.exists() or cursor.is_symlink()
        except OSError as error:
            raise ValueError("ROOT_CANARY_FAULT_PATH_INVALID") from error
        if not exists:
            if allow_missing:
                continue
            raise ValueError("ROOT_CANARY_FAULT_PATH_INVALID")
        if _is_reparse(cursor):
            raise ValueError("ROOT_CANARY_FAULT_PATH_REPARSE")
        if index < len(parts) - 1:
            try:
                if not stat.S_ISDIR(os.lstat(cursor).st_mode):
                    raise ValueError("ROOT_CANARY_FAULT_PATH_INVALID")
            except OSError as error:
                raise ValueError("ROOT_CANARY_FAULT_PATH_INVALID") from error


def _validate_regular_leaf(path: Path, *, allow_missing: bool) -> None:
    try:
        info = os.lstat(_absolute_path(path))
    except FileNotFoundError:
        if allow_missing:
            return
        raise ValueError("ROOT_CANARY_FAULT_PATH_INVALID")
    except OSError as error:
        raise ValueError("ROOT_CANARY_FAULT_PATH_INVALID") from error
    if (
        _is_reparse(_absolute_path(path))
        or not stat.S_ISREG(info.st_mode)
        or getattr(info, "st_nlink", 1) != 1
    ):
        raise ValueError("ROOT_CANARY_FAULT_PATH_INVALID")


def _safe_read_bytes(path: Path) -> bytes:
    _check_path_components(path, allow_missing=False)
    _validate_regular_leaf(path, allow_missing=False)
    flags = os.O_RDONLY
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags | nofollow)
    except (OSError, ValueError) as error:
        raise ValueError("FAULT_PROXY_JOURNAL_READ_FAILED") from error
    try:
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode)
            or getattr(info, "st_nlink", 1) != 1
        ):
            raise ValueError("FAULT_PROXY_JOURNAL_READ_FAILED")
        with os.fdopen(descriptor, "rb") as stream:
            descriptor = -1
            return stream.read()
    finally:
        if descriptor != -1:
            os.close(descriptor)


@contextmanager
def _file_lock(path: Path) -> Iterator[None]:
    """Serialize journal read/modify/write across threads and processes."""

    _check_path_components(path, allow_missing=True)
    _validate_regular_leaf(path, allow_missing=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    _check_path_components(path, allow_missing=True)
    _validate_regular_leaf(path, allow_missing=True)
    key = str(path.absolute())
    with _JOURNAL_LOCKS_GUARD:
        thread_lock = _JOURNAL_LOCKS.setdefault(key, threading.RLock())
    with thread_lock:
        try:
            import fcntl  # type: ignore[import-not-found]
        except ImportError:
            fcntl = None
        with path.open("a+b") as stream:
            info = os.fstat(stream.fileno())
            if (
                not stat.S_ISREG(info.st_mode)
                or getattr(info, "st_nlink", 1) != 1
            ):
                raise ValueError("ROOT_CANARY_FAULT_PATH_INVALID")
            if fcntl is not None:
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
            else:
                import msvcrt

                stream.seek(0, os.SEEK_END)
                if stream.tell() == 0:
                    stream.write(b"0")
                    stream.flush()
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                if fcntl is not None:
                    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
                else:
                    import msvcrt

                    stream.seek(0)
                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)


@dataclass
class FaultProxy:
    journal_path: Path
    events: tuple[Mapping[str, object], ...]
    run_command: Callable[[tuple[str, ...]], object]
    run_root: Path | None = None

    def __post_init__(self) -> None:
        self.journal_path = Path(self.journal_path)
        if self.run_root is not None:
            self.run_root = Path(self.run_root)
            self.journal_path = _require_child(self.journal_path, self.run_root)
        else:
            _check_path_components(self.journal_path, allow_missing=True)
        if not callable(self.run_command):
            raise ValueError("FAULT_PROXY_COMMAND_INVALID")
        if type(self.events) is not tuple:
            raise ValueError("FAULT_PROXY_PLAN_INVALID")
        for event in self.events:
            if type(event) is not dict or set(event) - {
                "role",
                "point",
                "stable_action_id",
                "plan_revision_digest",
                "payload_digest",
            }:
                raise ValueError("FAULT_PROXY_PLAN_INVALID")
            for field in ("role", "point"):
                if type(event.get(field)) is not str or not event[field]:
                    raise ValueError("FAULT_PROXY_PLAN_INVALID")
            if "stable_action_id" in event and (
                type(event["stable_action_id"]) is not str
                or not event["stable_action_id"]
            ):
                raise ValueError("FAULT_PROXY_PLAN_INVALID")
            if "payload_digest" in event and (
                type(event["payload_digest"]) is not str
                or not event["payload_digest"]
            ):
                raise ValueError("FAULT_PROXY_PLAN_INVALID")
            if "plan_revision_digest" in event and (
                type(event["plan_revision_digest"]) is not str
                or _DIGEST_RE.fullmatch(event["plan_revision_digest"]) is None
            ):
                raise ValueError("FAULT_PROXY_PLAN_INVALID")

    @classmethod
    def from_files(
        cls,
        plan_path: Path,
        journal_path: Path,
        *,
        run_root: Path | None = None,
    ) -> "FaultProxy":
        if run_root is not None:
            plan_path = _require_child(Path(plan_path), Path(run_root))
            journal_path = _require_child(Path(journal_path), Path(run_root))
        else:
            _check_path_components(Path(plan_path), allow_missing=False)
        try:
            plan = json.loads(_safe_read_bytes(Path(plan_path)).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
            raise ValueError("FAULT_PROXY_PLAN_INVALID") from error
        if (
            type(plan) is not dict
            or set(plan) != {"events"}
            or type(plan["events"]) is not list
            or any(type(event) is not dict for event in plan["events"])
        ):
            raise ValueError("FAULT_PROXY_PLAN_INVALID")
        return cls(
            journal_path=Path(journal_path),
            events=tuple(dict(event) for event in plan["events"]),
            run_root=run_root,
            run_command=lambda command: subprocess.run(
                command,
                text=True,
                capture_output=True,
                check=True,
            ).stdout,
        )

    def _read_unlocked(self) -> dict[str, object]:
        if self.run_root is not None:
            _require_child(self.journal_path, self.run_root)
        else:
            _check_path_components(self.journal_path, allow_missing=True)
        if not self.journal_path.exists():
            return {"effects": {}, "consumed_faults": []}
        try:
            raw = json.loads(_safe_read_bytes(self.journal_path).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
            raise ValueError("FAULT_PROXY_JOURNAL_INVALID") from error
        if type(raw) is not dict or set(raw) != {"effects", "consumed_faults"}:
            raise ValueError("FAULT_PROXY_JOURNAL_INVALID")
        effects = raw["effects"]
        consumed = raw["consumed_faults"]
        if (
            type(effects) is not dict
            or type(consumed) is not list
            or any(type(key) is not str or not key for key in effects)
            or any(type(item) is not str or not item for item in consumed)
            or len(consumed) != len(set(consumed))
        ):
            raise ValueError("FAULT_PROXY_JOURNAL_INVALID")
        return {"effects": dict(effects), "consumed_faults": list(consumed)}

    def _read(self) -> dict[str, object]:
        with _file_lock(self.journal_path.with_name(self.journal_path.name + ".lock")):
            return self._read_unlocked()

    def _write_atomically_unlocked(self, payload: Mapping[str, object]) -> None:
        if self.run_root is not None:
            _require_child(self.journal_path, self.run_root)
        else:
            _check_path_components(self.journal_path, allow_missing=True)
        _validate_regular_leaf(self.journal_path, allow_missing=True)
        self.journal_path.parent.mkdir(parents=True, exist_ok=True)
        if self.run_root is not None:
            _require_child(self.journal_path, self.run_root)
        else:
            _check_path_components(self.journal_path, allow_missing=True)
        temporary = self.journal_path.with_name(
            f".{self.journal_path.name}.{os.getpid()}.{threading.get_ident()}.{time.time_ns()}.tmp"
        )
        _check_path_components(temporary, allow_missing=True)
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        nofollow = getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(temporary, flags | nofollow, 0o600)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                descriptor = -1
                stream.write(_canonical_bytes(payload))
                stream.flush()
                os.fsync(stream.fileno())
            if self.run_root is not None:
                _require_child(self.journal_path, self.run_root)
            else:
                _check_path_components(self.journal_path, allow_missing=True)
            _validate_regular_leaf(self.journal_path, allow_missing=True)
            os.replace(temporary, self.journal_path)
            if os.name != "nt":
                directory = os.open(self.journal_path.parent, os.O_RDONLY)
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
        finally:
            if descriptor != -1:
                os.close(descriptor)
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass

    def _write_atomically(self, payload: Mapping[str, object]) -> None:
        with _file_lock(self.journal_path.with_name(self.journal_path.name + ".lock")):
            self._write_atomically_unlocked(payload)

    @staticmethod
    def _event_matches(
        event: Mapping[str, object],
        request: FaultRequest,
    ) -> bool:
        if event.get("role") != request.role or event.get("point") != request.point:
            return False
        for field, value in (
            ("stable_action_id", request.stable_action_id),
            ("plan_revision_digest", request.plan_revision_digest),
            ("payload_digest", request.payload_digest),
        ):
            if field in event and event[field] != value:
                return False
        return True

    @staticmethod
    def _fault_key(request: FaultRequest) -> str:
        return ":".join(
            (
                request.role,
                request.point,
                request.stable_action_id,
                request.plan_revision_digest,
            )
        )

    @staticmethod
    def _validate_previous(
        previous: object,
        request: FaultRequest,
    ) -> str:
        expected = {
            "role",
            "point",
            "stable_action_id",
            "plan_revision_digest",
            "payload_digest",
            "command",
            "command_digest",
            "response",
            "response_digest",
        }
        if type(previous) is not dict or set(previous) != expected:
            raise ValueError("FAULT_PROXY_JOURNAL_INVALID")
        if previous.get("payload_digest") != request.payload_digest:
            raise ValueError("FAULT_ACTION_PAYLOAD_MISMATCH")
        if any(
            previous.get(field) != value
            for field, value in (
                ("role", request.role),
                ("point", request.point),
                ("stable_action_id", request.stable_action_id),
                ("plan_revision_digest", request.plan_revision_digest),
                ("command", list(request.command)),
            )
        ):
            raise ValueError("FAULT_ACTION_IDENTITY_MISMATCH")
        command_digest = previous.get("command_digest")
        if type(command_digest) is not str or command_digest != _command_digest(request.command):
            raise ValueError("FAULT_ACTION_COMMAND_MISMATCH")
        response_digest = previous.get("response_digest")
        if type(response_digest) is not str or _DIGEST_RE.fullmatch(response_digest) is None:
            raise ValueError("FAULT_PROXY_JOURNAL_INVALID")
        if response_digest != _response_digest(request, previous.get("response")):
            raise ValueError("FAULT_ACTION_RESPONSE_MISMATCH")
        return response_digest

    def execute(
        self,
        request: FaultRequest,
        *,
        run_command: Callable[[tuple[str, ...]], object] | None = None,
    ) -> str:
        if type(request) is not FaultRequest:
            raise ValueError("FAULT_REQUEST_INVALID")
        if _DIGEST_RE.fullmatch(request.plan_revision_digest) is None:
            raise ValueError("FAULT_REQUEST_PLAN_IDENTITY_INVALID")
        lock_path = self.journal_path.with_name(self.journal_path.name + ".lock")
        with _file_lock(lock_path):
            journal = self._read_unlocked()
            effects = journal["effects"]
            assert type(effects) is dict
            previous = effects.get(request.stable_action_id)
            if previous is not None:
                return self._validate_previous(previous, request)

            response = (run_command or self.run_command)(request.command)
            # Validate the response before claiming that it is durable.  The
            # exact response is retained so a replay can verify its digest
            # instead of trusting an unbound digest string.
            _canonical_bytes(response)
            response_digest = _response_digest(request, response)
            consumed = journal["consumed_faults"]
            assert type(consumed) is list
            fault_key = self._fault_key(request)
            inject = any(
                self._event_matches(event, request)
                and fault_key not in consumed
                for event in self.events
            )
            effects[request.stable_action_id] = {
                "role": request.role,
                "point": request.point,
                "stable_action_id": request.stable_action_id,
                "plan_revision_digest": request.plan_revision_digest,
                "payload_digest": request.payload_digest,
                "command": list(request.command),
                "command_digest": _command_digest(request.command),
                "response": response,
                "response_digest": response_digest,
            }
            if inject:
                consumed.append(fault_key)
                consumed.sort()
            self._write_atomically_unlocked(
                {"effects": effects, "consumed_faults": consumed}
            )
            if inject:
                raise FaultProxyProcessExit(fault_key)
            return response_digest


def _require_child(path: Path, root: Path) -> Path:
    """Return a canonical child only when no path component is reparsed."""

    try:
        resolved_root = _absolute_path(Path(root))
        if not resolved_root.exists() or not resolved_root.is_dir():
            raise ValueError("ROOT_CANARY_FAULT_PATH_INVALID")
        _check_path_components(resolved_root, allow_missing=False)
        root_real = resolved_root.resolve(strict=True)
        candidate = _absolute_path(Path(path))
        if candidate == resolved_root:
            raise ValueError("ROOT_CANARY_FAULT_PATH_INVALID")
        if not candidate.is_relative_to(resolved_root):
            raise ValueError("ROOT_CANARY_FAULT_PATH_OUTSIDE_RUN_ROOT")
        _check_path_components(candidate, allow_missing=True)
        _validate_regular_leaf(candidate, allow_missing=True)
        resolved = candidate.resolve(strict=False)
        if not resolved.is_relative_to(root_real):
            raise ValueError("ROOT_CANARY_FAULT_PATH_OUTSIDE_RUN_ROOT")
        # A component may have changed between the first check and resolve;
        # recheck the lexical path and refuse the operation rather than
        # following a swapped link.
        _check_path_components(candidate, allow_missing=True)
        _validate_regular_leaf(candidate, allow_missing=True)
        return resolved
    except ValueError:
        raise
    except (OSError, RuntimeError, TypeError) as error:
        raise ValueError("ROOT_CANARY_FAULT_PATH_INVALID") from error


__all__ = [
    "FaultProxy",
    "FaultProxyProcessExit",
    "FaultRequest",
    "_require_child",
]
