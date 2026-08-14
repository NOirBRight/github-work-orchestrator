from __future__ import annotations

import ast
import ctypes
import os
import struct
import sys
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for scripts_path in (ROOT / "scripts", ROOT / "skills" / "orchestrator" / "scripts"):
    if str(scripts_path) not in sys.path:
        sys.path.insert(0, str(scripts_path))

import gwo_v8.cutover_guard as cutover_guard  # noqa: E402
import gwo_v8._source_snapshot as source_snapshot  # noqa: E402
from gwo_v8.cutover_guard import (  # noqa: E402
    CutoverGuardError,
    CutoverSubject,
    ProductionPathScanner,
    ReadOnlyPackageValidator,
    source_tree_digest,
)


def test_production_path_scanner_reports_a_reachable_predecessor_edge(tmp_path):
    package = tmp_path / "skills" / "orchestrator" / "scripts" / "gwo_v8"
    package.mkdir(parents=True)
    (package / "public.py").write_text(
        "from .legacy import LegacyWriter\n\n"
        "def start():\n    return LegacyWriter().write()\n",
        encoding="utf-8",
    )
    (package / "legacy.py").write_text(
        "class LegacyWriter:\n"
        "    def write(self):\n        return None\n",
        encoding="utf-8",
    )
    subject = scanned_subject(tmp_path, ("gwo_v8.public:start",))

    readback = ProductionPathScanner(package_root=tmp_path).read(subject)

    assert readback.reachable_legacy_writer_refs == (
        "gwo_v8.legacy:LegacyWriter.write",
    )


def test_production_path_scanner_rejects_a_missing_entry_module_fail_closed(tmp_path):
    package = tmp_path / "skills" / "orchestrator" / "scripts" / "gwo_v8"
    package.mkdir(parents=True)
    (package / "public.py").write_text(
        "def start():\n    return None\n",
        encoding="utf-8",
    )
    subject = scanned_subject(tmp_path, ("gwo_v8.missing:start",))

    with pytest.raises(CutoverGuardError) as error:
        ProductionPathScanner(package_root=tmp_path).read(subject)

    assert error.value.code == "CUTOVER_COMPATIBILITY_AUDIT_INVALID"


def test_production_path_scanner_rejects_an_unresolved_alias_edge_fail_closed(tmp_path):
    package = tmp_path / "skills" / "orchestrator" / "scripts" / "gwo_v8"
    package.mkdir(parents=True)
    (package / "public.py").write_text(
        "from .legacy import LegacyWriter as Writer\n\n"
        "def start():\n"
        "    factory = Writer\n"
        "    return factory().write()\n",
        encoding="utf-8",
    )
    (package / "legacy.py").write_text(
        "class LegacyWriter:\n"
        "    def write(self):\n        return None\n",
        encoding="utf-8",
    )
    subject = scanned_subject(tmp_path, ("gwo_v8.public:start",))

    with pytest.raises(CutoverGuardError) as error:
        ProductionPathScanner(package_root=tmp_path).read(subject)

    assert error.value.code == "CUTOVER_COMPATIBILITY_AUDIT_INVALID"


def test_source_digest_and_path_scanner_use_captured_source_bytes(tmp_path, monkeypatch):
    package = tmp_path / "skills" / "orchestrator" / "scripts" / "gwo_v8"
    package.mkdir(parents=True)
    (tmp_path / "skills" / "implement-gwo").mkdir(parents=True)
    (tmp_path / "skills" / "implement-gwo" / "SKILL.md").write_text(
        "# implement-gwo\n", encoding="utf-8"
    )
    (tmp_path / "skills" / "orchestrator" / "SKILL.md").write_text(
        "# orchestrator\n", encoding="utf-8"
    )
    (package / "public.py").write_text(
        "from .legacy import LegacyWriter\n\n"
        "def start():\n    return LegacyWriter().write()\n",
        encoding="utf-8",
    )
    (package / "legacy.py").write_text(
        "class LegacyWriter:\n"
        "    def write(self):\n        return None\n",
        encoding="utf-8",
    )
    subject = scanned_subject(tmp_path, ("gwo_v8.public:start",))
    parsed: list[object] = []
    original_parse = ast.parse

    def capture_parse(source, *args, **kwargs):
        parsed.append(source)
        return original_parse(source, *args, **kwargs)

    monkeypatch.setattr(cutover_guard.ast, "parse", capture_parse)
    monkeypatch.setattr(
        Path,
        "rglob",
        lambda *_args, **_kwargs: pytest.fail("source traversal must be held"),
    )
    monkeypatch.setattr(
        Path,
        "read_bytes",
        lambda *_args, **_kwargs: pytest.fail("source bytes must be held"),
    )
    monkeypatch.setattr(
        Path,
        "read_text",
        lambda *_args, **_kwargs: pytest.fail("source text must be held"),
    )

    readback = ProductionPathScanner(package_root=tmp_path).read(subject)

    assert readback.reachable_legacy_writer_refs == (
        "gwo_v8.legacy:LegacyWriter.write",
    )
    assert parsed and all(type(source) is bytes for source in parsed)


