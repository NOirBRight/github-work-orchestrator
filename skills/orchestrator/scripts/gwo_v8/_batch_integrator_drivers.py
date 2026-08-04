"""Private Git and local-suite drivers for BatchIntegrator composition."""

from __future__ import annotations

import base64
from dataclasses import dataclass
import os
from pathlib import Path
import subprocess
import tempfile
from typing import TYPE_CHECKING, Callable, Literal, Protocol

from ._canonical import digest_value
from .batch_patch_identity import PatchIdentityEntry, patch_identity_v1, require_clean_base_advance
from .candidate_gate import AcceptedCandidateReceipt, InteractionClassification, InteractionKey

if TYPE_CHECKING:
    from .batch_integrator import (
        AncestorReadback,
        BatchTarget,
        LocalSuiteDefinition,
        TargetDeltaReadback,
    )
    from .batch_patch_identity import CleanBaseAdvanceProof


DETERMINISTIC_AUTHOR_NAME = "GWO V8 Batch Integrator"
DETERMINISTIC_AUTHOR_EMAIL = "gwo-v8-batch-integrator@example.invalid"
DETERMINISTIC_COMMIT_DATE = "1970-01-01T00:00:00Z"
_BATCH_REF_PREFIX = "refs/gwo-v8/integration-batches/"


def _batch_error(code: str, detail: str) -> Exception:
    from .batch_integrator import BatchIntegratorError

    return BatchIntegratorError(code, detail)


