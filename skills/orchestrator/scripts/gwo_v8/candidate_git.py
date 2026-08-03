"""Authoritative raw-Git Candidate readback for CandidateGate."""

from __future__ import annotations

from pathlib import Path
import re
import subprocess
from typing import NoReturn, Protocol

from .candidate_gate import (
    CandidateDiffRecordV1,
    CandidateGateError,
    CandidateIdentity,
    CandidateReadback,
    CandidateReadbackPort,
)


_OID_RE = re.compile(r"^[0-9a-f]+$")
_TREE_MODES = {b"100644", b"100755", b"120000", b"160000"}


class CandidateBasePort(Protocol):
    def read_base(self, repository: str) -> tuple[str, str]:
        """Return the frozen base commit OID and base tree OID."""

        ...


def _fail(code: str, detail: str) -> NoReturn:
    raise CandidateGateError(code, detail)


class GitCandidateReader(CandidateReadbackPort):
    """Read one immutable Candidate and its complete raw-tree diff."""

    def __init__(
        self,
        *,
        repository_path: Path,
        base_reader: CandidateBasePort,
    ) -> None:
        self._repository_path = repository_path
        self._base_reader = base_reader

    def _git_bytes(self, *args: str) -> bytes:
        return subprocess.run(
            ["git", "-C", str(self._repository_path), *args],
            check=True,
            capture_output=True,
        ).stdout

    def _git_text(self, *args: str) -> str:
        try:
            output = self._git_bytes(*args)
        except CandidateGateError:
            raise
        except Exception as error:
            raise CandidateGateError(
                "CANDIDATE_GATE_READBACK_INVALID",
                "Git authoritative readback command failed",
            ) from error
        if type(output) is not bytes:
            _fail(
                "CANDIDATE_GATE_READBACK_INVALID",
                "Git authoritative readback returned non-byte output",
            )
        try:
            value = output.decode("ascii").strip()
        except UnicodeDecodeError as error:
            raise CandidateGateError(
                "CANDIDATE_GATE_READBACK_INVALID",
                "Git authoritative readback returned non-ASCII identity text",
            ) from error
        if not value or any(character.isspace() for character in value):
            _fail(
                "CANDIDATE_GATE_READBACK_INVALID",
                "Git authoritative readback returned malformed identity text",
            )
        return value

    @staticmethod
    def _validate_repository(repository: str) -> None:
        if type(repository) is not str or not repository or "\x00" in repository:
            _fail(
                "CANDIDATE_GATE_READBACK_INVALID",
                "Candidate repository identity is malformed",
            )

    @staticmethod
    def _validate_reference(reported_reference: str) -> None:
        if (
            type(reported_reference) is not str
            or not reported_reference
            or reported_reference.startswith("-")
            or any(character.isspace() for character in reported_reference)
            or "\x00" in reported_reference
        ):
            _fail(
                "CANDIDATE_GATE_READBACK_INVALID",
                "Candidate reference is malformed",
            )

    @staticmethod
    def _validate_oid(
        value: object,
        *,
        object_format: str,
        field_name: str,
        code: str,
    ) -> str:
        expected_length = {"sha1": 40, "sha256": 64}.get(object_format)
        if (
            expected_length is None
            or type(value) is not str
            or len(value) != expected_length
            or _OID_RE.fullmatch(value) is None
        ):
            _fail(code, f"{field_name} is not an exact {object_format} object ID")
        return value

    def _read_tree(
        self,
        tree_oid: str,
        *,
        object_format: str,
    ) -> dict[bytes, tuple[str, str, str]]:
        try:
            output = self._git_bytes("ls-tree", "-rz", tree_oid)
        except CandidateGateError:
            raise
        except Exception as error:
            raise CandidateGateError(
                "CANDIDATE_GATE_DIFF_INVALID",
                "Git tree readback failed",
            ) from error
        if type(output) is not bytes:
            _fail(
                "CANDIDATE_GATE_DIFF_INVALID",
                "Git tree readback returned non-byte output",
            )
        if not output:
            return {}
        if not output.endswith(b"\x00"):
            _fail(
                "CANDIDATE_GATE_DIFF_INVALID",
                "Git tree readback is not NUL terminated",
            )

        result: dict[bytes, tuple[str, str, str]] = {}
        records = output.split(b"\x00")
        for index, record in enumerate(records):
            if not record:
                if index == len(records) - 1:
                    continue
                _fail(
                    "CANDIDATE_GATE_DIFF_INVALID",
                    "Git tree readback contains an empty record",
                )
            if record.count(b"\t") != 1:
                _fail(
                    "CANDIDATE_GATE_DIFF_INVALID",
                    "Git tree readback record has malformed path framing",
                )
            header, raw_path = record.split(b"\t", 1)
            if (
                not raw_path
                or raw_path.startswith(b"/")
                or raw_path.endswith(b"/")
                or b"//" in raw_path
                or any(part in {b".", b".."} for part in raw_path.split(b"/"))
            ):
                _fail(
                    "CANDIDATE_GATE_DIFF_INVALID",
                    "Git tree readback contains a malformed repository path",
                )
            fields = header.split(b" ")
            if len(fields) != 3:
                _fail(
                    "CANDIDATE_GATE_DIFF_INVALID",
                    "Git tree readback header is malformed",
                )
            mode, object_type, object_oid = fields
            if mode not in _TREE_MODES:
                _fail(
                    "CANDIDATE_GATE_DIFF_INVALID",
                    "Git tree readback mode is unsupported",
                )
            if mode == b"160000":
                if object_type != b"commit":
                    _fail(
                        "CANDIDATE_GATE_DIFF_INVALID",
                        "Gitlink tree entry does not have commit type",
                    )
                normalized_type = "gitlink"
            else:
                if object_type != b"blob":
                    _fail(
                        "CANDIDATE_GATE_DIFF_INVALID",
                        "File tree entry does not have blob type",
                    )
                normalized_type = "blob"
            try:
                decoded_oid = object_oid.decode("ascii")
            except UnicodeDecodeError as error:
                raise CandidateGateError(
                    "CANDIDATE_GATE_DIFF_INVALID",
                    "Git tree readback object ID is not ASCII",
                ) from error
            self._validate_oid(
                decoded_oid,
                object_format=object_format,
                field_name="tree object ID",
                code="CANDIDATE_GATE_DIFF_INVALID",
            )
            if raw_path in result:
                _fail(
                    "CANDIDATE_GATE_DIFF_INVALID",
                    "Git tree contains a duplicate raw path",
                )
            result[raw_path] = (
                mode.decode("ascii"),
                normalized_type,
                decoded_oid,
            )
        return result

    def _resolve(self, value: str, suffix: str, *, field_name: str) -> str:
        resolved = self._git_text(
            "rev-parse",
            "--verify",
            f"{value}^{{{suffix}}}",
        )
        return self._validate_oid(
            resolved,
            object_format=self._object_format,
            field_name=field_name,
            code="CANDIDATE_GATE_READBACK_INVALID",
        )

    def read_candidate(
        self,
        repository: str,
        reported_reference: str,
    ) -> CandidateReadback:
        self._validate_repository(repository)
        self._validate_reference(reported_reference)
        try:
            self._object_format = self._git_text(
                "rev-parse", "--show-object-format"
            )
            if self._object_format not in {"sha1", "sha256"}:
                _fail(
                    "CANDIDATE_GATE_READBACK_INVALID",
                    "Git repository object format is unsupported",
                )
            base_result = self._base_reader.read_base(repository)
            if (
                type(base_result) is not tuple
                or len(base_result) != 2
                or any(type(value) is not str for value in base_result)
            ):
                _fail(
                    "CANDIDATE_GATE_READBACK_INVALID",
                    "CandidateBasePort returned a malformed base identity",
                )
            base_commit_oid, base_tree_oid = base_result
            base_commit_oid = self._validate_oid(
                base_commit_oid,
                object_format=self._object_format,
                field_name="base commit OID",
                code="CANDIDATE_GATE_READBACK_INVALID",
            )
            base_tree_oid = self._validate_oid(
                base_tree_oid,
                object_format=self._object_format,
                field_name="base tree OID",
                code="CANDIDATE_GATE_READBACK_INVALID",
            )
            if self._resolve(
                base_commit_oid,
                "commit",
                field_name="base commit OID",
            ) != base_commit_oid:
                _fail(
                    "CANDIDATE_GATE_READBACK_INVALID",
                    "CandidateBasePort base commit is not a commit object",
                )
            if self._resolve(
                base_commit_oid,
                "tree",
                field_name="base tree OID",
            ) != base_tree_oid:
                _fail(
                    "CANDIDATE_GATE_READBACK_INVALID",
                    "CandidateBasePort base commit/tree identity is inconsistent",
                )

            candidate_commit_oid = self._resolve(
                reported_reference,
                "commit",
                field_name="candidate commit OID",
            )
            candidate_tree_oid = self._resolve(
                reported_reference,
                "tree",
                field_name="candidate tree OID",
            )
            if self._resolve(
                candidate_commit_oid,
                "tree",
                field_name="candidate tree OID",
            ) != candidate_tree_oid:
                _fail(
                    "CANDIDATE_GATE_READBACK_INVALID",
                    "Candidate commit/tree identity is inconsistent",
                )

            base_entries = self._read_tree(
                base_tree_oid,
                object_format=self._object_format,
            )
            candidate_entries = self._read_tree(
                candidate_tree_oid,
                object_format=self._object_format,
            )
            diff_record = CandidateDiffRecordV1.from_tree_entries(
                repository_object_format=self._object_format,
                base_commit_oid=base_commit_oid,
                base_tree_oid=base_tree_oid,
                candidate_commit_oid=candidate_commit_oid,
                candidate_tree_oid=candidate_tree_oid,
                base_entries=base_entries,
                candidate_entries=candidate_entries,
            )
            candidate = CandidateIdentity(
                reported_reference=reported_reference,
                base_commit_oid=base_commit_oid,
                base_tree_oid=base_tree_oid,
                candidate_commit_oid=candidate_commit_oid,
                candidate_tree_oid=candidate_tree_oid,
                changed_path_tokens=diff_record.changed_path_tokens,
            )

            confirmed_commit_oid = self._resolve(
                reported_reference,
                "commit",
                field_name="candidate commit OID",
            )
            confirmed_tree_oid = self._resolve(
                reported_reference,
                "tree",
                field_name="candidate tree OID",
            )
            if (
                confirmed_commit_oid != candidate_commit_oid
                or confirmed_tree_oid != candidate_tree_oid
            ):
                _fail(
                    "CANDIDATE_GATE_READBACK_INVALID",
                    "Candidate reference moved during authoritative readback",
                )
            return CandidateReadback(
                repository=repository,
                candidate=candidate,
                diff_record=diff_record,
            )
        except CandidateGateError:
            raise
        except Exception as error:
            raise CandidateGateError(
                "CANDIDATE_GATE_READBACK_INVALID",
                "authoritative Candidate reference readback failed",
            ) from error


__all__ = ["CandidateBasePort", "GitCandidateReader"]