@pytest.mark.skipif(os.name == "nt", reason="POSIX O_NOFOLLOW contract")
def test_source_snapshot_rejects_a_symlinked_audited_ancestor(tmp_path):
    root = tmp_path / "root"
    package = tmp_path / "real-scripts" / "gwo_v8"
    (root / "skills" / "implement-gwo").mkdir(parents=True)
    (root / "skills" / "orchestrator").mkdir(parents=True)
    (package).mkdir(parents=True)
    (root / "skills" / "implement-gwo" / "SKILL.md").write_text(
        "# implement-gwo\n", encoding="utf-8"
    )
    (root / "skills" / "orchestrator" / "SKILL.md").write_text(
        "# orchestrator\n", encoding="utf-8"
    )
    (package / "public.py").write_text("def start():\n    return None\n", encoding="utf-8")
    (root / "skills" / "orchestrator" / "scripts").symlink_to(
        package.parent, target_is_directory=True
    )

    with pytest.raises(CutoverGuardError):
        source_tree_digest(root)


@pytest.mark.skipif(os.name == "nt", reason="POSIX descriptor identity contract")
def test_path_scanner_rejects_file_replacement_after_captured_parse(tmp_path):
    package = tmp_path / "skills" / "orchestrator" / "scripts" / "gwo_v8"
    package.mkdir(parents=True)
    (tmp_path / "skills" / "implement-gwo").mkdir(parents=True)
    (tmp_path / "skills" / "implement-gwo" / "SKILL.md").write_text(
        "# implement-gwo\n", encoding="utf-8"
    )
    (tmp_path / "skills" / "orchestrator" / "SKILL.md").write_text(
        "# orchestrator\n", encoding="utf-8"
    )
    public = package / "public.py"
    public.write_text(
        "from .legacy import LegacyWriter\n\n"
        "def start():\n    return LegacyWriter().write()\n",
        encoding="utf-8",
    )
    (package / "legacy.py").write_text(
        "class LegacyWriter:\n"
        "    def write(self):\n        return None\n",
        encoding="utf-8",
    )
    subject = scanned_subject(tmp_path, ("gwo_v8.public:start",))
    original_parse = cutover_guard.ast.parse
    replaced = False

    def replace_after_capture(source, *args, **kwargs):
        nonlocal replaced
        if not replaced:
            replacement = public.with_suffix(".replacement")
            replacement.write_bytes(public.read_bytes() + b"\n")
            os.replace(replacement, public)
            replaced = True
        return original_parse(source, *args, **kwargs)

    cutover_guard.ast.parse = replace_after_capture
    try:
        with pytest.raises(CutoverGuardError):
            ProductionPathScanner(package_root=tmp_path).read(subject)
    finally:
        cutover_guard.ast.parse = original_parse


