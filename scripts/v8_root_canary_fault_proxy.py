"""One-shot, run-root-bound fault injection for the V8 root Canary.

The proxy is deliberately a small durable adapter.  It owns neither Campaign
state nor workflow transitions; it only records an exact command response and
can terminate once after that response is durable.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field as dataclass_field
import hashlib
import importlib
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
_PathIdentity = tuple[int, int]


def _held_filesystem_module():
    """Load the repository's descriptor-relative filesystem primitives."""

    try:
        return importlib.import_module("beta3_release_subject")
    except ModuleNotFoundError as error:
        if error.name != "beta3_release_subject":
            raise
        return importlib.import_module("scripts.beta3_release_subject")


@contextmanager
def _held_directory(path: Path):
    """Hold every directory component used by a journal operation.

    The release subject primitives open components without following reparse
    points.  This local lease additionally shares delete on Windows so a
    boundary regression can exercise a rename while the operation remains
    anchored to the original handles.  POSIX calls use those handles as
    directory fds; Windows relative native opens use them as root handles.
    """

    filesystem = _held_filesystem_module()
    code = "ROOT_CANARY_FAULT_PATH_INVALID"
    descriptors: list[int] = []
    identities: list[dict[str, int | str]] = []
    try:
        for index, component in enumerate(filesystem._directory_components(path)):
            parent = descriptors[-1] if descriptors else None
            open_path = component if parent is None else Path(component.name)
            descriptor = filesystem._open_path_handle(
                open_path,
                code,
                directory=True,
                parent=parent,
                share_delete=True,
            )
            try:
                identity = filesystem._windows_handle_identity(
                    descriptor,
                    code,
                    directory=True,
                )
            except Exception:
                os.close(descriptor)
                raise
            descriptors.append(descriptor)
            identities.append(dict(identity))
        if not descriptors:
            raise ValueError(code)
        lease = filesystem._DirectoryLease(
            Path(os.path.abspath(path)),
            tuple(descriptors),
            tuple(identities),
        )
        descriptors = []
    except ValueError:
        filesystem._close_descriptors(descriptors)
        raise
    except Exception as error:
        filesystem._close_descriptors(descriptors)
        raise ValueError(code) from error
    try:
        yield lease
    finally:
        lease.close()


def _open_held_file(
    name: str,
    parent: object,
    *,
    create_new: bool = False,
    writable: bool = False,
    delete: bool = False,
    missing_ok: bool = False,
) -> int | None:
    """Open one leaf relative to a held parent and revalidate the lease."""

    filesystem = _held_filesystem_module()
    code = "ROOT_CANARY_FAULT_PATH_INVALID"
    assert_stable = getattr(parent, "assert_stable", None)
    handles = getattr(parent, "handles", None)
    if not callable(assert_stable) or type(handles) is not tuple or not handles:
        raise ValueError(code)
    assert_stable()
    descriptor: int | None = None
    try:
        descriptor = filesystem._open_path_handle(
            Path(name),
            code,
            directory=False,
            parent=handles[-1],
            create_new=create_new,
            writable=writable,
            delete=delete,
            share_delete=True,
            missing_ok=missing_ok,
        )
    except FileNotFoundError:
        assert_stable()
        if missing_ok:
            return None
        raise ValueError("FAULT_PROXY_JOURNAL_READ_FAILED")
    except Exception as error:
        raise ValueError(code) from error
    try:
        assert_stable()
    except Exception:
        os.close(descriptor)
        raise
    return descriptor


