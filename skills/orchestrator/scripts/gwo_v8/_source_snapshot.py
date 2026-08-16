"""Held, descriptor-relative snapshots for the audited V8 source tree."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
import ctypes
import hashlib
import os
from pathlib import Path
import stat
import struct
from typing import Mapping


class SourceSnapshotError(RuntimeError):
    """Raised when the audited source cannot be captured as one stable tree."""


_WINDOWS_DIRECTORY_INFO_CLASS = 60
_WINDOWS_DIRECTORY_RECORD_MIN_SIZE = 88
_WINDOWS_DIRECTORY_FILE_ID_OFFSET = 72
_WINDOWS_DIRECTORY_FILE_ID_SIZE = 16
_WINDOWS_DIRECTORY_FILE_ID_END = 88
_WINDOWS_DIRECTORY_FILE_NAME_OFFSET = 88


def _is_reparse(value: os.stat_result) -> bool:
    return bool(getattr(value, "st_file_attributes", 0) & 0x0400)


def _identity(descriptor: int, *, directory: bool) -> dict[str, int | str]:
    try:
        observed = os.fstat(descriptor)
    except OSError as error:
        raise SourceSnapshotError("held source identity is unavailable") from error
    if (
        stat.S_ISDIR(observed.st_mode) != directory
        or (not directory and not stat.S_ISREG(observed.st_mode))
        or _is_reparse(observed)
    ):
        raise SourceSnapshotError("held source component has the wrong type")
    if os.name != "nt":
        return {
            "st_dev": int(observed.st_dev),
            "st_ino": int(observed.st_ino),
            "st_mode": int(observed.st_mode),
            "st_size": int(observed.st_size),
            "st_mtime_ns": int(observed.st_mtime_ns),
        }
    try:
        import msvcrt

        class FileIdInfo(ctypes.Structure):
            _fields_ = [
                ("volume_serial_number", ctypes.c_ulonglong),
                ("file_id", ctypes.c_ubyte * 16),
            ]

        class FileStandardInfo(ctypes.Structure):
            _fields_ = [
                ("allocation_size", ctypes.c_longlong),
                ("end_of_file", ctypes.c_longlong),
                ("number_of_links", ctypes.c_ulong),
                ("delete_pending", ctypes.c_int),
                ("directory", ctypes.c_int),
            ]

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.GetFileInformationByHandleEx.argtypes = [
            ctypes.c_void_p,
            ctypes.c_int,
            ctypes.c_void_p,
            ctypes.c_ulong,
        ]
        handle = msvcrt.get_osfhandle(descriptor)
        file_id = FileIdInfo()
        standard = FileStandardInfo()
        if not kernel32.GetFileInformationByHandleEx(
            handle, 18, ctypes.byref(file_id), ctypes.sizeof(file_id)
        ):
            raise OSError(ctypes.get_last_error(), "FILE_ID_INFO unavailable")
        if not kernel32.GetFileInformationByHandleEx(
            handle, 1, ctypes.byref(standard), ctypes.sizeof(standard)
        ):
            raise OSError(ctypes.get_last_error(), "FILE_STANDARD_INFO unavailable")
        strong_file_id = bytes(file_id.file_id)
        if len(strong_file_id) != 16 or not any(strong_file_id):
            raise OSError("authoritative Windows file identity is unavailable")
        return {
            "volume_id": int(file_id.volume_serial_number),
            "file_id": strong_file_id.hex(),
            "st_mode": int(observed.st_mode),
            "st_size": int(standard.end_of_file),
            "st_mtime_ns": int(observed.st_mtime_ns),
        }
    except (ImportError, OSError, AttributeError, TypeError) as error:
        raise SourceSnapshotError("Windows source identity is unavailable") from error


def _matches(left: Mapping[str, object], right: Mapping[str, object]) -> bool:
    if os.name == "nt":
        left_file_id = left.get("file_id")
        right_file_id = right.get("file_id")
        if (
            type(left.get("volume_id")) is not int
            or type(right.get("volume_id")) is not int
            or type(left_file_id) is not str
            or type(right_file_id) is not str
            or len(left_file_id) != 32
            or len(right_file_id) != 32
        ):
            raise SourceSnapshotError("Windows source identity is unavailable")
        return left.get("volume_id") == right.get("volume_id") and (
            left_file_id == right_file_id
        )
    if "file_id" in left or "file_id" in right:
        return left.get("volume_id") == right.get("volume_id") and left.get(
            "file_id"
        ) == right.get("file_id")
    return left.get("st_dev") == right.get("st_dev") and left.get("st_ino") == right.get(
        "st_ino"
    )


def _stat_identity(
    observed: os.stat_result,
    *,
    directory: bool,
) -> dict[str, int | str]:
    if (
        stat.S_ISDIR(observed.st_mode) != directory
        or (not directory and not stat.S_ISREG(observed.st_mode))
        or _is_reparse(observed)
    ):
        raise SourceSnapshotError("enumerated source component has the wrong type")
    if os.name == "nt":
        raise SourceSnapshotError("Windows directory entry identity is unavailable")
    return {
        "st_dev": int(observed.st_dev),
        "st_ino": int(observed.st_ino),
        "st_mode": int(observed.st_mode),
        "st_size": int(observed.st_size),
        "st_mtime_ns": int(observed.st_mtime_ns),
    }


def _fd_from_windows_handle(handle: object, flags: int) -> int:
    import msvcrt

    value = handle if isinstance(handle, int) else getattr(handle, "value", None)
    if type(value) is not int:
        raise OSError("Windows handle value is unavailable")
    return msvcrt.open_osfhandle(value, flags | getattr(os, "O_BINARY", 0))


def _open_windows_component(path: Path, parent: int | None, *, directory: bool) -> int:
    """Open one existing component without following a Windows reparse point."""

    import msvcrt

    if parent is None:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateFileW.restype = ctypes.c_void_p
        kernel32.CreateFileW.argtypes = [
            ctypes.c_wchar_p,
            ctypes.c_uint32,
            ctypes.c_uint32,
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.c_uint32,
            ctypes.c_void_p,
        ]
        access = (
            0x00000001 | 0x00000020 | 0x00000080
            if directory
            else 0x80000000 | 0x00100000
        )
        handle = kernel32.CreateFileW(
            str(Path(path).absolute()),
            access,
            0x00000003,
            None,
            3,
            0x02000000 | 0x00200000,
            None,
        )
        if handle in (None, ctypes.c_void_p(-1).value):
            raise OSError(ctypes.get_last_error(), "CreateFileW failed")
        try:
            return _fd_from_windows_handle(handle, os.O_RDONLY)
        except Exception:
            kernel32.CloseHandle(handle)
            raise

    class UnicodeString(ctypes.Structure):
        _fields_ = [
            ("length", ctypes.c_ushort),
            ("maximum_length", ctypes.c_ushort),
            ("buffer", ctypes.c_wchar_p),
        ]

    class ObjectAttributes(ctypes.Structure):
        _fields_ = [
            ("length", ctypes.c_ulong),
            ("root_directory", ctypes.c_void_p),
            ("object_name", ctypes.POINTER(UnicodeString)),
            ("attributes", ctypes.c_ulong),
            ("security_descriptor", ctypes.c_void_p),
            ("security_quality_of_service", ctypes.c_void_p),
        ]

    class IoStatusBlock(ctypes.Structure):
        _fields_ = [("status", ctypes.c_void_p), ("information", ctypes.c_size_t)]

    name = str(Path(path).name)
    if not name or name in {".", ".."}:
        raise SourceSnapshotError("invalid relative source component")
    name_buffer = ctypes.create_unicode_buffer(name)
    unicode_name = UnicodeString(
        len(name) * ctypes.sizeof(ctypes.c_wchar),
        ctypes.sizeof(name_buffer),
        ctypes.cast(name_buffer, ctypes.c_wchar_p),
    )
    object_attributes = ObjectAttributes(
        ctypes.sizeof(ObjectAttributes),
        ctypes.c_void_p(msvcrt.get_osfhandle(parent)),
        ctypes.pointer(unicode_name),
        0x00001040,
        None,
        None,
    )
    io_status = IoStatusBlock()
    handle = ctypes.c_void_p()
    ntdll = ctypes.WinDLL("ntdll")
    ntdll.NtCreateFile.restype = ctypes.c_long
    ntdll.NtCreateFile.argtypes = [
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.c_ulong,
        ctypes.POINTER(ObjectAttributes),
        ctypes.POINTER(IoStatusBlock),
        ctypes.c_void_p,
        ctypes.c_ulong,
        ctypes.c_ulong,
        ctypes.c_ulong,
        ctypes.c_ulong,
        ctypes.c_void_p,
        ctypes.c_ulong,
    ]
    status = int(
        ntdll.NtCreateFile(
            ctypes.byref(handle),
            0x00000001 | 0x00000020 | 0x00000080 | 0x00100000
            if directory
            else 0x80000000 | 0x00100000,
            ctypes.byref(object_attributes),
            ctypes.byref(io_status),
            None,
            0x00000080,
            0x00000003,
            1,
            0x00000020
            | 0x00200000
            | (0x00000001 if directory else 0x00000040),
            None,
            0,
        )
    ) & 0xFFFFFFFF
    if status & 0x80000000:
        raise OSError(status, "NtCreateFile failed")
    try:
        return _fd_from_windows_handle(handle, os.O_RDONLY)
    except Exception:
        ctypes.WinDLL("kernel32", use_last_error=True).CloseHandle(handle)
        raise


def _open_component(path: Path | str, parent: int | None, *, directory: bool) -> int:
    # Keep the POSIX branch path-like agnostic.  Besides avoiding needless
    # pathlib construction, this lets the contract test exercise the POSIX
    # branch on Windows by replacing ``os.name`` without asking pathlib to
    # instantiate a native-incompatible PosixPath.
    raw_path: object = path
    try:
        raw_path = os.fspath(path)
        if os.name == "nt":
            return _open_windows_component(
                Path(raw_path), parent, directory=directory
            )
        nofollow = getattr(os, "O_NOFOLLOW", None)
        if nofollow is None:
            raise SourceSnapshotError(
                "POSIX source capture requires O_NOFOLLOW"
            )
        flags = os.O_RDONLY | nofollow
        if directory:
            directory_flag = getattr(os, "O_DIRECTORY", None)
            if directory_flag is None:
                raise SourceSnapshotError(
                    "POSIX source capture requires O_DIRECTORY"
                )
            flags |= directory_flag
        else:
            nonblock = getattr(os, "O_NONBLOCK", None)
            if nonblock is None:
                raise SourceSnapshotError(
                    "POSIX source capture requires O_NONBLOCK for files"
                )
            flags |= nonblock
        target = raw_path if parent is None else os.path.basename(raw_path)
        return os.open(target, flags, dir_fd=parent)
    except SourceSnapshotError:
        raise
    except (OSError, TypeError) as error:
        raise SourceSnapshotError(
            f"source component cannot be held: {raw_path}"
        ) from error


def _read(descriptor: int) -> bytes:
    try:
        os.lseek(descriptor, 0, os.SEEK_SET)
        chunks: list[bytes] = []
        while True:
            block = os.read(descriptor, 1024 * 1024)
            if not block:
                return b"".join(chunks)
            chunks.append(block)
    except OSError as error:
        raise SourceSnapshotError("held source read failed") from error


@dataclass(frozen=True)
class _HeldFile:
    relative: str
    handle: int
    identity: Mapping[str, object]
    content: bytes


@dataclass(frozen=True)
class _HeldDirectory:
    relative: str
    handle: int
    identity: Mapping[str, object]


def _enumerate_directory_entries(
    handle: int,
) -> tuple[tuple[str, bool, bool, Mapping[str, object]], ...]:
    if os.name == "nt":
        return _windows_directory_entries(handle)
    nofollow = getattr(os, "O_NOFOLLOW", None)
    directory_flag = getattr(os, "O_DIRECTORY", None)
    if nofollow is None or directory_flag is None:
        raise SourceSnapshotError(
            "POSIX source enumeration requires O_NOFOLLOW and O_DIRECTORY"
        )
    duplicate: int | None = None
    try:
        duplicate = os.open(".", os.O_RDONLY | nofollow | directory_flag, dir_fd=handle)
        os.lseek(duplicate, 0, os.SEEK_SET)
        with os.scandir(duplicate) as scanner:
            return tuple(
                sorted(
                    (
                        entry.name,
                        entry.is_dir(follow_symlinks=False),
                        entry.is_symlink(),
                        _stat_identity(
                            entry.stat(follow_symlinks=False),
                            directory=entry.is_dir(follow_symlinks=False),
                        ),
                    )
                    for entry in scanner
                )
            )
    except OSError as error:
        raise SourceSnapshotError("source directory cannot be enumerated") from error
    finally:
        if duplicate is not None:
            try:
                os.close(duplicate)
            except OSError:
                pass


@dataclass
class HeldSourceSnapshot:
    root: Path
    files: dict[str, _HeldFile]
    directories: dict[str, _HeldDirectory]
    ancestors: tuple[_HeldDirectory, ...]
    ancestor_parts: tuple[str, ...]
    entries: dict[str, tuple[tuple[str, bool, bool, Mapping[str, object]], ...]]
    borrowed_handles: frozenset[int] = field(default_factory=frozenset)
    _closed: bool = False
    _stability_suspension_depth: int = field(
        default=0, init=False, repr=False, compare=False
    )

    @classmethod
    def capture(
        cls,
        package_root: Path,
        *,
        root_handle: int | None = None,
    ) -> "HeldSourceSnapshot":
        root = Path(os.path.abspath(Path(package_root).expanduser()))
        directories: dict[str, _HeldDirectory] = {}
        ancestors: list[_HeldDirectory] = []
        files: dict[str, _HeldFile] = {}
        entries: dict[
            str, tuple[tuple[str, bool, bool, Mapping[str, object]], ...]
        ] = {}
        independent_ancestors: list[_HeldDirectory] = []
        borrowed_handles = (
            frozenset({root_handle}) if root_handle is not None else frozenset()
        )
        try:
            root_parts = Path(os.path.abspath(root)).parts
            if root_handle is None:
                parent: int | None = None
                for index, part in enumerate(root_parts):
                    path = Path(part) if index == 0 else Path(*root_parts[: index + 1])
                    handle = _open_component(path, parent, directory=True)
                    try:
                        observed = _HeldDirectory(
                            f"@ancestor/{index}",
                            handle,
                            _identity(handle, directory=True),
                        )
                    except Exception:
                        os.close(handle)
                        raise
                    ancestors.append(observed)
                    parent = handle
                root_descriptor = ancestors[-1].handle
                root_identity = ancestors[-1].identity
            else:
                root_descriptor = root_handle
                root_identity = _identity(root_handle, directory=True)
                parent: int | None = None
                for index, part in enumerate(root_parts):
                    path = Path(part) if index == 0 else Path(*root_parts[: index + 1])
                    handle = _open_component(path, parent, directory=True)
                    try:
                        ancestor = _HeldDirectory(
                            f"@ancestor/{index}",
                            handle,
                            _identity(handle, directory=True),
                        )
                    except Exception:
                        os.close(handle)
                        raise
                    independent_ancestors.append(ancestor)
                    parent = handle
                ancestors.extend(independent_ancestors)
                if not independent_ancestors or not _matches(
                    independent_ancestors[-1].identity,
                    root_identity,
                ):
                    raise SourceSnapshotError(
                        "borrowed source root handle does not match package_root"
                    )
            directories[""] = _HeldDirectory("", root_descriptor, root_identity)

            def enumerate_names(
                directory: _HeldDirectory,
            ) -> tuple[tuple[str, bool, bool, Mapping[str, object]], ...]:
                observed = _enumerate_directory_entries(directory.handle)
                entries.setdefault(directory.relative, observed)
                return observed

            def ensure_directory(relative: str, *, optional: bool = False) -> _HeldDirectory | None:
                if relative in directories:
                    return directories[relative]
                parent_relative, _, name = relative.rpartition("/")
                parent_directory = ensure_directory(parent_relative, optional=optional)
                if parent_directory is None:
                    return None
                names = {item[0]: item for item in enumerate_names(parent_directory)}
                entry = names.get(name)
                if entry is None:
                    if optional:
                        return None
                    raise SourceSnapshotError(f"source directory is missing: {relative}")
                if entry[2] or not entry[1]:
                    raise SourceSnapshotError(f"source directory is not a real directory: {relative}")
                handle: int | None = None
                try:
                    handle = _open_component(Path(name), parent_directory.handle, directory=True)
                    identity = _identity(handle, directory=True)
                    if not _matches(identity, entry[3]):
                        raise SourceSnapshotError(
                            f"source directory changed before open: {relative}"
                        )
                    directory = _HeldDirectory(relative, handle, identity)
                except Exception:
                    if handle is not None:
                        os.close(handle)
                    raise
                directories[relative] = directory
                return directory

            def capture_file(relative: str, *, optional: bool = False) -> None:
                parent_relative, _, name = relative.rpartition("/")
                parent_directory = ensure_directory(parent_relative, optional=optional)
                if parent_directory is None:
                    return
                names = {item[0]: item for item in enumerate_names(parent_directory)}
                entry = names.get(name)
                if entry is None:
                    if optional:
                        return
                    raise SourceSnapshotError(f"source file is missing: {relative}")
                if entry[2] or entry[1]:
                    raise SourceSnapshotError(f"source file is not a regular file: {relative}")
                handle: int | None = None
                try:
                    handle = _open_component(Path(name), parent_directory.handle, directory=False)
                    initial = _identity(handle, directory=False)
                    if not _matches(initial, entry[3]):
                        raise SourceSnapshotError(
                            f"source file changed before open: {relative}"
                        )
                    content = _read(handle)
                    final = _identity(handle, directory=False)
                    if not _matches(initial, final) or final.get("st_size") != len(content):
                        raise SourceSnapshotError(f"source file changed during capture: {relative}")
                    files[relative] = _HeldFile(relative, handle, initial, content)
                except Exception:
                    if handle is not None:
                        os.close(handle)
                    raise

            capture_file("skills/implement-gwo/SKILL.md", optional=True)
            capture_file("skills/orchestrator/SKILL.md", optional=True)

            def walk(relative: str) -> None:
                directory = ensure_directory(relative)
                assert directory is not None
                for name, is_directory, is_link, _entry_identity in enumerate_names(
                    directory
                ):
                    if is_link:
                        raise SourceSnapshotError(f"source tree contains a link: {relative}/{name}")
                    child = f"{relative}/{name}"
                    if is_directory:
                        ensure_directory(child)
                        walk(child)
                    elif name.endswith(".py"):
                        capture_file(child)

            if ensure_directory("skills/orchestrator/scripts/gwo_v8", optional=True) is not None:
                walk("skills/orchestrator/scripts/gwo_v8")
            return cls(
                root,
                files,
                directories,
                tuple(ancestors),
                tuple(root_parts),
                entries,
                borrowed_handles,
            )
        except Exception:
            handles: set[int] = set()
            for file in files.values():
                handles.add(file.handle)
            for directory in (*directories.values(), *ancestors, *independent_ancestors):
                handles.add(directory.handle)
            for handle in reversed(tuple(handles)):
                if handle in borrowed_handles:
                    continue
                try:
                    os.close(handle)
                except OSError:
                    pass
            raise

    def assert_stable(self) -> None:
        if self._closed:
            raise SourceSnapshotError("source snapshot is closed")
        if self._stability_suspension_depth:
            return
        fresh_ancestors: list[int] = []
        try:
            parent: int | None = None
            for index, part in enumerate(self.ancestor_parts):
                path = (
                    Path(part)
                    if index == 0
                    else Path(*self.ancestor_parts[: index + 1])
                )
                handle = _open_component(path, parent, directory=True)
                fresh_ancestors.append(handle)
                if not _matches(
                    _identity(handle, directory=True),
                    self.ancestors[index].identity,
                ):
                    raise SourceSnapshotError("source ancestor path changed")
                parent = handle
        finally:
            for handle in reversed(fresh_ancestors):
                try:
                    os.close(handle)
                except OSError:
                    pass
        if not _matches(
            _identity(self.directories[""].handle, directory=True),
            self.directories[""].identity,
        ):
            raise SourceSnapshotError("borrowed source root handle changed")
        for index in range(1, len(self.ancestors)):
            parent = self.ancestors[index - 1]
            expected = self.ancestors[index]
            current_handle = _open_component(
                Path(self.ancestor_parts[index]).name,
                parent.handle,
                directory=True,
            )
            try:
                if not _matches(_identity(current_handle, directory=True), expected.identity):
                    raise SourceSnapshotError("source ancestor changed after capture")
            finally:
                try:
                    os.close(current_handle)
                except OSError:
                    pass
        for directory in self.directories.values():
            if not _matches(_identity(directory.handle, directory=True), directory.identity):
                raise SourceSnapshotError(f"source directory changed: {directory.relative}")
            expected_entries = self.entries.get(directory.relative)
            if expected_entries is None:
                raise SourceSnapshotError(
                    f"source directory entries are not captured: {directory.relative}"
                )
            if _enumerate_directory_entries(directory.handle) != expected_entries:
                raise SourceSnapshotError(
                    f"source directory entries changed: {directory.relative}"
                )
            if directory.relative:
                parent_relative, _, name = directory.relative.rpartition("/")
                parent = self.directories.get(parent_relative)
                if parent is None:
                    raise SourceSnapshotError(f"source parent is not held: {directory.relative}")
                current_handle = _open_component(Path(name), parent.handle, directory=True)
                try:
                    if not _matches(_identity(current_handle, directory=True), directory.identity):
                        raise SourceSnapshotError(f"source directory path changed: {directory.relative}")
                finally:
                    try:
                        os.close(current_handle)
                    except OSError:
                        pass
        for file in self.files.values():
            current = _identity(file.handle, directory=False)
            content = _read(file.handle)
            after = _identity(file.handle, directory=False)
            parent_relative, _, name = file.relative.rpartition("/")
            parent = self.directories.get(parent_relative)
            if parent is None:
                raise SourceSnapshotError(f"source parent is not held: {file.relative}")
            current_handle = _open_component(Path(name), parent.handle, directory=False)
            try:
                path_identity = _identity(current_handle, directory=False)
                path_content = _read(current_handle)
                path_after = _identity(current_handle, directory=False)
            finally:
                try:
                    os.close(current_handle)
                except OSError:
                    pass
            if (
                not _matches(current, file.identity)
                or not _matches(after, file.identity)
                or after.get("st_size") != len(content)
                or content != file.content
                or not _matches(path_identity, file.identity)
                or not _matches(path_after, file.identity)
                or path_after.get("st_size") != len(path_content)
                or path_content != file.content
            ):
                raise SourceSnapshotError(f"source file changed: {file.relative}")

    @contextmanager
    def _stable_read_view(self):
        """Validate once around a read-only operation over the held tree."""

        self.assert_stable()
        self._stability_suspension_depth += 1
        completed = False
        try:
            yield self
            completed = True
        finally:
            self._stability_suspension_depth -= 1
            if completed:
                self.assert_stable()

    def digest(self) -> str:
        self.assert_stable()
        digest = hashlib.sha256()
        for relative, file in sorted(self.files.items()):
            encoded = relative.encode("utf-8")
            content = file.content.replace(b"\r\n", b"\n")
            digest.update(len(encoded).to_bytes(4, "big"))
            digest.update(encoded)
            digest.update(len(content).to_bytes(8, "big"))
            digest.update(content)
        value = digest.hexdigest()
        self.assert_stable()
        return value

    def bytes_for(self, relative: str) -> bytes:
        self.assert_stable()
        try:
            return self.files[relative].content
        except KeyError as error:
            raise SourceSnapshotError(f"source file is not in snapshot: {relative}") from error

    def has_file(self, relative: str) -> bool:
        self.assert_stable()
        return relative in self.files

    def has_directory(self, relative: str) -> bool:
        self.assert_stable()
        return relative in self.directories

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        handles: set[int] = set()
        for file in self.files.values():
            handles.add(file.handle)
        for directory in (*self.directories.values(), *self.ancestors):
            handles.add(directory.handle)
        for handle in reversed(tuple(handles)):
            if handle in self.borrowed_handles:
                continue
            try:
                os.close(handle)
            except OSError:
                pass

    def __enter__(self) -> "HeldSourceSnapshot":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()


def _windows_directory_entries(
    handle: int,
) -> tuple[tuple[str, bool, bool, Mapping[str, object]], ...]:
    try:
        import msvcrt

        class IoStatusBlock(ctypes.Structure):
            _fields_ = [("status", ctypes.c_void_p), ("information", ctypes.c_size_t)]

        ntdll = ctypes.WinDLL("ntdll")
        ntdll.NtQueryDirectoryFile.restype = ctypes.c_long
        ntdll.NtQueryDirectoryFile.argtypes = [
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.POINTER(IoStatusBlock),
            ctypes.c_void_p,
            ctypes.c_ulong,
            ctypes.c_int,
            ctypes.c_ubyte,
            ctypes.c_void_p,
            ctypes.c_ubyte,
        ]
        native = ctypes.c_void_p(msvcrt.get_osfhandle(handle))
        parent_identity = _identity(handle, directory=True)
        volume_id = parent_identity.get("volume_id")
        result: list[tuple[str, bool, bool, Mapping[str, object]]] = []
        restart = 1
        while True:
            buffer = ctypes.create_string_buffer(64 * 1024)
            status_block = IoStatusBlock()
            status = int(
                ntdll.NtQueryDirectoryFile(
                    native,
                    None,
                    None,
                    None,
                    ctypes.byref(status_block),
                    ctypes.byref(buffer),
                    ctypes.sizeof(buffer),
                    _WINDOWS_DIRECTORY_INFO_CLASS,
                    0,
                    None,
                    restart,
                )
            ) & 0xFFFFFFFF
            restart = 0
            if status == 0x80000006:
                break
            if status not in {0, 0x80000005}:
                raise OSError(status, "NtQueryDirectoryFile failed")
            data = buffer.raw[: int(status_block.information)]
            offset = 0
            saw_record = False
            while offset < len(data):
                if offset + _WINDOWS_DIRECTORY_RECORD_MIN_SIZE > len(data):
                    raise OSError("directory record is truncated")
                next_offset = struct.unpack_from("<I", data, offset)[0]
                attrs = struct.unpack_from("<I", data, offset + 56)[0]
                name_length = struct.unpack_from("<I", data, offset + 60)[0]
                file_id_start = offset + _WINDOWS_DIRECTORY_FILE_ID_OFFSET
                file_id_end = offset + _WINDOWS_DIRECTORY_FILE_ID_END
                file_id = data[file_id_start:file_id_end]
                start = offset + _WINDOWS_DIRECTORY_FILE_NAME_OFFSET
                end = start + name_length
                if (
                    len(file_id) != _WINDOWS_DIRECTORY_FILE_ID_SIZE
                    or not any(file_id)
                    or name_length % 2
                    or end > len(data)
                    or (
                        next_offset
                        and next_offset
                        < _WINDOWS_DIRECTORY_FILE_NAME_OFFSET + name_length
                    )
                    or (next_offset and offset + next_offset > len(data))
                ):
                    raise OSError("directory record is malformed")
                name = data[start:end].decode("utf-16-le")
                saw_record = True
                attributes = bool(attrs & 0x400)
                entry_identity = {
                    "volume_id": volume_id,
                    "file_id": file_id.hex(),
                }
                if name not in {".", ".."}:
                    result.append((name, bool(attrs & 0x10), attributes, entry_identity))
                if not next_offset:
                    offset = len(data)
                    break
                offset += next_offset
            if data and not saw_record:
                raise OSError("directory enumeration did not advance")
        return tuple(sorted(result))
    except (ImportError, OSError, UnicodeError, AttributeError, TypeError) as error:
        raise SourceSnapshotError("Windows source directory enumeration failed") from error
