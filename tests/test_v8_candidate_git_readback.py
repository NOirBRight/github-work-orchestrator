from __future__ import annotations

import base64
from dataclasses import dataclass
from pathlib import Path
import subprocess

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "orchestrator" / "scripts"
import sys

if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from gwo_v8.candidate_gate import CandidateDiffRecordV1, CandidateGateError
from gwo_v8.candidate_git import CandidateBasePort, GitCandidateReader


@dataclass(frozen=True)
class GitRepositoryFixture:
    path: Path
    name: str
    base_commit_oid: str
    base_tree_oid: str
    candidate_commit_oid: str
    candidate_tree_oid: str

    def read_base(self, repository: str) -> tuple[str, str]:
        if repository != self.name:
            raise AssertionError("Candidate reader requested another repository")
        return self.base_commit_oid, self.base_tree_oid


def run_git(repository: Path, *args: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ["git", "-C", str(repository), *args],
        check=True,
        capture_output=True,
    )


def run_git_input(
    repository: Path,
    *args: str,
    input_bytes: bytes,
) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ["git", "-C", str(repository), *args],
        input=input_bytes,
        check=True,
        capture_output=True,
    )


def encode_raw_path(raw_path: bytes) -> str:
    return base64.urlsafe_b64encode(raw_path).decode("ascii").rstrip("=")


def write_tree(
    repository: Path,
    entries: tuple[tuple[bytes, bytes], ...],
) -> str:
    records: list[bytes] = []
    for raw_path, content in sorted(entries):
        blob_oid = run_git_input(
            repository,
            "hash-object",
            "-w",
            "--stdin",
            input_bytes=content,
        ).stdout.strip()
        records.append(b"100644 blob " + blob_oid + b"\t" + raw_path + b"\x00")
    return run_git_input(
        repository,
        "mktree",
        "-z",
        input_bytes=b"".join(records),
    ).stdout.decode("ascii").strip()


def commit_tree(
    repository: Path,
    tree_oid: str,
    *,
    parent_oid: str | None,
    message: bytes,
) -> str:
    args = ["commit-tree", tree_oid]
    if parent_oid is not None:
        args.extend(("-p", parent_oid))
    return run_git_input(
        repository,
        *args,
        input_bytes=message,
    ).stdout.decode("ascii").strip()


def make_repository(
    tmp_path: Path,
    *,
    directory: str,
    base_entries: tuple[tuple[bytes, bytes], ...],
    candidate_entries: tuple[tuple[bytes, bytes], ...],
) -> GitRepositoryFixture:
    path = tmp_path / directory
    path.mkdir()
    run_git(path, "init", "-q")
    run_git(path, "config", "user.name", "GWO Test")
    run_git(path, "config", "user.email", "gwo@example.invalid")
    base_tree_oid = write_tree(path, base_entries)
    base_commit_oid = commit_tree(
        path,
        base_tree_oid,
        parent_oid=None,
        message=b"base\n",
    )
    candidate_tree_oid = write_tree(path, candidate_entries)
    candidate_commit_oid = commit_tree(
        path,
        candidate_tree_oid,
        parent_oid=base_commit_oid,
        message=b"candidate\n",
    )
    run_git(path, "update-ref", "refs/heads/candidate", candidate_commit_oid)
    return GitRepositoryFixture(
        path=path,
        name="owner/repository",
        base_commit_oid=base_commit_oid,
        base_tree_oid=base_tree_oid,
        candidate_commit_oid=candidate_commit_oid,
        candidate_tree_oid=candidate_tree_oid,
    )


def make_git_repository(tmp_path: Path) -> GitRepositoryFixture:
    return make_repository(
        tmp_path,
        directory="ordinary",
        base_entries=((b"main.py", b"print('base')\n"),),
        candidate_entries=((b"main.py", b"print('candidate')\n"),),
    )


def make_rename_repository(tmp_path: Path) -> GitRepositoryFixture:
    return make_repository(
        tmp_path,
        directory="rename",
        base_entries=((b"old.py", b"same\n"),),
        candidate_entries=((b"new.py", b"same\n"),),
    )


def make_non_utf8_path_repository(tmp_path: Path) -> GitRepositoryFixture:
    return make_repository(
        tmp_path,
        directory="non-utf8",
        base_entries=((b"old-\xff.txt", b"base\n"),),
        candidate_entries=((b"old-\xff.txt", b"candidate\n"),),
    )


def test_git_candidate_reader_reads_exact_commit_and_tree_from_reference(tmp_path):
    repository = make_git_repository(tmp_path)
    readback = GitCandidateReader(
        repository_path=repository.path,
        base_reader=repository,
    ).read_candidate(repository.name, "refs/heads/candidate")

    assert readback.candidate.reported_reference == "refs/heads/candidate"
    assert readback.candidate.base_commit_oid == repository.base_commit_oid
    assert readback.candidate.base_tree_oid == repository.base_tree_oid
    assert readback.candidate.candidate_commit_oid == repository.candidate_commit_oid
    assert readback.candidate.candidate_tree_oid == repository.candidate_tree_oid
    assert type(readback.diff_record) is CandidateDiffRecordV1
    assert readback.diff_record.schema_version == "CandidateDiffRecordV1"
    assert readback.diff_record.repository_object_format == "sha1"
    assert readback.diff_record.changed_path_tokens == (
        encode_raw_path(b"main.py"),
    )
    assert readback.diff_record.entries[0].change_kind == "modify"