def _read_held_file(name: str, parent: object) -> bytes:
    filesystem = _held_filesystem_module()
    descriptor = _open_held_file(name, parent)
    assert descriptor is not None
    try:
        before = filesystem._windows_handle_identity(
            descriptor,
            "FAULT_PROXY_JOURNAL_READ_FAILED",
            directory=False,
        )
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or getattr(info, "st_nlink", 1) != 1:
            raise ValueError("FAULT_PROXY_JOURNAL_READ_FAILED")
        raw = filesystem._read_held_bytes(
            descriptor,
            "FAULT_PROXY_JOURNAL_READ_FAILED",
        )
        after = filesystem._windows_handle_identity(
            descriptor,
            "FAULT_PROXY_JOURNAL_READ_FAILED",
            directory=False,
        )
        if (
            not filesystem._identity_matches(after, before)
            or after.get("st_size") != len(raw)
        ):
            raise ValueError("FAULT_PROXY_JOURNAL_READ_FAILED")
        parent.assert_stable()
        return raw
    except ValueError:
        raise
    except Exception as error:
        raise ValueError("FAULT_PROXY_JOURNAL_READ_FAILED") from error
    finally:
        os.close(descriptor)


def _open_lock_file(name: str, parent: object) -> int:
    descriptor = _open_held_file(name, parent, writable=True, missing_ok=True)
    if descriptor is not None:
        return descriptor
    try:
        descriptor = _open_held_file(
            name,
            parent,
            create_new=True,
            writable=True,
        )
    except ValueError as create_error:
        try:
            descriptor = _open_held_file(name, parent, writable=True)
        except ValueError:
            raise create_error
    assert descriptor is not None
    return descriptor


def _windows_rename_by_handle(
    descriptor: int,
    parent_descriptor: int,
    target_name: str,
) -> None:
    import ctypes
    import msvcrt

    class IoStatusBlock(ctypes.Structure):
        _fields_ = [("status", ctypes.c_void_p), ("information", ctypes.c_size_t)]

    class FileRenameInfo(ctypes.Structure):
        _fields_ = [
            ("replace_if_exists", ctypes.c_ubyte),
            ("root_directory", ctypes.c_void_p),
            ("file_name_length", ctypes.c_uint32),
            ("file_name", ctypes.c_wchar * len(target_name)),
        ]

    info = FileRenameInfo(
        1,
        ctypes.c_void_p(msvcrt.get_osfhandle(parent_descriptor)),
        len(target_name) * ctypes.sizeof(ctypes.c_wchar),
        target_name,
    )
    io_status = IoStatusBlock()
    ntdll = ctypes.WinDLL("ntdll")
    ntdll.NtSetInformationFile.restype = ctypes.c_long
    ntdll.NtSetInformationFile.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(IoStatusBlock),
        ctypes.c_void_p,
        ctypes.c_ulong,
        ctypes.c_int,
    ]
    status = int(
        ntdll.NtSetInformationFile(
            msvcrt.get_osfhandle(descriptor),
            ctypes.byref(io_status),
            ctypes.byref(info),
            ctypes.sizeof(info),
            10,
        )
    ) & 0xFFFFFFFF
    if status & 0x80000000:
        raise OSError(status, "NtSetInformationFile rename failed")


def _replace_relative(parent: object, source_name: str, target_name: str) -> None:
    """Atomically replace one held-parent child without path traversal."""

    filesystem = _held_filesystem_module()
    handles = getattr(parent, "handles", None)
    assert_stable = getattr(parent, "assert_stable", None)
    if type(handles) is not tuple or not handles or not callable(assert_stable):
        raise ValueError("ROOT_CANARY_FAULT_PATH_INVALID")
    assert_stable()
    source = _open_held_file(
        source_name,
        parent,
        delete=True,
    )
    assert source is not None
    try:
        source_identity = filesystem._windows_handle_identity(
            source,
            "FAULT_PROXY_JOURNAL_WRITE_FAILED",
            directory=False,
        )
        if os.name != "nt":
            os.replace(
                Path(source_name),
                Path(target_name),
                src_dir_fd=handles[-1],
                dst_dir_fd=handles[-1],
            )
        else:
            _windows_rename_by_handle(source, handles[-1], target_name)
    except ValueError:
        raise
    except Exception as error:
        raise ValueError("FAULT_PROXY_JOURNAL_WRITE_FAILED") from error
    finally:
        os.close(source)
    target = _open_held_file(target_name, parent)
    assert target is not None
    try:
        target_identity = filesystem._windows_handle_identity(
            target,
            "FAULT_PROXY_JOURNAL_WRITE_FAILED",
            directory=False,
        )
        if not filesystem._identity_matches(target_identity, source_identity):
            raise ValueError("FAULT_PROXY_JOURNAL_WRITE_FAILED")
    finally:
        os.close(target)