@pytest.mark.skipif(os.name == "nt", reason="POSIX descriptor identity contract")
def test_path_scanner_checks_final_source_stability_after_ast_processing(tmp_path):
    package = tmp_path / "skills" / "orchestrator" / "scripts" / "gwo_v8"
    package.mkdir(parents=True)
    (tmp_path / "skills" / "implement-gwo").mkdir(parents=True)
    (tmp_path / "skills" / "implement-gwo" / "SKILL.md").write_text(
        "# implement-gwo\n", encoding="utf-8"
    )
    (tmp_path / "skills" / "orchestrator" / "SKILL.md").write_text(
        "# orchestrator\n", encoding="utf-8"
    )
    public = package / "public.py"
    public.write_text("def start():\n    return None\n", encoding="utf-8")
    subject = scanned_subject(tmp_path, ("gwo_v8.public:start",))
    original_parse = cutover_guard.ast.parse
    replaced = False

    def replace_after_parse(source, *args, **kwargs):
        nonlocal replaced
        tree = original_parse(source, *args, **kwargs)
        if not replaced:
            replacement = public.with_suffix(".replacement")
            replacement.write_bytes(public.read_bytes() + b"\n")
            os.replace(replacement, public)
            replaced = True
        return tree

    cutover_guard.ast.parse = replace_after_parse
    try:
        with pytest.raises(CutoverGuardError):
            ProductionPathScanner(package_root=tmp_path).read(subject)
    finally:
        cutover_guard.ast.parse = original_parse


def test_path_scanner_rejects_a_symlinked_package_root_without_resolving_it(tmp_path):
    real_root = tmp_path / "real"
    package = real_root / "skills" / "orchestrator" / "scripts" / "gwo_v8"
    package.mkdir(parents=True)
    (real_root / "skills" / "implement-gwo").mkdir(parents=True)
    (real_root / "skills" / "implement-gwo" / "SKILL.md").write_text(
        "# implement-gwo\n", encoding="utf-8"
    )
    (real_root / "skills" / "orchestrator" / "SKILL.md").write_text(
        "# orchestrator\n", encoding="utf-8"
    )
    (package / "public.py").write_text("def start():\n    return None\n", encoding="utf-8")
    linked_root = tmp_path / "linked"
    try:
        linked_root.symlink_to(real_root, target_is_directory=True)
    except (NotImplementedError, OSError):
        pytest.skip("symbolic links are unavailable")

    with pytest.raises(CutoverGuardError):
        source_tree_digest(linked_root)


@pytest.mark.skipif(os.name == "nt", reason="POSIX descriptor identity contract")
def test_source_snapshot_rejects_replacement_between_entry_enumeration_and_open(
    tmp_path, monkeypatch
):
    package = tmp_path / "skills" / "orchestrator" / "scripts" / "gwo_v8"
    package.mkdir(parents=True)
    (tmp_path / "skills" / "implement-gwo").mkdir(parents=True)
    (tmp_path / "skills" / "implement-gwo" / "SKILL.md").write_text(
        "# implement-gwo\n", encoding="utf-8"
    )
    (tmp_path / "skills" / "orchestrator" / "SKILL.md").write_text(
        "# orchestrator\n", encoding="utf-8"
    )
    public = package / "public.py"
    public.write_text("def start():\n    return None\n", encoding="utf-8")
    original_open = source_snapshot._open_component
    replaced = False

    def replace_before_open(path, parent, *, directory):
        nonlocal replaced
        if not directory and Path(path).name == "public.py" and not replaced:
            replacement = public.with_suffix(".replacement")
            replacement.write_bytes(public.read_bytes() + b"\n")
            os.replace(replacement, public)
            replaced = True
        return original_open(path, parent, directory=directory)

    monkeypatch.setattr(source_snapshot, "_open_component", replace_before_open)
    with pytest.raises(CutoverGuardError):
        source_tree_digest(tmp_path)


@pytest.mark.skipif(os.name == "nt", reason="POSIX descriptor identity contract")
def test_source_snapshot_binds_a_borrowed_root_handle_to_package_root(tmp_path):
    real_root = tmp_path / "real"
    package = real_root / "skills" / "orchestrator" / "scripts" / "gwo_v8"
    package.mkdir(parents=True)
    (real_root / "skills" / "implement-gwo").mkdir(parents=True)
    (real_root / "skills" / "implement-gwo" / "SKILL.md").write_text(
        "# implement-gwo\n", encoding="utf-8"
    )
    (real_root / "skills" / "orchestrator" / "SKILL.md").write_text(
        "# orchestrator\n", encoding="utf-8"
    )
    (package / "public.py").write_text("def start():\n    return None\n", encoding="utf-8")
    linked_root = tmp_path / "linked"
    try:
        linked_root.symlink_to(real_root, target_is_directory=True)
    except (NotImplementedError, OSError):
        pytest.skip("symbolic links are unavailable")
    root_handle = os.open(real_root, os.O_RDONLY)
    try:
        with pytest.raises(CutoverGuardError):
            source_tree_digest(linked_root, root_handle=root_handle)
    finally:
        os.close(root_handle)


