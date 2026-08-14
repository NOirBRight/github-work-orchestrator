from __future__ import annotations

from contextlib import contextmanager
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import sys
from typing import Iterator

import pytest


ROOT = Path(__file__).resolve().parents[1]


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise AssertionError(f"module spec unavailable: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


runner = _load_module(
    "beta3_round2_contract_runner",
    ROOT / "scripts" / "run_beta3_live_guard.py",
)
release_subject = _load_module(
    "beta3_round2_contract_release_subject",
    ROOT / "scripts" / "beta3_release_subject.py",
)


@contextmanager
def _missing_posix_flag(module: object, name: str) -> Iterator[None]:
    module_os = module.os
    old_name = module_os.name
    old_open = module_os.open
    had_flag = hasattr(module_os, name)
    old_flag = getattr(module_os, name, None)
    module_os.name = "posix"
    if had_flag:
        delattr(module_os, name)

    def unexpected_open(*_args: object, **_kwargs: object) -> int:
        raise AssertionError("os.open was reached without a required flag")

    module_os.open = unexpected_open
    try:
        yield
    finally:
        module_os.open = old_open
        module_os.name = old_name
        if had_flag:
            setattr(module_os, name, old_flag)


@pytest.mark.parametrize(
    ("missing_flag", "directory"),
    (
        ("O_DIRECTORY", True),
        ("O_NOFOLLOW", True),
        ("O_NOFOLLOW", False),
        ("O_NONBLOCK", False),
    ),
)
def test_runner_path_open_fails_closed_for_each_required_posix_flag(
    tmp_path: Path,
    missing_flag: str,
    directory: bool,
):
    path = tmp_path if directory else tmp_path / "source.py"
    if not directory:
        path.write_bytes(b"value = 1\n")

    with _missing_posix_flag(runner, missing_flag):
        with pytest.raises(runner.RunnerError) as error:
            runner._open_path_handle(
                path,
                "ROUND2_REQUIRED_POSIX_FLAG",
                directory=directory,
            )

    assert error.value.code == "ROUND2_REQUIRED_POSIX_FLAG"


@pytest.mark.parametrize(
    ("missing_flag", "directory"),
    (
        ("O_DIRECTORY", True),
        ("O_NOFOLLOW", True),
        ("O_NOFOLLOW", False),
        ("O_NONBLOCK", False),
    ),
)
def test_release_subject_path_open_fails_closed_for_each_required_posix_flag(
    tmp_path: Path,
    missing_flag: str,
    directory: bool,
):
    path = tmp_path if directory else tmp_path / "source.py"
    if not directory:
        path.write_bytes(b"value = 1\n")

    with _missing_posix_flag(release_subject, missing_flag):
        with pytest.raises(release_subject.ReleaseSubjectError) as error:
            release_subject._open_path_handle(
                path,
                "ROUND2_REQUIRED_POSIX_FLAG",
                directory=directory,
            )

    assert error.value.code == "ROUND2_REQUIRED_POSIX_FLAG"


@pytest.mark.parametrize("missing_flag", ("O_DIRECTORY", "O_NOFOLLOW"))
def test_runner_held_directory_enumeration_fails_closed(
    tmp_path: Path,
    missing_flag: str,
):
    # The required-flag check must happen before the descriptor is used; a
    # sentinel keeps this contract test runnable on Windows.
    descriptor = 0
    with _missing_posix_flag(runner, missing_flag):
        with pytest.raises(runner.RunnerError) as error:
            runner._held_directory_entries(
                descriptor,
                "ROUND2_REQUIRED_POSIX_FLAG",
            )
    assert error.value.code == "ROUND2_REQUIRED_POSIX_FLAG"


@pytest.mark.parametrize("missing_flag", ("O_DIRECTORY", "O_NOFOLLOW"))
def test_release_subject_source_enumeration_fails_closed(
    tmp_path: Path,
    missing_flag: str,
):
    descriptor = 0
    with _missing_posix_flag(release_subject, missing_flag):
        with pytest.raises(release_subject.ReleaseSubjectError) as error:
            release_subject._enumerate_source_directory_entries(
                descriptor,
                "ROUND2_REQUIRED_POSIX_FLAG",
            )
    assert error.value.code == "ROUND2_REQUIRED_POSIX_FLAG"


@pytest.mark.parametrize("missing_flag", ("O_NOFOLLOW", "O_NONBLOCK"))
def test_release_subject_absence_probe_fails_closed(
    tmp_path: Path,
    missing_flag: str,
):
    descriptor = 0
    lease = SimpleNamespace(handles=(descriptor,), assert_stable=lambda: None)
    with _missing_posix_flag(release_subject, missing_flag):
        old_path = release_subject.Path
        release_subject.Path = lambda _value: SimpleNamespace(name="subject")
        try:
            with pytest.raises(release_subject.ReleaseSubjectError) as error:
                release_subject._require_subject_absent_held(
                    str(tmp_path / "subject"),
                    lease,
                )
        finally:
            release_subject.Path = old_path
    assert error.value.code == "RELEASE_SUBJECT_PATH_INVALID"


@pytest.mark.parametrize("missing_flag", ("O_DIRECTORY", "O_NOFOLLOW"))
def test_release_subject_cleanup_directory_open_fails_closed(
    tmp_path: Path,
    missing_flag: str,
):
    descriptor = 0
    with _missing_posix_flag(release_subject, missing_flag):
        with pytest.raises(release_subject.ReleaseSubjectError) as error:
            release_subject._open_posix_cleanup_directory(descriptor)
    assert error.value.code == "RELEASE_SUBJECT_WRITE_FAILED"
