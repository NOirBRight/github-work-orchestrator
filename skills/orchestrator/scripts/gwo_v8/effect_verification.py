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
from .evidence import TypedEvidence

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
    return resolved if resolved == sha and _SHA40.fullmatch(resolved) else None


@dataclass(frozen=True)
class EffectContractDecision:
    """The one narrow typed Effect Contract verdict a Kernel caller receives."""

    status: Literal["accepted", "rejected"]
    verification: TypedEvidence
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
        if "\0" in decoded:
            raise EffectVerificationError(
                "GIT_CHANGED_PATHS_INVALID",
                "git changed-path output contained a NUL path escape",
            )
        if "\\" in decoded:
            raise EffectVerificationError(
                "GIT_CHANGED_PATHS_INVALID",
                "git changed path contains a literal backslash",
            )
        if decoded.startswith("/") or any(
            part in {".", ".."} for part in decoded.split("/")
        ):
            raise EffectVerificationError(
                "GIT_CHANGED_PATHS_INVALID",
                f"git changed path is not repository-relative: {decoded!r}",
            )
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


def _effect_source_ref(identity: dict[str, str]) -> str:
    return (
        "store://effect-contract-verification/"
        f"{identity['plan_digest']}/{identity['node_key']}/"
        f"{identity['attempt_id']}/{identity['candidate_sha']}"
    )


def effect_contract_evidence_binds(
    evidence: TypedEvidence,
    *,
    identity: dict[str, str],
    contract_digest: str,
    scopes: tuple[str, ...],
    observer_id: str,
) -> bool:
    """Validate the common envelope and its exact producer/source binding."""

    payload = evidence.payload
    if not isinstance(payload, dict):
        return False
    projection = {
        "base_sha": payload.get("base_sha"),
        "base_tree_sha": payload.get("base_tree_sha"),
        "candidate_sha": payload.get("candidate_sha"),
        "candidate_tree_sha": payload.get("candidate_tree_sha"),
        "changed_paths": payload.get("changed_paths"),
    }
    return not (
        not evidence.has_valid_digest()
        or evidence.kind != "decision"
        or evidence.subject != identity["candidate_sha"]
        or evidence.observer_type != "kernel"
        or evidence.observer_id != observer_id
        or evidence.source_ref != _effect_source_ref(identity)
        or payload.get("decision_type") != "effect_contract_verification"
        or any(payload.get(name) != value for name, value in identity.items())
        or payload.get("contract_digest") != contract_digest
        or payload.get("write_scopes") != list(scopes)
        or payload.get("status") not in {"accepted", "rejected"}
        or not isinstance(payload.get("findings"), list)
        or any(not isinstance(item, str) for item in payload["findings"])
        or not isinstance(payload.get("changed_paths"), list)
        or payload.get("diff_projection_digest") != digest_value(projection)
    )


class EffectContractVerifier:
    """Verify one Candidate against the Plan Node Effect Contract."""

    def __init__(
        self,
        *,
        observer_id: str,
        assert_writer: Callable[[dict[str, Any]], None],
        persist_state: Callable[[dict[str, Any]], None],
    ):
        self._observer_id = observer_id
        self._assert_writer = assert_writer
        self._persist_state = persist_state

    @staticmethod
    def initial_fields() -> dict[str, Any]:
        """The verification-internal persisted fields owned by this module."""

        return {"effect_verification": None}

    def _saved_verification(
        self,
        state: dict[str, Any],
        *,
        identity: dict[str, str],
        contract_digest: str,
        scopes: tuple[str, ...],
    ) -> TypedEvidence | None:
        saved = state.get("effect_verification")
        if not isinstance(saved, dict):
            return None
        try:
            evidence = TypedEvidence(**saved)
        except TypeError:
            return None
        if not effect_contract_evidence_binds(
            evidence,
            identity=identity,
            contract_digest=contract_digest,
            scopes=scopes,
            observer_id=self._observer_id,
        ):
            return None
        return evidence

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
        contract_digest = str(
            work_node.get("contract_digest") or state.get("contract_digest") or ""
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

        saved = self._saved_verification(
            state,
            identity=identity,
            contract_digest=contract_digest,
            scopes=scopes,
        )
        if saved is not None:
            return EffectContractDecision(
                status=saved.payload["status"],
                verification=saved,
                findings=tuple(saved.payload["findings"]),
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
            ancestry = subprocess.run(
                [
                    "git",
                    "-C",
                    str(workspace),
                    "merge-base",
                    "--is-ancestor",
                    identity["base_sha"],
                    identity["candidate_sha"],
                ],
                capture_output=True,
            )
            if ancestry.returncode != 0:
                findings.append(
                    "Candidate is not descended from the exact integration base"
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
            try:
                changed_paths = _parse_changed_paths(result.stdout)
            except EffectVerificationError as error:
                findings.append(error.detail)
            else:
                for entry in changed_paths:
                    label = _STATUS_LABELS[entry["status"]]
                    destination = entry["path"]
                    if not _covered(destination, scopes):
                        findings.append(
                            f"changed path '{destination}' ({label}) is outside "
                            "the authorized Write Scope"
                        )
                    source = entry.get("source_path")
                    if source is None:
                        continue
                    source_path = source
                    if not _covered(source_path, scopes):
                        findings.append(
                            f"{label} source path '{source_path}' is outside "
                            "the authorized Write Scope"
                        )

        status = "accepted" if not findings else "rejected"
        projection = {
            "base_sha": identity["base_sha"],
            "base_tree_sha": base_tree_sha,
            "candidate_sha": identity["candidate_sha"],
            "candidate_tree_sha": candidate_tree_sha,
            "changed_paths": [dict(entry) for entry in changed_paths],
        }
        evidence = TypedEvidence._capture(
            kind="decision",
            subject=identity["candidate_sha"],
            observer_type="kernel",
            observer_id=self._observer_id,
            observed_at=_now(),
            source_ref=_effect_source_ref(identity),
            payload={
                "decision_type": "effect_contract_verification",
                **identity,
                "contract_digest": contract_digest,
                "base_tree_sha": base_tree_sha,
                "candidate_tree_sha": candidate_tree_sha,
                "write_scopes": list(scopes),
                "changed_paths": [dict(entry) for entry in changed_paths],
                "diff_projection_digest": digest_value(projection),
                "status": status,
                "findings": list(findings),
            },
        )
        state["effect_verification"] = asdict(evidence)
        self._assert_writer(state)
        self._persist_state(state)
        return EffectContractDecision(
            status=status,
            verification=evidence,
            findings=tuple(findings),
        )