def _delete_relative_if_identity(
    parent: object,
    name: str,
    expected_identity: Mapping[str, object] | None,
) -> None:
    """Delete only a child still owned by this operation and held parent."""

    if expected_identity is None:
        return
    filesystem = _held_filesystem_module()
    try:
        descriptor = _open_held_file(name, parent, delete=True, missing_ok=True)
    except (FileNotFoundError, ValueError):
        return
    if descriptor is None:
        return
    try:
        identity = filesystem._windows_handle_identity(
            descriptor,
            "FAULT_PROXY_JOURNAL_WRITE_FAILED",
            directory=False,
        )
        if not filesystem._identity_matches(identity, expected_identity):
            return
        if os.name != "nt":
            handles = getattr(parent, "handles", None)
            if type(handles) is not tuple or not handles:
                return
            os.unlink(Path(name), dir_fd=handles[-1])
        else:
            import ctypes
            import msvcrt

            class FileDispositionInfo(ctypes.Structure):
                _fields_ = [("delete_file", ctypes.c_int)]

            disposition = FileDispositionInfo(1)
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.SetFileInformationByHandle.restype = ctypes.c_int
            kernel32.SetFileInformationByHandle.argtypes = [
                ctypes.c_void_p,
                ctypes.c_int,
                ctypes.c_void_p,
                ctypes.c_uint32,
            ]
            if not kernel32.SetFileInformationByHandle(
                msvcrt.get_osfhandle(descriptor),
                4,
                ctypes.byref(disposition),
                ctypes.sizeof(disposition),
            ):
                raise OSError(ctypes.get_last_error(), "SetFileInformationByHandle delete failed")
    except (FileNotFoundError, OSError, ValueError):
        return
    finally:
        os.close(descriptor)


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


def _path_identity(path: Path, *, directory: bool = False) -> _PathIdentity:
    try:
        info = os.lstat(_absolute_path(path))
    except (OSError, ValueError) as error:
        raise ValueError("ROOT_CANARY_FAULT_PATH_INVALID") from error
    if directory and not stat.S_ISDIR(info.st_mode):
        raise ValueError("ROOT_CANARY_FAULT_PATH_INVALID")
    if _is_reparse(_absolute_path(path)):
        raise ValueError("ROOT_CANARY_FAULT_PATH_REPARSE")
    return (int(info.st_dev), int(info.st_ino))