@pytest.mark.skipif(os.name == "nt", reason="POSIX directory offset contract")
def test_source_snapshot_directory_enumeration_does_not_reuse_open_file_offset(tmp_path):
    directory = tmp_path / "directory"
    directory.mkdir()
    (directory / "a").write_text("a", encoding="utf-8")
    (directory / "b").write_text("b", encoding="utf-8")
    handle = os.open(directory, os.O_RDONLY)
    try:
        first = source_snapshot._enumerate_directory_entries(handle)
        second = source_snapshot._enumerate_directory_entries(handle)
    finally:
        os.close(handle)
    assert first == second


@pytest.mark.skipif(os.name == "nt", reason="POSIX descriptor identity contract")
def test_source_snapshot_closes_an_ancestor_handle_when_identity_fails(tmp_path, monkeypatch):
    snapshot = source_snapshot
    opened: list[int] = []
    closed: list[int] = []
    original_open = snapshot._open_component
    original_close = os.close

    def track_open(path, parent, *, directory):
        handle = original_open(path, parent, directory=directory)
        opened.append(handle)
        return handle

    def track_close(handle):
        closed.append(handle)
        return original_close(handle)

    monkeypatch.setattr(snapshot, "_open_component", track_open)
    monkeypatch.setattr(os, "close", track_close)

    def fail_identity(_descriptor, *, directory):
        del directory
        raise snapshot.SourceSnapshotError("identity failed")

    monkeypatch.setattr(snapshot, "_identity", fail_identity)

    with pytest.raises(snapshot.SourceSnapshotError):
        snapshot.HeldSourceSnapshot.capture(tmp_path)

    assert opened
    assert set(opened) <= set(closed)


@pytest.mark.skipif(os.name == "nt", reason="POSIX descriptor identity contract")
def test_source_snapshot_closes_a_borrowed_root_independent_ancestor_on_identity_failure(
    tmp_path, monkeypatch
):
    snapshot = source_snapshot
    root_handle = os.open(tmp_path, os.O_RDONLY)
    opened: list[int] = []
    closed: list[int] = []
    original_open = snapshot._open_component
    original_close = os.close
    original_identity = snapshot._identity

    def track_open(path, parent, *, directory):
        handle = original_open(path, parent, directory=directory)
        opened.append(handle)
        return handle

    def track_close(handle):
        closed.append(handle)
        return original_close(handle)

    calls = 0

    def fail_independent_ancestor(descriptor, *, directory):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise snapshot.SourceSnapshotError("identity failed")
        return original_identity(descriptor, directory=directory)

    monkeypatch.setattr(snapshot, "_open_component", track_open)
    monkeypatch.setattr(os, "close", track_close)
    monkeypatch.setattr(snapshot, "_identity", fail_independent_ancestor)

    try:
        with pytest.raises(snapshot.SourceSnapshotError):
            snapshot.HeldSourceSnapshot.capture(tmp_path, root_handle=root_handle)
        assert opened
        assert set(opened) <= set(closed)
        assert root_handle not in closed
    finally:
        original_close(root_handle)


@pytest.mark.skipif(os.name == "nt", reason="POSIX descriptor identity contract")
def test_source_snapshot_closes_all_successful_borrowed_root_ancestors_after_later_identity_failure(
    tmp_path, monkeypatch
):
    snapshot = source_snapshot
    root_handle = os.open(tmp_path, os.O_RDONLY)
    opened: list[int] = []
    closed: list[int] = []
    original_open = snapshot._open_component
    original_close = os.close
    original_identity = snapshot._identity

    def track_open(path, parent, *, directory):
        handle = original_open(path, parent, directory=directory)
        opened.append(handle)
        return handle

    def track_close(handle):
        closed.append(handle)
        return original_close(handle)

    calls = 0

    def fail_after_one_independent_ancestor(descriptor, *, directory):
        nonlocal calls
        calls += 1
        if calls == 3:
            raise snapshot.SourceSnapshotError("identity failed")
        return original_identity(descriptor, directory=directory)

    monkeypatch.setattr(snapshot, "_open_component", track_open)
    monkeypatch.setattr(os, "close", track_close)
    monkeypatch.setattr(snapshot, "_identity", fail_after_one_independent_ancestor)

    try:
        with pytest.raises(snapshot.SourceSnapshotError):
            snapshot.HeldSourceSnapshot.capture(tmp_path, root_handle=root_handle)
        assert len(opened) >= 2
        assert set(opened) <= set(closed)
        assert root_handle not in closed
    finally:
        for handle in opened:
            if handle not in closed:
                try:
                    original_close(handle)
                except OSError:
                    pass
        original_close(root_handle)