def test_git_candidate_reader_uses_only_the_frozen_base_port_identity(tmp_path):
    repository = make_git_repository(tmp_path)
    frozen_tree_oid = write_tree(
        repository.path,
        ((b"main.py", b"frozen-base\n"),),
    )
    frozen_commit_oid = commit_tree(
        repository.path,
        frozen_tree_oid,
        parent_oid=None,
        message=b"frozen base\n",
    )

    class FrozenBase(CandidateBasePort):
        def read_base(self, repository_name: str) -> tuple[str, str]:
            assert repository_name == repository.name
            return frozen_commit_oid, frozen_tree_oid

    readback = GitCandidateReader(
        repository_path=repository.path,
        base_reader=FrozenBase(),
    ).read_candidate(repository.name, "refs/heads/candidate")

    assert readback.candidate.base_commit_oid == frozen_commit_oid
    assert readback.candidate.base_tree_oid == frozen_tree_oid
    assert readback.diff_record.entries[0].old_oid != readback.diff_record.entries[0].new_oid


def test_git_candidate_reader_represents_rename_as_delete_and_add(tmp_path):
    repository = make_rename_repository(tmp_path)
    readback = GitCandidateReader(
        repository_path=repository.path,
        base_reader=repository,
    ).read_candidate(repository.name, "refs/heads/candidate")

    assert [entry.change_kind for entry in readback.diff_record.entries] == [
        "delete",
        "add",
    ]
    assert readback.diff_record.entries[0].old_path == encode_raw_path(b"old.py")
    assert readback.diff_record.entries[0].new_path is None
    assert readback.diff_record.entries[1].old_path is None
    assert readback.diff_record.entries[1].new_path == encode_raw_path(b"new.py")


def test_git_candidate_reader_preserves_raw_non_utf8_path_bytes(tmp_path):
    repository = make_non_utf8_path_repository(tmp_path)
    readback = GitCandidateReader(
        repository_path=repository.path,
        base_reader=repository,
    ).read_candidate(repository.name, "refs/heads/candidate")

    assert encode_raw_path(b"old-\xff.txt") in readback.diff_record.changed_path_tokens
    assert readback.diff_record.entries[0].old_path == encode_raw_path(b"old-\xff.txt")


@pytest.mark.parametrize("reported_reference", ["refs/heads/missing", "\x00"])
def test_git_candidate_reader_rejects_malformed_or_missing_reference(
    tmp_path,
    reported_reference,
):
    repository = make_git_repository(tmp_path)

    with pytest.raises(CandidateGateError) as raised:
        GitCandidateReader(
            repository_path=repository.path,
            base_reader=repository,
        ).read_candidate(repository.name, reported_reference)

    assert raised.value.code == "CANDIDATE_GATE_READBACK_INVALID"


def test_git_candidate_reader_rejects_reference_that_resolves_only_to_a_tree(tmp_path):
    repository = make_git_repository(tmp_path)
    run_git(
        repository.path,
        "update-ref",
        "refs/tags/tree-only",
        repository.base_tree_oid,
    )

    with pytest.raises(CandidateGateError) as raised:
        GitCandidateReader(
            repository_path=repository.path,
            base_reader=repository,
        ).read_candidate(repository.name, "refs/tags/tree-only")

    assert raised.value.code == "CANDIDATE_GATE_READBACK_INVALID"


def test_git_candidate_reader_rejects_a_reference_that_moves_during_readback(tmp_path):
    repository = make_git_repository(tmp_path)

    class MovingReader(GitCandidateReader):
        moved = False

        def _git_text(self, *args: str) -> str:
            value = super()._git_text(*args)
            if not self.moved and "refs/heads/candidate^{commit}" in args:
                run_git(
                    repository.path,
                    "update-ref",
                    "refs/heads/candidate",
                    repository.base_commit_oid,
                )
                self.moved = True
            return value

    with pytest.raises(CandidateGateError) as raised:
        MovingReader(
            repository_path=repository.path,
            base_reader=repository,
        ).read_candidate(repository.name, "refs/heads/candidate")

    assert raised.value.code == "CANDIDATE_GATE_READBACK_INVALID"


class RawTreeReader(GitCandidateReader):
    def __init__(self, *, raw_tree: bytes, **kwargs):
        super().__init__(**kwargs)
        self._raw_tree = raw_tree

    def _git_bytes(self, *args: str) -> bytes:
        if args and args[0] == "ls-tree":
            return self._raw_tree
        return super()._git_bytes(*args)


@pytest.mark.parametrize(
    "raw_tree",
    [
        b"not-a-tree-record\x00",
        b"100644 blob not-an-oid\tmain.py\x00",
    ],
)
def test_git_candidate_reader_fails_closed_on_malformed_raw_tree_records(
    tmp_path,
    raw_tree,
):
    repository = make_git_repository(tmp_path)

    with pytest.raises(CandidateGateError) as raised:
        RawTreeReader(
            raw_tree=raw_tree,
            repository_path=repository.path,
            base_reader=repository,
        ).read_candidate(repository.name, "refs/heads/candidate")

    assert raised.value.code == "CANDIDATE_GATE_DIFF_INVALID"


def test_git_candidate_reader_fails_closed_on_duplicate_raw_tree_paths(tmp_path):
    repository = make_git_repository(tmp_path)
    blob_oid = run_git_input(
        repository.path,
        "hash-object",
        "-w",
        "--stdin",
        input_bytes=b"duplicate\n",
    ).stdout.strip()
    raw_tree = (
        b"100644 blob "
        + blob_oid
        + b"\tmain.py\x00"
        + b"100644 blob "
        + blob_oid
        + b"\tmain.py\x00"
    )

    with pytest.raises(CandidateGateError) as raised:
        RawTreeReader(
            raw_tree=raw_tree,
            repository_path=repository.path,
            base_reader=repository,
        ).read_candidate(repository.name, "refs/heads/candidate")

    assert raised.value.code == "CANDIDATE_GATE_DIFF_INVALID"