def _require_object_id(name: str, value: str) -> None:
    if (
        type(value) is not str
        or len(value) not in {40, 64}
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise _batch_error("BATCH_OBJECT_ID_INVALID", f"{name} is not a Git object ID")


def _require_digest(name: str, value: str) -> None:
    if (
        type(value) is not str
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise _batch_error("BATCH_DIGEST_INVALID", f"{name} is not a SHA-256 digest")


def _require_text(name: str, value: str) -> None:
    if type(value) is not str or not value or "\x00" in value:
        raise _batch_error("BATCH_DRIVER_IDENTITY_INVALID", f"{name} is invalid")


class GitBatchDriver(Protocol):
    def read_target(self, target: "BatchTarget") -> "BatchTarget": ...

    def read_ancestor(
        self, ancestor_sha: str, descendant_sha: str
    ) -> "AncestorReadback": ...

    def read_target_delta(
        self, base_sha: str, target: "BatchTarget"
    ) -> "TargetDeltaReadback": ...

    def read_ref(self, ref: str) -> str | None: ...

    def update_ref_cas(
        self, ref: str, expected_sha: str | None, new_sha: str
    ) -> str: ...

    def compose_batch(
        self,
        batch_id: str,
        target: "BatchTarget",
        members: tuple[AcceptedCandidateReceipt, ...],
    ) -> str: ...

    def clean_base_advance(
        self,
        batch_id: str,
        target: "BatchTarget",
        member: AcceptedCandidateReceipt,
    ) -> "CleanBaseAdvanceProof": ...


class LocalSuiteDriver(Protocol):
    def run(
        self, batch_sha: str, suite: "LocalSuiteDefinition"
    ) -> "LocalCheckReceipt": ...


@dataclass(frozen=True)
class LocalCheckReceipt:
    """Exact identity for one local suite observation."""

    batch_sha: str
    suite_id: str
    definition_digest: str
    outcome: Literal["passed", "code_failure", "infrastructure_failure"]
    observation_digest: str
    source_ref: str
    receipt_digest: str

    def __post_init__(self) -> None:
        _require_object_id("local batch_sha", self.batch_sha)
        _require_text("local suite_id", self.suite_id)
        _require_digest("local definition_digest", self.definition_digest)
        if self.outcome not in {
            "passed",
            "code_failure",
            "infrastructure_failure",
        }:
            raise _batch_error("BATCH_LOCAL_OUTCOME_INVALID", "local outcome is not closed")
        _require_digest("local observation_digest", self.observation_digest)
        _require_text("local source_ref", self.source_ref)
        _require_digest("local receipt_digest", self.receipt_digest)
        if self.receipt_digest != digest_value(
            {"kind": "local-check-receipt.v1", **self.body()}
        ):
            raise _batch_error(
                "BATCH_LOCAL_RECEIPT_MISMATCH",
                "local check receipt digest changed after readback",
            )

    def body(self) -> dict[str, str]:
        return {
            "batch_sha": self.batch_sha,
            "suite_id": self.suite_id,
            "definition_digest": self.definition_digest,
            "outcome": self.outcome,
            "observation_digest": self.observation_digest,
            "source_ref": self.source_ref,
        }

    @classmethod
    def create(
        cls,
        *,
        batch_sha: str,
        suite_id: str,
        definition_digest: str,
        outcome: Literal["passed", "code_failure", "infrastructure_failure"],
        source_ref: str,
        stdout_digest: str | None = None,
        stderr_digest: str | None = None,
    ) -> "LocalCheckReceipt":
        body = {
            "batch_sha": batch_sha,
            "suite_id": suite_id,
            "definition_digest": definition_digest,
            "outcome": outcome,
            "source_ref": source_ref,
        }
        observation_body = {
            **body,
            "stdout_digest": stdout_digest,
            "stderr_digest": stderr_digest,
        }
        observation_digest = digest_value(
            {"kind": "local-observation.v1", **observation_body}
        )
        receipt_body = {**body, "observation_digest": observation_digest}
        return cls(
            **receipt_body,
            receipt_digest=digest_value(
                {"kind": "local-check-receipt.v1", **receipt_body}
            ),
        )


def _run_git(
    repository: Path,
    *arguments: str,
    env: dict[str, str] | None = None,
    text: bool = True,
    check: bool = True,
) -> str | bytes:
    try:
        completed = subprocess.run(
            ("git", *arguments),
            cwd=repository,
            check=False,
            capture_output=True,
            text=text,
            env=env,
        )
    except OSError as error:
        raise _batch_error("BATCH_GIT_UNAVAILABLE", str(error)) from error
    if check and completed.returncode != 0:
        if text:
            detail = (completed.stderr or completed.stdout).strip()
        else:
            detail = (completed.stderr or completed.stdout).decode(
                "utf-8", errors="replace"
            ).strip()
        raise _batch_error(
            "BATCH_GIT_COMMAND_FAILED",
            detail or f"git {' '.join(arguments)} failed",
        )
    return completed.stdout


def _tree_entry_type(mode: str, object_type: str) -> str:
    return "gitlink" if mode == "160000" or object_type == "commit" else "blob"


def _path_text(raw_path: bytes) -> str:
    try:
        path = raw_path.decode("utf-8")
    except UnicodeDecodeError as error:
        raise _batch_error(
            "BATCH_PATCH_IDENTITY_INVALID", "Git path is not valid UTF-8"
        ) from error
    if (
        not path
        or "\x00" in path
        or path.startswith("/")
        or "\\" in path
        or any(part in {"", ".", ".."} for part in path.split("/"))
    ):
        raise _batch_error(
            "BATCH_PATCH_IDENTITY_INVALID",
            "Git path is not a safe repository-relative path",
        )
    return path


def _path_token(raw_path: bytes) -> str:
    return base64.urlsafe_b64encode(raw_path).decode("ascii").rstrip("=")


def _tree_entries(
    repository: Path, treeish: str
) -> dict[bytes, tuple[str, str, str]]:
    raw = _run_git(
        repository,
        "ls-tree",
        "-r",
        "-z",
        "--full-tree",
        treeish,
        text=False,
    )
    assert isinstance(raw, bytes)
    entries: dict[bytes, tuple[str, str, str]] = {}
    for record in raw.split(b"\x00"):
        if not record:
            continue
        try:
            metadata, path = record.split(b"\t", 1)
            mode_raw, object_type_raw, oid_raw = metadata.split(b" ", 2)
            mode = mode_raw.decode("ascii")
            object_type = object_type_raw.decode("ascii")
            oid = oid_raw.decode("ascii")
        except (UnicodeDecodeError, ValueError) as error:
            raise _batch_error(
                "BATCH_PATCH_IDENTITY_INVALID", "Git tree entry is malformed"
            ) from error
        _require_object_id("tree entry oid", oid)
        if path in entries:
            raise _batch_error(
                "BATCH_PATCH_IDENTITY_INVALID", "Git tree contains duplicate paths"
            )
        entries[path] = (mode, _tree_entry_type(mode, object_type), oid)
    return entries


def _patch_identity_for_trees(
    repository: Path,
    old_tree: str,
    new_tree: str,
) -> str:
    _require_object_id("old tree", old_tree)
    _require_object_id("new tree", new_tree)
    old_entries = _tree_entries(repository, old_tree)
    new_entries = _tree_entries(repository, new_tree)
    entries: list[PatchIdentityEntry] = []
    for raw_path in sorted(set(old_entries) | set(new_entries)):
        old = old_entries.get(raw_path)
        new = new_entries.get(raw_path)
        if old == new:
            continue
        old_mode, old_type, old_oid = old if old is not None else (None, None, None)
        new_mode, new_type, new_oid = new if new is not None else (None, None, None)
        if old is None:
            change_kind = "add"
        elif new is None:
            change_kind = "delete"
        elif old_type != new_type or (
            old_mode in {"120000", "160000"} or new_mode in {"120000", "160000"}
        ) and old_mode != new_mode:
            change_kind = "type-change"
        else:
            change_kind = "modify"
        path = _path_text(raw_path)
        entries.append(
            PatchIdentityEntry(
                old_path=None if old is None else path,
                new_path=None if new is None else path,
                change_kind=change_kind,
                old_mode=old_mode,
                new_mode=new_mode,
                old_object_type=old_type,
                new_object_type=new_type,
                old_oid=old_oid,
                new_oid=new_oid,
            )
        )
    object_format_raw = _run_git(repository, "rev-parse", "--show-object-format")
    assert isinstance(object_format_raw, str)
    object_format = object_format_raw.strip()
    if object_format not in {"sha1", "sha256"}:
        raise _batch_error(
            "BATCH_PATCH_IDENTITY_INVALID", "Git object format is unsupported"
        )
    try:
        return patch_identity_v1(object_format, tuple(entries))  # type: ignore[arg-type]
    except (TypeError, ValueError) as error:
        raise _batch_error(
            "BATCH_PATCH_IDENTITY_INVALID", str(error)
        ) from error


def patch_identity_for_trees(repository: Path, old_tree: str, new_tree: str) -> str:
    """Return PatchIdentityV1 for two repository trees."""

    return _patch_identity_for_trees(Path(repository), old_tree, new_tree)


class GitCliBatchDriver:
    def __init__(
        self, repository: Path, *, crash_hook: Callable[[str], None]
    ) -> None:
        self.repository = Path(repository)
        self.crash_hook = crash_hook
        self.compose_calls = 0
        self.clean_base_advance_calls: list[str] = []
        self.recomputed_patch_digest: str | None = None

    def _commit_tree(self, sha: str, *, name: str) -> str:
        _require_object_id(name, sha)
        try:
            commit = _run_git(
                self.repository, "rev-parse", "--verify", f"{sha}^{{commit}}"
            )
            tree = _run_git(
                self.repository, "rev-parse", "--verify", f"{sha}^{{tree}}"
            )
        except Exception as error:
            if getattr(error, "code", None) == "BATCH_GIT_COMMAND_FAILED":
                raise _batch_error(
                    "BATCH_CANDIDATE_IDENTITY_MISMATCH",
                    f"{name} is not an available commit",
                ) from error
            raise
        assert isinstance(commit, str) and isinstance(tree, str)
        if commit.strip() != sha:
            raise _batch_error(
                "BATCH_CANDIDATE_IDENTITY_MISMATCH",
                f"{name} resolved to a different commit",
            )
        _require_object_id(f"{name} tree", tree.strip())
        return tree.strip()

    def _verify_member(self, member: AcceptedCandidateReceipt) -> str:
        base_tree = self._commit_tree(member.base_sha, name="base_sha")
        candidate_tree = self._commit_tree(member.candidate_sha, name="candidate_sha")
        if base_tree != member.base_tree_oid or candidate_tree != member.candidate_tree_oid:
            raise _batch_error(
                "BATCH_CANDIDATE_IDENTITY_MISMATCH",
                f"{member.ticket_key} commit/tree identity changed",
            )
        return candidate_tree

    def read_target(self, target: "BatchTarget") -> "BatchTarget":
        from .batch_integrator import BatchIntegratorError, BatchTarget

        _require_text("target branch", target.target_branch)
        try:
            head = _run_git(
                self.repository,
                "rev-parse",
                "--verify",
                f"refs/heads/{target.target_branch}^{{commit}}",
            )
            tree = _run_git(
                self.repository, "rev-parse", "--verify", f"{head.strip()}^{{tree}}"
            )
        except Exception as error:
            if isinstance(error, BatchIntegratorError):
                raise _batch_error(
                    "BATCH_TARGET_READBACK_MISMATCH",
                    "target branch could not be read",
                ) from error
            raise
        assert isinstance(head, str) and isinstance(tree, str)
        actual_head = head.strip()
        actual_tree = tree.strip()
        if actual_head != target.target_head_sha or actual_tree != target.target_tree_oid:
            raise _batch_error(
                "BATCH_TARGET_READBACK_MISMATCH",
                "target branch head or tree changed before composition",
            )
        return BatchTarget(
            repository=target.repository,
            target_branch=target.target_branch,
            target_head_sha=actual_head,
            target_tree_oid=actual_tree,
            target_facts_digest=target.target_facts_digest,
        )

    def read_ancestor(
        self, ancestor_sha: str, descendant_sha: str
    ) -> "AncestorReadback":
        from .batch_integrator import AncestorReadback

        _require_object_id("ancestor_sha", ancestor_sha)
        _require_object_id("descendant_sha", descendant_sha)
        completed = subprocess.run(
            ("git", "merge-base", "--is-ancestor", ancestor_sha, descendant_sha),
            cwd=self.repository,
            check=False,
            capture_output=True,
            text=True,
        )
        if completed.returncode not in {0, 1}:
            raise _batch_error(
                "BATCH_GIT_COMMAND_FAILED",
                completed.stderr.strip() or "git merge-base failed",
            )
        body = {
            "ancestor_sha": ancestor_sha,
            "descendant_sha": descendant_sha,
            "is_ancestor": completed.returncode == 0,
        }
        return AncestorReadback(
            **body,
            readback_digest=digest_value({"kind": "ancestor-readback.v1", **body}),
        )

    def read_target_delta(
        self, base_sha: str, target: "BatchTarget"
    ) -> "TargetDeltaReadback":
        from .batch_integrator import TargetDeltaReadback

        base_tree = self._commit_tree(base_sha, name="target delta base_sha")
        target_tree = self._commit_tree(
            target.target_head_sha, name="target delta target_head_sha"
        )
        changed_paths = sorted(
            set(_tree_entries(self.repository, base_tree))
            ^ set(_tree_entries(self.repository, target_tree))
        )
        # A path present in both trees may still have changed mode/content.
        old_entries = _tree_entries(self.repository, base_tree)
        new_entries = _tree_entries(self.repository, target_tree)
        changed_paths = sorted(
            path
            for path in set(old_entries) | set(new_entries)
            if old_entries.get(path) != new_entries.get(path)
        )
        interaction_keys = tuple(
            InteractionKey(
                namespace="candidate-path",
                value=_path_token(path),
                classification=(
                    InteractionClassification.HIGH_COUPLING
                    if (
                        old_entries.get(path, ("", "", ""))[1] == "gitlink"
                        or new_entries.get(path, ("", "", ""))[1] == "gitlink"
                    )
                    else InteractionClassification.ORDINARY
                ),
            )
            for path in changed_paths
        )
        protected_interaction_keys = tuple(
            key for key in interaction_keys if key.requires_singleton
        )
        body = {
            "base_sha": base_sha,
            "target_head_sha": target.target_head_sha,
            "interaction_keys": [key.canonical() for key in interaction_keys],
            "protected_interaction_keys": [
                key.canonical() for key in protected_interaction_keys
            ],
        }
        return TargetDeltaReadback(
            base_sha=base_sha,
            target_head_sha=target.target_head_sha,
            interaction_keys=interaction_keys,
            protected_interaction_keys=protected_interaction_keys,
            facts_digest=digest_value(body),
            readback_digest=digest_value(
                {"kind": "target-delta-readback.v1", **body}
            ),
        )

    @staticmethod
    def _apply_member_policy(
        target_delta: "TargetDeltaReadback",
        member: AcceptedCandidateReceipt,
    ) -> "TargetDeltaReadback":
        from .batch_integrator import TargetDeltaReadback

        protected_surfaces = set(member.protected_surfaces)

        def value_aliases(value: str) -> set[str]:
            aliases = {value}
            try:
                raw_value = base64.urlsafe_b64decode(
                    value + "=" * (-len(value) % 4)
                )
            except ValueError:
                raw_value = None
            if raw_value is not None:
                try:
                    aliases.add(raw_value.decode("utf-8"))
                except UnicodeDecodeError:
                    pass
            aliases.add(
                base64.urlsafe_b64encode(value.encode("utf-8"))
                .decode("ascii")
                .rstrip("=")
            )
            return aliases

        member_interactions: dict[tuple[str, str], InteractionClassification] = {}
        for interaction in member.interaction_keys:
            if not interaction.requires_singleton:
                continue
            for alias in value_aliases(interaction.value):
                current = member_interactions.get((interaction.namespace, alias))
                if current is None or (
                    interaction.classification.value > current.value
                ):
                    member_interactions[(interaction.namespace, alias)] = (
                        interaction.classification
                    )

        def is_protected_surface(key: InteractionKey) -> bool:
            if key.value in protected_surfaces:
                return True
            try:
                raw_path = base64.urlsafe_b64decode(
                    key.value + "=" * (-len(key.value) % 4)
                ).decode("utf-8")
            except (UnicodeDecodeError, ValueError):
                return False
            return raw_path in protected_surfaces

        keys = tuple(
            InteractionKey(
                namespace=key.namespace,
                value=key.value,
                classification=(
                    InteractionClassification.PROTECTED
                    if is_protected_surface(key)
                    else next(
                        (
                            member_interactions[(key.namespace, alias)]
                            for alias in value_aliases(key.value)
                            if (key.namespace, alias) in member_interactions
                        ),
                        key.classification,
                    )
                ),
            )
            for key in target_delta.interaction_keys
        )
        protected = tuple(key for key in keys if key.requires_singleton)
        body = {
            "base_sha": target_delta.base_sha,
            "target_head_sha": target_delta.target_head_sha,
            "interaction_keys": [key.canonical() for key in keys],
            "protected_interaction_keys": [key.canonical() for key in protected],
        }
        return TargetDeltaReadback(
            base_sha=target_delta.base_sha,
            target_head_sha=target_delta.target_head_sha,
            interaction_keys=keys,
            protected_interaction_keys=protected,
            facts_digest=digest_value(body),
            readback_digest=digest_value(
                {"kind": "target-delta-readback.v1", **body}
            ),
        )

    def read_ref(self, ref: str) -> str | None:
        _require_text("batch ref", ref)
        completed = subprocess.run(
            ("git", "show-ref", "--hash", "--verify", ref),
            cwd=self.repository,
            check=False,
            capture_output=True,
            text=True,
        )
        if completed.returncode == 1 or (
            completed.returncode == 128
            and "not a valid ref" in completed.stderr.lower()
        ):
            return None
        if completed.returncode != 0:
            raise _batch_error(
                "BATCH_GIT_COMMAND_FAILED",
                completed.stderr.strip() or "git show-ref failed",
            )
        value = completed.stdout.strip()
        if not value:
            return None
        _require_object_id("batch ref SHA", value)
        return value

    def update_ref_cas(
        self, ref: str, expected_sha: str | None, new_sha: str
    ) -> str:
        _require_text("batch ref", ref)
        _require_object_id("new batch ref SHA", new_sha)
        if expected_sha is not None:
            _require_object_id("expected batch ref SHA", expected_sha)
        current = self.read_ref(ref)
        if current != expected_sha:
            raise _batch_error(
                "BATCH_REF_CAS_CONFLICT",
                f"unexpected current SHA for {ref}",
            )
        old_value = expected_sha or ""
        completed = subprocess.run(
            ("git", "update-ref", ref, new_sha, old_value),
            cwd=self.repository,
            check=False,
            capture_output=True,
            text=True,
        )
        if completed.returncode != 0:
            raise _batch_error(
                "BATCH_REF_CAS_CONFLICT",
                completed.stderr.strip() or f"unable to publish {ref}",
            )
        readback = self.read_ref(ref)
        if readback != new_sha:
            raise _batch_error(
                "BATCH_REF_IDENTITY_MISMATCH",
                f"published {ref} does not read back to the requested SHA",
            )
        return readback

    def _verify_batch_ref(
        self,
        batch_sha: str,
        target: "BatchTarget",
        members: tuple[AcceptedCandidateReceipt, ...],
    ) -> None:
        self._commit_tree(batch_sha, name="batch ref")
        if len(members) == 1 and (
            members[0].base_sha == target.target_head_sha
            and members[0].base_tree_oid == target.target_tree_oid
        ):
            if batch_sha != members[0].candidate_sha:
                raise _batch_error(
                    "BATCH_REF_IDENTITY_MISMATCH",
                    "Singleton ref is not the exact Candidate SHA",
                )
            return
        target_is_ancestor = subprocess.run(
            (
                "git",
                "merge-base",
                "--is-ancestor",
                target.target_head_sha,
                batch_sha,
            ),
            cwd=self.repository,
            check=False,
            capture_output=True,
            text=True,
        )
        if target_is_ancestor.returncode != 0:
            raise _batch_error(
                "BATCH_REF_IDENTITY_MISMATCH",
                "batch ref does not contain the exact target commit",
            )
        for member in members:
            completed = subprocess.run(
                (
                    "git",
                    "merge-base",
                    "--is-ancestor",
                    member.candidate_sha,
                    batch_sha,
                ),
                cwd=self.repository,
                check=False,
                capture_output=True,
                text=True,
            )
            if completed.returncode != 0:
                raise _batch_error(
                    "BATCH_REF_IDENTITY_MISMATCH",
                    f"batch ref does not contain {member.ticket_key}",
                )

    def _with_worktree(self, target_sha: str) -> tuple[tempfile.TemporaryDirectory[str], Path]:
        directory = tempfile.TemporaryDirectory(prefix="gwo-v8-batch-")
        worktree = Path(directory.name) / "worktree"
        try:
            _run_git(
                self.repository,
                "worktree",
                "add",
                "--detach",
                "--quiet",
                str(worktree),
                target_sha,
            )
        except Exception:
            directory.cleanup()
            raise
        return directory, worktree

    def _merge_target_and_members(
        self,
        batch_id: str,
        target: "BatchTarget",
        members: tuple[AcceptedCandidateReceipt, ...],
    ) -> str:
        ordered = tuple(sorted(members, key=lambda member: member.integration_node_key))
        directory, worktree = self._with_worktree(target.target_head_sha)
        try:
            message = f"GWO V8 Integration Batch {batch_id}"
            completed = subprocess.run(
                (
                    "git",
                    "merge",
                    "--no-ff",
                    "--no-commit",
                    "--no-edit",
                    "-m",
                    message,
                    *(member.candidate_sha for member in ordered),
                ),
                cwd=worktree,
                check=False,
                capture_output=True,
                text=True,
            )
            if completed.returncode != 0:
                subprocess.run(
                    ("git", "merge", "--abort"),
                    cwd=worktree,
                    check=False,
                    capture_output=True,
                    text=True,
                )
                detail = completed.stderr.strip() or "Candidate trees do not compose cleanly"
                raise _batch_error("BATCH_COMPOSITION_CONFLICT", detail)
            commit_env = os.environ.copy()
            commit_env.update(
                {
                    "GIT_AUTHOR_NAME": DETERMINISTIC_AUTHOR_NAME,
                    "GIT_AUTHOR_EMAIL": DETERMINISTIC_AUTHOR_EMAIL,
                    "GIT_AUTHOR_DATE": DETERMINISTIC_COMMIT_DATE,
                    "GIT_COMMITTER_NAME": DETERMINISTIC_AUTHOR_NAME,
                    "GIT_COMMITTER_EMAIL": DETERMINISTIC_AUTHOR_EMAIL,
                    "GIT_COMMITTER_DATE": DETERMINISTIC_COMMIT_DATE,
                }
            )
            commit = _run_git(
                worktree,
                "commit",
                "-m",
                message,
                env=commit_env,
            )
            del commit
            batch_sha = _run_git(worktree, "rev-parse", "HEAD")
            assert isinstance(batch_sha, str)
            return batch_sha.strip()
        finally:
            subprocess.run(
                ("git", "worktree", "remove", "--force", str(worktree)),
                cwd=self.repository,
                check=False,
                capture_output=True,
                text=True,
            )
            directory.cleanup()

    def clean_base_advance(
        self,
        batch_id: str,
        target: "BatchTarget",
        member: AcceptedCandidateReceipt,
    ) -> "CleanBaseAdvanceProof":
        self.clean_base_advance_calls.append(member.ticket_key)
        candidate_tree = self._verify_member(member)
        ancestor = self.read_ancestor(member.base_sha, target.target_head_sha)
        target_delta = self._apply_member_policy(
            self.read_target_delta(member.base_sha, target), member
        )
        original_patch_digest = _patch_identity_for_trees(
            self.repository, member.base_tree_oid, candidate_tree
        )
        directory, worktree = self._with_worktree(target.target_head_sha)
        try:
            completed = subprocess.run(
                (
                    "git",
                    "merge",
                    "--no-ff",
                    "--no-commit",
                    "--no-edit",
                    member.candidate_sha,
                ),
                cwd=worktree,
                check=False,
                capture_output=True,
                text=True,
            )
            if completed.returncode != 0:
                subprocess.run(
                    ("git", "merge", "--abort"),
                    cwd=worktree,
                    check=False,
                    capture_output=True,
                    text=True,
                )
                raise _batch_error(
                    "BATCH_CLEAN_BASE_COMPOSITION_CONFLICT",
                    completed.stderr.strip() or "Candidate cannot advance onto target",
                )
            advanced_tree = _run_git(worktree, "write-tree")
            assert isinstance(advanced_tree, str)
            advanced_tree = advanced_tree.strip()
        finally:
            subprocess.run(
                ("git", "worktree", "remove", "--force", str(worktree)),
                cwd=self.repository,
                check=False,
                capture_output=True,
                text=True,
            )
            directory.cleanup()
        recomputed_patch_digest = (
            self.recomputed_patch_digest
            or _patch_identity_for_trees(
                self.repository, target.target_tree_oid, advanced_tree
            )
        )
        return require_clean_base_advance(
            member=member,
            original_patch_digest=original_patch_digest,
            recomputed_patch_digest=recomputed_patch_digest,
            ancestor=ancestor,
            target_delta=target_delta,
            target_tree_oid=target.target_tree_oid,
            advanced_member_tree_oid=advanced_tree,
        )

    def compose_batch(
        self,
        batch_id: str,
        target: "BatchTarget",
        members: tuple[AcceptedCandidateReceipt, ...],
    ) -> str:
        if not members:
            raise _batch_error("BATCH_EMPTY", "cannot compose an empty Batch")
        target = self.read_target(target)
        for member in members:
            self._verify_member(member)
        for member in members:
            if (
                member.base_sha != target.target_head_sha
                or member.base_tree_oid != target.target_tree_oid
            ):
                self.clean_base_advance(batch_id, target, member)
        ref = f"{_BATCH_REF_PREFIX}{batch_id}"
        existing = self.read_ref(ref)
        if existing is not None:
            self._verify_batch_ref(existing, target, members)
            if self.read_target(target) != target:
                raise _batch_error(
                    "BATCH_TARGET_READBACK_MISMATCH",
                    "target branch moved while reusing a Batch ref",
                )
            return existing
        self.compose_calls += 1
        if len(members) == 1 and (
            members[0].base_sha == target.target_head_sha
            and members[0].base_tree_oid == target.target_tree_oid
        ):
            batch_sha = members[0].candidate_sha
        else:
            batch_sha = self._merge_target_and_members(batch_id, target, members)
        self._verify_batch_ref(batch_sha, target, members)
        published = self.update_ref_cas(ref, None, batch_sha)
        self.crash_hook("batch_ref_publication")
        readback = self.read_ref(ref)
        if readback != published or readback != batch_sha:
            raise _batch_error(
                "BATCH_REF_IDENTITY_MISMATCH",
                "Batch ref did not read back to its composed SHA",
            )
        self._verify_batch_ref(readback, target, members)
        if self.read_target(target) != target:
            raise _batch_error(
                "BATCH_TARGET_READBACK_MISMATCH",
                "target branch moved during Batch preparation",
            )
        return readback

    def tree_contains(self, batch_sha: str, path: str) -> bool:
        _require_object_id("batch SHA", batch_sha)
        _require_text("tree path", path)
        raw = _run_git(
            self.repository,
            "ls-tree",
            "-r",
            "--name-only",
            batch_sha,
        )
        assert isinstance(raw, str)
        return path in {line for line in raw.splitlines() if line}


__all__ = [
    "GitBatchDriver",
    "GitCliBatchDriver",
    "LocalCheckReceipt",
    "LocalSuiteDriver",
    "patch_identity_for_trees",
]