@pytest.mark.skipif(os.name == "nt", reason="POSIX FIFO contract")
def test_source_snapshot_opens_a_fifo_entry_without_blocking_before_fail_closed(
    tmp_path, monkeypatch
):
    if not hasattr(os, "O_NONBLOCK"):
        pytest.skip("O_NONBLOCK is unavailable")
    snapshot = source_snapshot
    package = tmp_path / "skills" / "orchestrator" / "scripts" / "gwo_v8"
    package.mkdir(parents=True)
    public = package / "public.py"
    public.write_text("def start():\n    return None\n", encoding="utf-8")
    original_open = snapshot._open_component
    replaced = False
    writer: threading.Thread | None = None

    def delayed_writer() -> None:
        time.sleep(0.4)
        try:
            descriptor = os.open(public, os.O_WRONLY | os.O_NONBLOCK)
        except OSError:
            return
        os.close(descriptor)

    def replace_before_open(path, parent, *, directory):
        nonlocal replaced, writer
        if not directory and Path(path).name == "public.py" and not replaced:
            public.unlink()
            os.mkfifo(public)
            writer = threading.Thread(target=delayed_writer)
            writer.start()
            replaced = True
        return original_open(path, parent, directory=directory)

    monkeypatch.setattr(snapshot, "_open_component", replace_before_open)
    started = time.monotonic()
    with pytest.raises(snapshot.SourceSnapshotError):
        snapshot.HeldSourceSnapshot.capture(tmp_path)
    elapsed = time.monotonic() - started
    if writer is not None:
        writer.join(timeout=1.0)
        assert not writer.is_alive()
    assert elapsed < 0.2


def test_windows_identity_comparison_requires_authoritative_128_bit_file_id(
    monkeypatch,
):
    snapshot = source_snapshot
    source = Path(snapshot.__file__).read_text(encoding="utf-8")
    assert "file_id_64" not in source
    monkeypatch.setattr(snapshot.os, "name", "nt")

    weak = {"volume_id": 1, "file_id_64": "0123456789abcdef"}
    with pytest.raises(snapshot.SourceSnapshotError):
        snapshot._matches(weak, weak)

    left = {"volume_id": 1, "file_id": "01" * 16, "file_id_64": "same"}
    right = {"volume_id": 1, "file_id": "02" * 16, "file_id_64": "same"}
    assert not snapshot._matches(left, right)


class _FakeWindowsQuery:
    def __init__(self, payload: bytes):
        self.payload = payload
        self.calls: list[int] = []

        class IoStatusBlock(ctypes.Structure):
            _fields_ = [
                ("status", ctypes.c_void_p),
                ("information", ctypes.c_size_t),
            ]

        self._io_status_block = IoStatusBlock

    def __call__(self, *args):
        self.calls.append(args[7])
        if len(self.calls) == 1:
            ctypes.memmove(args[5], self.payload, len(self.payload))
            status_block = ctypes.cast(
                args[4], ctypes.POINTER(self._io_status_block)
            )
            status_block.contents.information = len(self.payload)
            return 0
        return 0x80000006


def _windows_directory_query(monkeypatch, payload: bytes) -> _FakeWindowsQuery:
    query = _FakeWindowsQuery(payload)
    fake_ntdll = type("FakeNtdll", (), {})()
    fake_ntdll.NtQueryDirectoryFile = query
    monkeypatch.setattr(source_snapshot.ctypes, "WinDLL", lambda *_args: fake_ntdll)
    monkeypatch.setattr(
        source_snapshot,
        "_identity",
        lambda _handle, *, directory: {
            "volume_id": 7,
            "file_id": "ff" * 16,
        },
    )
    monkeypatch.setattr(source_snapshot.os, "name", "nt")
    return query