def _safe_read_bytes(path: Path, *, parent: object | None = None) -> bytes:
    if parent is not None:
        return _read_held_file(Path(path).name, parent)
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
def _file_lock(path: Path, *, parent: object | None = None) -> Iterator[None]:
    """Serialize journal read/modify/write across threads and processes."""

    if parent is not None:
        descriptor = _open_lock_file(Path(path).name, parent)
        key = str(_absolute_path(path))
        with _JOURNAL_LOCKS_GUARD:
            thread_lock = _JOURNAL_LOCKS.setdefault(key, threading.RLock())
        with thread_lock:
            try:
                import fcntl  # type: ignore[import-not-found]
            except ImportError:
                fcntl = None
            try:
                with os.fdopen(descriptor, "a+b") as stream:
                    descriptor = -1
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
                        parent.assert_stable()
                        yield
                    finally:
                        if fcntl is not None:
                            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
                        else:
                            import msvcrt

                            stream.seek(0)
                            msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                        parent.assert_stable()
            finally:
                if descriptor != -1:
                    os.close(descriptor)
        return

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
    run_root_identity: _PathIdentity | None = dataclass_field(default=None, repr=False)
    _journal_parent_identity: _PathIdentity | None = dataclass_field(
        default=None,
        init=False,
        repr=False,
    )

    def __post_init__(self) -> None:
        self.journal_path = Path(self.journal_path)
        if self.run_root is not None:
            self.run_root = _absolute_path(Path(self.run_root))
            expected_root_identity = self.run_root_identity
            if expected_root_identity is None:
                expected_root_identity = _path_identity(
                    self.run_root,
                    directory=True,
                )
            self.run_root_identity = expected_root_identity
            self.journal_path = _require_child(self.journal_path, self.run_root)
            self._assert_held_path_identity()
            if self.journal_path.parent.exists():
                self._journal_parent_identity = _path_identity(
                    self.journal_path.parent,
                    directory=True,
                )
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

    def _assert_held_path_identity(self) -> None:
        if self.run_root is None or self.run_root_identity is None:
            return
        if _path_identity(self.run_root, directory=True) != self.run_root_identity:
            raise ValueError("ROOT_CANARY_FAULT_PATH_INVALID")
        if self._journal_parent_identity is not None:
            if (
                _path_identity(self.journal_path.parent, directory=True)
                != self._journal_parent_identity
            ):
                raise ValueError("ROOT_CANARY_FAULT_PATH_INVALID")

    def _validated_child_path(self, path: Path) -> Path:
        if self.run_root is None:
            _check_path_components(path, allow_missing=True)
            _validate_regular_leaf(path, allow_missing=True)
            return path
        self._assert_held_path_identity()
        validated = _require_child(path, self.run_root)
        self._assert_held_path_identity()
        _validate_regular_leaf(validated, allow_missing=True)
        return validated

    def _validated_journal_path(self) -> Path:
        return self._validated_child_path(self.journal_path)

    @classmethod
    def from_files(
        cls,
        plan_path: Path,
        journal_path: Path,
        *,
        run_root: Path | None = None,
    ) -> "FaultProxy":
        root_identity: _PathIdentity | None = None
        if run_root is not None:
            run_root = _absolute_path(Path(run_root))
            root_identity = _path_identity(run_root, directory=True)
            plan_path = _require_child(Path(plan_path), run_root)
            if _path_identity(run_root, directory=True) != root_identity:
                raise ValueError("ROOT_CANARY_FAULT_PATH_INVALID")
            journal_path = _require_child(Path(journal_path), run_root)
            if _path_identity(run_root, directory=True) != root_identity:
                raise ValueError("ROOT_CANARY_FAULT_PATH_INVALID")
        else:
            _check_path_components(Path(plan_path), allow_missing=False)
        try:
            with _held_directory(Path(plan_path).parent) as parent:
                plan = json.loads(
                    _safe_read_bytes(Path(plan_path), parent=parent).decode("utf-8")
                )
                parent.assert_stable()
                if (
                    run_root is not None
                    and root_identity is not None
                    and _path_identity(run_root, directory=True) != root_identity
                ):
                    raise ValueError("ROOT_CANARY_FAULT_PATH_INVALID")
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
            raise ValueError("FAULT_PROXY_PLAN_INVALID") from error
        if (
            run_root is not None
            and root_identity is not None
            and _path_identity(run_root, directory=True) != root_identity
        ):
            raise ValueError("ROOT_CANARY_FAULT_PATH_INVALID")
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
            run_root_identity=root_identity,
            run_command=lambda command: subprocess.run(
                command,
                text=True,
                capture_output=True,
                check=True,
            ).stdout,
        )

    def _read_unlocked(self, parent: object) -> dict[str, object]:
        journal_path = self._validated_journal_path()
        descriptor = _open_held_file(
            journal_path.name,
            parent,
            missing_ok=True,
        )
        if descriptor is None:
            return {"effects": {}, "consumed_faults": []}
        os.close(descriptor)
        try:
            raw = json.loads(
                _safe_read_bytes(journal_path, parent=parent).decode("utf-8")
            )
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
            raise ValueError("FAULT_PROXY_JOURNAL_INVALID") from error
        parent.assert_stable()
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
        journal_path = self._validated_journal_path()
        with _held_directory(journal_path.parent) as parent:
            self._assert_held_path_identity()
            with _file_lock(
                journal_path.with_name(journal_path.name + ".lock"),
                parent=parent,
            ):
                self._assert_held_path_identity()
                return self._read_unlocked(parent)

    def _write_atomically_unlocked(
        self,
        payload: Mapping[str, object],
        parent: object,
    ) -> None:
        filesystem = _held_filesystem_module()
        journal_path = self._validated_journal_path()
        if self.run_root is None:
            journal_path.parent.mkdir(parents=True, exist_ok=True)
        self._assert_held_path_identity()
        journal_path = self._validated_journal_path()
        temporary = Path(
            f".{journal_path.name}.{os.getpid()}.{threading.get_ident()}.{time.time_ns()}.tmp"
        )
        descriptor = _open_held_file(
            temporary.name,
            parent,
            create_new=True,
            writable=True,
            delete=True,
        )
        assert descriptor is not None
        temporary_identity: Mapping[str, object] | None = None
        try:
            temporary_identity = filesystem._windows_handle_identity(
                descriptor,
                "FAULT_PROXY_JOURNAL_WRITE_FAILED",
                directory=False,
            )
            raw = _canonical_bytes(payload)
            offset = 0
            while offset < len(raw):
                written = os.write(descriptor, raw[offset:])
                if written <= 0:
                    raise ValueError("FAULT_PROXY_JOURNAL_WRITE_FAILED")
                offset += written
            os.fsync(descriptor)
            after_identity = filesystem._windows_handle_identity(
                descriptor,
                "FAULT_PROXY_JOURNAL_WRITE_FAILED",
                directory=False,
            )
            if not filesystem._identity_matches(after_identity, temporary_identity):
                raise ValueError("FAULT_PROXY_JOURNAL_WRITE_FAILED")
            os.close(descriptor)
            descriptor = -1
            parent.assert_stable()
            _replace_relative(parent, temporary.name, journal_path.name)
            parent.assert_stable()
            self._assert_held_path_identity()
            if os.name != "nt":
                handles = getattr(parent, "handles", None)
                if type(handles) is not tuple or not handles:
                    raise ValueError("ROOT_CANARY_FAULT_PATH_INVALID")
                directory = os.open(
                    ".",
                    os.O_RDONLY
                    | getattr(os, "O_DIRECTORY", 0)
                    | getattr(os, "O_NOFOLLOW", 0),
                    dir_fd=handles[-1],
                )
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
        except Exception:
            _delete_relative_if_identity(
                parent,
                journal_path.name,
                temporary_identity,
            )
            raise
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            _delete_relative_if_identity(
                parent,
                temporary.name,
                temporary_identity,
            )

    def _write_atomically(self, payload: Mapping[str, object]) -> None:
        journal_path = self._validated_journal_path()
        if self.run_root is None:
            journal_path.parent.mkdir(parents=True, exist_ok=True)
        with _held_directory(journal_path.parent) as parent, _file_lock(
            journal_path.with_name(journal_path.name + ".lock"),
            parent=parent,
        ):
            self._assert_held_path_identity()
            self._write_atomically_unlocked(payload, parent)

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
        journal_path = self._validated_journal_path()
        lock_path = journal_path.with_name(journal_path.name + ".lock")
        if self.run_root is None:
            journal_path.parent.mkdir(parents=True, exist_ok=True)
        with _held_directory(journal_path.parent) as parent, _file_lock(
            lock_path,
            parent=parent,
        ):
            self._assert_held_path_identity()
            journal = self._read_unlocked(parent)
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
                {"effects": effects, "consumed_faults": consumed},
                parent,
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
