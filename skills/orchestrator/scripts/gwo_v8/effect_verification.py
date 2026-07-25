"""Deep Effect Contract verification module behind the Kernel's typed decision.

ADR-0022 requires independently observed Evidence before acceptance; the Plan
Node Effect Contract declares the Write Scope a Candidate may touch. This
module concentrates the whole verification of that contract against reality:
it resolves the authoritative integration base and the exact Candidate as Git
identities, computes the changed-path projection with rename and copy
detection, checks every changed path against the authorized Write Scope, and
binds the verdict to Plan Revision, Plan Node, Attempt, base, Candidate, and
the exact diff projection. Worker self-report and the current workspace status
are never consulted; a changed Candidate SHA recomputes the verdict. Kernel
callers receive one typed accepted/rejected decision and perform no Git
parsing of their own. Any doubt fails closed before Review materialization.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
import re
import subprocess
from typing import Any, Callable, Literal

from ._canonical import digest_value
from ._effects import EffectContractError, normalized_relative_path

_SHA40 = re.compile(r"^[0-9a-f]{40}$")

_SINGLE_PATH_STATUSES = {"A", "M", "D", "T"}
_PAIR_STATUSES = {"R", "C"}
_STATUS_LABELS = {
    "A": "added",
    "M": "modified",
    "D": "deleted",
    "T": "type-changed",
    "R": "renamed",
    "C": "copied",
}


class EffectVerificationError(RuntimeError):
    """Invariant violation inside Effect Contract verification.

    The Kernel boundary translates this one module-local typed error into its
    own error type, so callers observe no new failure surface.
    """

    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code = code
        self.detail = detail


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _git(repository: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repository), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if result.returncode != 0:
        raise EffectVerificationError(
            "GIT_OPERATION_FAILED",
            result.stderr.strip() or result.stdout.strip() or "git failed",
        )
    return result.stdout.strip()


def _git_identity(repository: Path, sha: str) -> str | None:
    """Resolve one claimed SHA to a commit, or None when it is not one."""

    result = subprocess.run(
        ["git", "-C", str(repository), "rev-parse", "--verify", f"{sha}^{{commit}}"],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if result.returncode != 0:
        return None
    resolved = result.stdout.strip()
    return resolved if _SHA40.fullmatch(resolved) else None


@dataclass(frozen=True)
class EffectContractVerification:
    """Durable verdict bound to Plan, Node, Attempt, base, Candidate, and diff."""

    plan_digest: str
    node_key: str
    attempt_id: str
    base_sha: str
    candidate_sha: str
    base_tree_sha: str
    candidate_tree_sha: str
    write_scopes: tuple[str, ...]
    changed_paths: tuple[dict[str, Any], ...]
    diff_projection_digest: str
    status: str
    findings: tuple[str, ...]
    observed_at: str
    content_digest: str

    @classmethod
    def _capture(
        cls,
        *,
        plan_digest: str,
        node_key: str,
        attempt_id: str,
        base_sha: str,
        candidate_sha: str,
        base_tree_sha: str,
        candidate_tree_sha: str,
        write_scopes: tuple[str, ...],
        changed_paths: tuple[dict[str, Any], ...],
        status: str,
        findings: tuple[str, ...],
    ) -> EffectContractVerification:
        projection = {
            "base_sha": base_sha,
            "base_tree_sha": base_tree_sha,
            "candidate_sha": candidate_sha,
            "candidate_tree_sha": candidate_tree_sha,
            "changed_paths": [dict(entry) for entry in changed_paths],
        }
        body = {
            "plan_digest": plan_digest,
            "node_key": node_key,
            "attempt_id": attempt_id,
            "base_sha": base_sha,
            "candidate_sha": candidate_sha,
            "base_tree_sha": base_tree_sha,
            "candidate_tree_sha": candidate_tree_sha,
            "write_scopes": list(write_scopes),
            "changed_paths": [dict(entry) for entry in changed_paths],
            "diff_projection_digest": digest_value(projection),
            "status": status,
            "findings": list(findings),
            "observed_at": _now(),
        }
        return cls(
            **{
                **body,
                "write_scopes": tuple(write_scopes),
                "changed_paths": tuple(dict(entry) for entry in changed_paths),
                "findings": tuple(findings),
            },
            content_digest=digest_value(body),
        )

    def has_valid_digest(self) -> bool:
        body = {
            "plan_digest": self.plan_digest,
            "node_key": self.node_key,
            "attempt_id": self.attempt_id,
            "base_sha": self.base_sha,
            "candidate_sha": self.candidate_sha,
            "base_tree_sha": self.base_tree_sha,
            "candidate_tree_sha": self.candidate_tree_sha,
            "write_scopes": list(self.write_scopes),
            "changed_paths": [dict(entry) for entry in self.changed_paths],
            "diff_projection_digest": self.diff_projection_digest,
            "status": self.status,
            "findings": list(self.findings),
            "observed_at": self.observed_at,
        }
        return self.content_digest == digest_value(body)

    def binds(
        self,
        *,
        plan_digest: str,
        node_key: str,
        attempt_id: str,
        base_sha: str,
        candidate_sha: str,
    ) -> bool:
        return (
            self.plan_digest == plan_digest
            and self.node_key == node_key
            and self.attempt_id == attempt_id
            and self.base_sha == base_sha
            and self.candidate_sha == candidate_sha
        )


@dataclass(frozen=True)
class EffectContractDecision:
    """The one narrow typed Effect Contract verdict a Kernel caller receives."""

    status: Literal["accepted", "rejected"]
    verification: EffectContractVerification
    findings: tuple[str, ...] = ()


def _parse_changed_paths(output: bytes) -> tuple[dict[str, Any], ...]:
    """Parse the NUL-terminated name-status projection, failing closed."""

    if output and not output.endswith(b"\0"):
        raise EffectVerificationError(
            "GIT_CHANGED_PATHS_INVALID",
            "git changed-path output was not NUL-terminated",
        )
    tokens = output.split(b"\0")
    tokens.pop()
    records: list[dict[str, Any]] = []
    cursor = 0

    def take() -> bytes:
        nonlocal cursor
        if cursor >= len(tokens):
            raise EffectVerificationError(
                "GIT_CHANGED_PATHS_INVALID",
                "git changed-path output ended mid-record",
            )
        token = tokens[cursor]
        cursor += 1
        return token

    def path() -> str:
        token = take()
        if not token:
            raise EffectVerificationError(
                "GIT_CHANGED_PATHS_INVALID",
                "git changed-path output contained an empty path",
            )
        try:
            decoded = token.decode("utf-8")
        except UnicodeDecodeError as error:
            raise EffectVerificationError(
                "GIT_CHANGED_PATHS_INVALID",
                "git changed-path output was not valid UTF-8",
            ) from error
        return decoded

    while cursor < len(tokens):
        status_token = take()
        try:
            status_text = status_token.decode("ascii")
        except UnicodeDecodeError as error:
            raise EffectVerificationError(
                "GIT_CHANGED_PATHS_INVALID",
                "git changed-path status was not ASCII",
            ) from error
        letter = status_text[:1]
        if letter in _PAIR_STATUSES:
            score_text = status_text[1:]
            if not score_text.isdigit():
                raise EffectVerificationError(
                    "GIT_CHANGED_PATHS_INVALID",
                    f"git changed-path status is malformed: {status_text!r}",
                )
            records.append(
                {
                    "status": letter,
                    "score": int(score_text),
                    "source_path": path(),
                    "path": path(),
                }
            )
            continue
        if letter in _SINGLE_PATH_STATUSES and len(status_text) == 1:
            records.append({"status": letter, "path": path()})
            continue
        raise EffectVerificationError(
            "GIT_CHANGED_PATHS_UNRECOGNIZED",
            f"git changed-path status is not verifiable: {status_text!r}",
        )
    return tuple(records)


def _covered(path: str, scopes: tuple[str, ...]) -> bool:
    return any(path == scope or path.startswith(f"{scope}/") for scope in scopes)


class EffectContractVerifier:
    """Verify one Candidate against the Plan Node Effect Contract."""

    def __init__(
        self,
        *,
        assert_writer: Callable[[dict[str, Any]], None],
        persist_state: Callable[[dict[str, Any]], None],
    ):
        self._assert_writer = assert_writer
        self._persist_state = persist_state

    @staticmethod
    def initial_fields() -> dict[str, Any]:
        """The verification-internal persisted fields owned by this module."""

        return {"effect_verification": None}

    @staticmethod
    def _saved_verification(state: dict[str, Any]) -> EffectContractVerification | None:
        saved = state.get("effect_verification")
        if not isinstance(saved, dict):
            return None
        try:
            record = EffectContractVerification(
                **{
                    **saved,
                    "write_scopes": tuple(saved.get("write_scopes") or ()),
                    "changed_paths": tuple(
                        dict(entry)
                        for entry in saved.get("changed_paths") or ()
                    ),
                    "findings": tuple(saved.get("findings") or ()),
                }
            )
        except TypeError:
            return None
        return record if record.has_valid_digest() else None

    def verify_candidate(
        self,
        state: dict[str, Any],
        work_node: dict[str, Any],
        binding,
        observation,
    ) -> EffectContractDecision:
        result_claim = observation.result_claim
        if result_claim is None:
            raise EffectVerificationError(
                "EFFECT_CANDIDATE_MISSING",
                "Effect Contract verification requires one Result Claim",
            )
        candidate_sha = result_claim.candidate_sha
        base_sha = state.get("base_sha")
        identity = {
            "plan_digest": str(state["plan_digest"]),
            "node_key": str(work_node.get("node_key") or state["node_key"]),
            "attempt_id": str(state["attempt_id"]),
            "base_sha": str(base_sha or ""),
            "candidate_sha": str(candidate_sha or ""),
        }
        saved = self._saved_verification(state)
        if saved is not None and saved.binds(**identity):
            return EffectContractDecision(
                status="accepted" if saved.status == "accepted" else "rejected",
                verification=saved,
                findings=tuple(saved.findings),
            )

        findings: list[str] = []
        changed_paths: tuple[dict[str, Any], ...] = ()
        base_tree_sha = ""
        candidate_tree_sha = ""
        scopes: tuple[str, ...] = ()
        workspace = Path(binding.workspace).resolve()

        raw_scopes = (work_node.get("effect_contract") or {}).get("write_scopes")
        if not isinstance(raw_scopes, list):
            findings.append("Effect Contract Write Scope is not a list")
        else:
            try:
                scopes = tuple(
                    normalized_relative_path(scope) for scope in raw_scopes
                )
            except EffectContractError as error:
                findings.append(
                    f"Effect Contract Write Scope is invalid: {error}"
                )

        if _SHA40.fullmatch(identity["base_sha"]) is None:
            findings.append("integration base SHA is not a valid Git identity")
        if _SHA40.fullmatch(identity["candidate_sha"]) is None:
            findings.append("Candidate SHA is not a valid Git identity")

        if not findings:
            if _git_identity(workspace, identity["base_sha"]) is None:
                findings.append(
                    "integration base is not resolvable as a Git commit"
                )
            if _git_identity(workspace, identity["candidate_sha"]) is None:
                findings.append(
                    "Candidate is not resolvable as a Git commit"
                )

        if not findings:
            base_tree_sha = _git(
                workspace, "rev-parse", f"{identity['base_sha']}^{{tree}}"
            )
            candidate_tree_sha = _git(
                workspace, "rev-parse", f"{identity['candidate_sha']}^{{tree}}"
            )
            result = subprocess.run(
                [
                    "git",
                    "-C",
                    str(workspace),
                    "-c",
                    "core.quotepath=false",
                    "diff",
                    "--name-status",
                    "-z",
                    "-M",
                    "-C",
                    "--find-copies-harder",
                    f"{identity['base_sha']}...{identity['candidate_sha']}",
                ],
                capture_output=True,
            )
            if result.returncode != 0:
                raise EffectVerificationError(
                    "GIT_OPERATION_FAILED",
                    result.stderr.decode("utf-8", errors="replace").strip()
                    or "git changed-path diff failed",
                )
            changed_paths = _parse_changed_paths(result.stdout)
            for entry in changed_paths:
                label = _STATUS_LABELS[entry["status"]]
                try:
                    destination = normalized_relative_path(entry["path"])
                except EffectContractError:
                    findings.append(
                        f"changed path ({label}) escapes the repository: "
                        f"{entry['path']!r}"
                    )
                    continue
                if not _covered(destination, scopes):
                    findings.append(
                        f"changed path '{destination}' ({label}) is outside "
                        "the authorized Write Scope"
                    )
                source = entry.get("source_path")
                if source is None:
                    continue
                try:
                    source_path = normalized_relative_path(source)
                except EffectContractError:
                    findings.append(
                        f"{label} source path escapes the repository: {source!r}"
                    )
                    continue
                if not _covered(source_path, scopes):
                    findings.append(
                        f"{label} source path '{source_path}' is outside "
                        "the authorized Write Scope"
                    )

        status = "accepted" if not findings else "rejected"
        record = EffectContractVerification._capture(
            plan_digest=identity["plan_digest"],
            node_key=identity["node_key"],
            attempt_id=identity["attempt_id"],
            base_sha=identity["base_sha"],
            candidate_sha=identity["candidate_sha"],
            base_tree_sha=base_tree_sha,
            candidate_tree_sha=candidate_tree_sha,
            write_scopes=scopes,
            changed_paths=changed_paths,
            status=status,
            findings=tuple(findings),
        )
        state["effect_verification"] = asdict(record)
        self._assert_writer(state)
        self._persist_state(state)
        return EffectContractDecision(
            status=status,
            verification=record,
            findings=tuple(findings),
        )