def _file_id_extd_dir_info_record(
    name: str,
    file_id: bytes,
    *,
    attributes: int = 0,
    next_offset: int = 0,
) -> bytes:
    assert len(file_id) == 16
    encoded_name = name.encode("utf-16-le")
    record_size = 88 + len(encoded_name)
    if next_offset:
        assert next_offset >= record_size
        record_size = next_offset
    record = bytearray(record_size)
    struct.pack_into("<I", record, 0, next_offset)
    struct.pack_into("<I", record, 56, attributes)
    struct.pack_into("<I", record, 60, len(encoded_name))
    record[72:88] = file_id
    record[88 : 88 + len(encoded_name)] = encoded_name
    return bytes(record)


def test_windows_extd_directory_parser_has_the_exact_128_bit_structure_seam():
    source = Path(source_snapshot.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    assignments = {
        target.id: node.value.value
        for node in tree.body
        if isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(target := node.targets[0], ast.Name)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, int)
    }

    assert assignments["_WINDOWS_DIRECTORY_INFO_CLASS"] == 60
    assert assignments["_WINDOWS_DIRECTORY_RECORD_MIN_SIZE"] == 88
    assert assignments["_WINDOWS_DIRECTORY_FILE_ID_OFFSET"] == 72
    assert assignments["_WINDOWS_DIRECTORY_FILE_ID_SIZE"] == 16
    assert assignments["_WINDOWS_DIRECTORY_FILE_ID_END"] == 88
    assert assignments["_WINDOWS_DIRECTORY_FILE_NAME_OFFSET"] == 88
    assert "file_id_64" not in source
    assert "data[offset + 96 : offset + 104]" not in source
    assert "data[offset + 88 : offset + 96]" not in source

    windows_parser = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef)
        and node.name == "_windows_directory_entries"
    )
    info_class_args = [
        call.args[7]
        for call in ast.walk(windows_parser)
        if isinstance(call, ast.Call)
        and isinstance(call.func, ast.Attribute)
        and call.func.attr == "NtQueryDirectoryFile"
    ]
    assert len(info_class_args) == 1
    assert isinstance(info_class_args[0], ast.Name)
    assert assignments[info_class_args[0].id] == 60


def test_windows_directory_enumeration_uses_extd_info_class_60_and_parses_file_id_128(
    monkeypatch,
):
    first_id = bytes(range(1, 17))
    second_id = bytes(range(17, 33))
    first = _file_id_extd_dir_info_record(
        "z.py", first_id, next_offset=120
    )
    second = _file_id_extd_dir_info_record(
        "a.py", second_id, attributes=0x400
    )
    query = _windows_directory_query(monkeypatch, first + second)

    entries = source_snapshot._windows_directory_entries(0)

    assert query.calls == [60, 60]
    assert [entry[0] for entry in entries] == ["a.py", "z.py"]
    assert entries[0][2] is True
    assert entries[0][3]["volume_id"] == 7
    assert entries[0][3]["file_id"] == second_id.hex()
    assert entries[1][3]["file_id"] == first_id.hex()


@pytest.mark.parametrize(
    "payload",
    (
        b"\x00" * 103,
        _file_id_extd_dir_info_record("entry.py", b"\x01" * 16)[:87],
        _file_id_extd_dir_info_record("entry.py", b"\x01" * 16)[:88],
        bytes(
            bytearray(
                _file_id_extd_dir_info_record("entry.py", b"\x01" * 16)
            )[:72]
            + b"\x00" * 16
            + "entry.py".encode("utf-16-le")
        ),
    ),
)
def test_windows_directory_enumeration_rejects_missing_truncated_or_non_16_byte_file_id(
    monkeypatch, payload
):
    _windows_directory_query(monkeypatch, payload)

    with pytest.raises(source_snapshot.SourceSnapshotError):
        source_snapshot._windows_directory_entries(0)


def test_windows_directory_enumeration_rejects_a_malformed_file_id_slice(
    monkeypatch,
):
    payload = _file_id_extd_dir_info_record("entry.py", b"\x00" * 16)
    _windows_directory_query(monkeypatch, payload)

    with pytest.raises(source_snapshot.SourceSnapshotError):
        source_snapshot._windows_directory_entries(0)


@pytest.mark.skipif(os.name == "nt", reason="POSIX directory enumeration contract")
def test_source_snapshot_maps_posix_duplicate_open_oserror(
    tmp_path, monkeypatch
):
    snapshot = source_snapshot
    directory_handle = os.open(tmp_path, os.O_RDONLY)
    original_open = os.open

    def fail_duplicate(path, flags, *args, **kwargs):
        if path == "." and kwargs.get("dir_fd") == directory_handle:
            raise OSError("duplicate open failed")
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", fail_duplicate)
    try:
        with pytest.raises(snapshot.SourceSnapshotError):
            snapshot._enumerate_directory_entries(directory_handle)
    finally:
        os.close(directory_handle)


@pytest.mark.skipif(os.name == "nt", reason="POSIX descriptor identity contract")
def test_source_snapshot_closes_a_nested_directory_handle_on_identity_failure(
    tmp_path, monkeypatch
):
    snapshot = source_snapshot
    (tmp_path / "skills").mkdir()
    target = os.stat(tmp_path / "skills", follow_symlinks=False)
    opened: list[int] = []
    closed: list[int] = []
    original_open = snapshot._open_component
    original_close = os.close
    original_identity = snapshot._identity
    failed_handle: int | None = None

    def track_open(path, parent, *, directory):
        handle = original_open(path, parent, directory=directory)
        opened.append(handle)
        return handle

    def track_close(handle):
        closed.append(handle)
        return original_close(handle)

    def fail_nested_identity(descriptor, *, directory):
        nonlocal failed_handle
        observed = os.fstat(descriptor)
        if (
            directory
            and observed.st_dev == target.st_dev
            and observed.st_ino == target.st_ino
        ):
            failed_handle = descriptor
            raise snapshot.SourceSnapshotError("identity failed")
        return original_identity(descriptor, directory=directory)

    monkeypatch.setattr(snapshot, "_open_component", track_open)
    monkeypatch.setattr(os, "close", track_close)
    monkeypatch.setattr(snapshot, "_identity", fail_nested_identity)

    with pytest.raises(snapshot.SourceSnapshotError):
        snapshot.HeldSourceSnapshot.capture(tmp_path)

    assert failed_handle is not None
    assert failed_handle in closed


def test_source_snapshot_posix_open_accepts_string_component_with_parent_descriptor(
    monkeypatch,
):
    snapshot = source_snapshot
    calls: list[tuple[object, int, int | None]] = []

    monkeypatch.setattr(snapshot.os, "name", "posix")
    monkeypatch.setattr(snapshot.os, "O_NOFOLLOW", 0x100, raising=False)
    monkeypatch.setattr(snapshot.os, "O_DIRECTORY", 0x200, raising=False)

    def fake_open(path, flags, *, dir_fd=None):
        calls.append((path, flags, dir_fd))
        return 17

    monkeypatch.setattr(snapshot.os, "open", fake_open)

    assert snapshot._open_component("child", 41, directory=True) == 17
    assert calls == [("child", os.O_RDONLY | 0x100 | 0x200, 41)]


@pytest.mark.skipif(os.name == "nt", reason="POSIX regular-file contract")
def test_source_snapshot_rejects_non_regular_files_before_reading(tmp_path):
    snapshot = source_snapshot
    fifo = tmp_path / "source.fifo"
    os.mkfifo(fifo)
    directory_handle = os.open(tmp_path, os.O_RDONLY)
    try:
        with pytest.raises(snapshot.SourceSnapshotError):
            snapshot._enumerate_directory_entries(directory_handle)
    finally:
        os.close(directory_handle)

    fifo_handle = os.open(fifo, os.O_RDONLY | os.O_NONBLOCK)
    try:
        with pytest.raises(snapshot.SourceSnapshotError):
            snapshot._identity(fifo_handle, directory=False)
    finally:
        os.close(fifo_handle)


@pytest.mark.skipif(os.name == "nt", reason="POSIX no-follow contract")
@pytest.mark.parametrize("missing_flag", ("O_NOFOLLOW", "O_DIRECTORY"))
def test_source_snapshot_fails_closed_when_posix_required_flag_is_missing(
    tmp_path, monkeypatch, missing_flag
):
    if not hasattr(os, missing_flag):
        pytest.skip(f"{missing_flag} is unavailable")
    snapshot = source_snapshot
    directory = tmp_path / "directory"
    directory.mkdir()
    monkeypatch.delattr(os, missing_flag)

    with pytest.raises(snapshot.SourceSnapshotError):
        snapshot._open_component(directory, None, directory=True)


@pytest.mark.skipif(os.name == "nt", reason="POSIX descriptor offset contract")
def test_source_snapshot_directory_enumeration_does_not_use_shared_dup_offset(
    tmp_path, monkeypatch
):
    snapshot = source_snapshot
    directory = tmp_path / "directory"
    directory.mkdir()
    (directory / "entry").write_text("entry", encoding="utf-8")
    handle = os.open(directory, os.O_RDONLY)
    monkeypatch.setattr(
        os,
        "dup",
        lambda *_args, **_kwargs: pytest.fail("directory enumeration must not dup the held fd"),
    )
    try:
        assert snapshot._enumerate_directory_entries(handle)
    finally:
        os.close(handle)


def test_package_validator_detects_manifest_drift_without_rewriting_any_file(tmp_path):
    source = make_two_package_tree(tmp_path / "source")
    installed = make_installed_surfaces(tmp_path)
    manifest = source / "implement-gwo" / ".skill-package.json"
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace(
            "8.0.0",
            "8.0.0-drift",
        ),
        encoding="utf-8",
    )
    drifted = manifest.read_bytes()

    readback = ReadOnlyPackageValidator(
        source_root=source,
        install_roots=installed,
    ).read(static_subject(tmp_path, ()))

    assert "source:implement-gwo" in readback.drift
    assert manifest.read_bytes() == drifted
    assert not (source / "implement-gwo" / ".skill-package.json.tmp").exists()


def test_package_validator_reads_all_install_surfaces_and_never_installs(tmp_path, monkeypatch):
    source = make_two_package_tree(tmp_path / "source")
    installed = make_installed_surfaces(tmp_path)
    writes = []
    monkeypatch.setattr(
        "shutil.copytree",
        lambda *args, **kwargs: writes.append("copytree"),
    )
    monkeypatch.setattr(
        "os.replace",
        lambda *args, **kwargs: writes.append("replace"),
    )

    readback = ReadOnlyPackageValidator(
        source_root=source,
        install_roots=installed,
    ).read(static_subject(tmp_path, ()))

    assert readback.drift == ()
    assert {item.package_name for item in readback.installed_packages} == {
        "implement-gwo",
        "orchestrator",
    }
    assert writes == []


def static_subject(root: Path, entry_refs: tuple[str, ...]) -> CutoverSubject:
    del root
    return CutoverSubject(
        repository="owner/repo",
        control_branch="gwo-control",
        target_branch="main",
        source_writer_generation="v6.1",
        target_writer_generation="v8",
        store_generation="store:v8:0001",
        source_commit="a" * 40,
        source_tree_digest="b" * 64,
        production_entry_refs=entry_refs,
    )


def scanned_subject(root: Path, entry_refs: tuple[str, ...]) -> CutoverSubject:
    from dataclasses import replace
    from gwo_v8.cutover_guard import source_tree_digest

    subject = static_subject(root, entry_refs)
    return replace(subject, source_tree_digest=source_tree_digest(root))


def make_two_package_tree(root: Path) -> Path:
    import json
    from scripts.sync_orchestrator import expected_manifest

    root.mkdir(parents=True, exist_ok=True)
    for package in ("implement-gwo", "orchestrator"):
        package_root = root / package
        package_root.mkdir()
        (package_root / "SKILL.md").write_text(
            f"# {package}\n", encoding="utf-8"
        )
        (package_root / ".skill-package.json").write_text(
            json.dumps(expected_manifest(package_root), indent=2, sort_keys=True)
            + "\n",
            encoding="utf-8",
        )
    return root


def make_installed_surfaces(root: Path) -> dict[str, Path]:
    surfaces = {
        ".agents": root / ".agents" / "skills",
        ".codex": root / ".codex" / "skills",
        ".claude": root / ".claude" / "skills",
    }
    for path in surfaces.values():
        path.mkdir(parents=True, exist_ok=True)
    source = root / "source"
    if not source.is_dir():
        make_two_package_tree(source)
    for surface in surfaces.values():
        for package in ("implement-gwo", "orchestrator"):
            target = surface / package
            target.mkdir()
            for item in (source / package).iterdir():
                target.joinpath(item.name).write_bytes(item.read_bytes())
    return surfaces
