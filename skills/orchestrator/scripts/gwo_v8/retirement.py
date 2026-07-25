"""Kernel-authorized, read-backed Runtime retirement.

This module owns the policy and durable identity for destructive cleanup.  The
Kernel may request an authorization only after exact target-branch readback;
Runtime adapters consume that authorization at their one destructive seam.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import subprocess
from typing import Any, Iterable

from ._canonical import digest_value
from .runtime import RuntimeBinding


class RetirementError(RuntimeError):
    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class RetirementAuthorization:
    repository: str
    plan_digest: str
    node_key: str
    admission_id: str
    attempt_id: str
    agent_id: str
    workspace_id: str
    candidate_sha: str
    integrated_sha: str
    target_branch: str
    temporary_branch: str | None
    authorization_digest: str

    @property
    def identity(self) -> dict[str, Any]:
        return {
            "repository": self.repository,
            "plan_digest": self.plan_digest,
            "node_key": self.node_key,
            "admission_id": self.admission_id,
            "attempt_id": self.attempt_id,
            "agent_id": self.agent_id,
            "workspace_id": self.workspace_id,
            "candidate_sha": self.candidate_sha,
            "integrated_sha": self.integrated_sha,
            "target_branch": self.target_branch,
            "temporary_branch": self.temporary_branch,
        }

    def assert_valid_digest(self) -> None:
        if digest_value(self.identity) != self.authorization_digest:
            raise RetirementError(
                "RETIREMENT_AUTHORIZATION_DIGEST_MISMATCH",
                "retirement authorization identity does not match its digest",
            )


@dataclass(frozen=True)
class RetirementReadback:
    repository: str
    admission_id: str
    attempt_id: str
    agent_id: str
    workspace_id: str
    candidate_sha: str
    integrated_sha: str
    target_branch: str
    authorization_digest: str
    agent_archived: bool
    directory_absent: bool
    worktree_absent: bool
    branch_deleted: bool

    @property
    def complete(self) -> bool:
        return (
            self.agent_archived
            and self.directory_absent
            and self.worktree_absent
            and self.branch_deleted
        )


@dataclass(frozen=True)
class WorktreeRegistration:
    head: str
    branch: str | None


def pending_retirement(
    authorization: RetirementAuthorization,
) -> dict[str, Any]:
    authorization.assert_valid_digest()
    return {
        "state": "pending",
        "authorization": {
            **authorization.identity,
            "authorization_digest": authorization.authorization_digest,
        },
        "error": None,
        "evidence": None,
    }


def failed_retirement(
    authorization: RetirementAuthorization,
    *,
    code: str,
    failure_class: str,
) -> dict[str, Any]:
    record = pending_retirement(authorization)
    record.update(
        {
            "state": "error",
            "error": {
                "code": code,
                "failure_class": failure_class,
            },
        }
    )
    return record


def completed_retirement(
    authorization: RetirementAuthorization,
    readback: RetirementReadback,
) -> dict[str, Any]:
    if (
        not readback.complete
        or readback.authorization_digest != authorization.authorization_digest
    ):
        raise RetirementError(
            "RETIREMENT_READBACK_IDENTITY_MISMATCH",
            "retirement readback does not complete the exact authorization",
        )
    record = pending_retirement(authorization)
    record.update(
        {
            "state": "complete",
            "evidence": {
                "repository": readback.repository,
                "admission_id": readback.admission_id,
                "attempt_id": readback.attempt_id,
                "agent_id": readback.agent_id,
                "workspace_id": readback.workspace_id,
                "candidate_sha": readback.candidate_sha,
                "integrated_sha": readback.integrated_sha,
                "target_branch": readback.target_branch,
                "temporary_branch": authorization.temporary_branch,
                "authorization_digest": readback.authorization_digest,
                "agent_archived": readback.agent_archived,
                "directory_absent": readback.directory_absent,
                "worktree_absent": readback.worktree_absent,
                "branch_deleted": readback.branch_deleted,
            },
        }
    )
    return record


def _git(repository: Path, *args: str, check: bool = True) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repository,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if check and result.returncode != 0:
        raise RetirementError(
            "RETIREMENT_GIT_READBACK_FAILED",
            result.stderr.strip() or result.stdout.strip() or "git readback failed",
        )
    return result.stdout.strip()


def _registered_worktrees(repository: Path) -> dict[Path, WorktreeRegistration]:
    listing = _git(repository, "worktree", "list", "--porcelain")
    registrations: dict[Path, WorktreeRegistration] = {}
    entry: dict[str, str] = {}
    for line in (*listing.splitlines(), ""):
        if not line:
            path = entry.get("worktree")
            head = entry.get("HEAD")
            if path and head:
                branch_ref = entry.get("branch")
                branch = (
                    branch_ref.removeprefix("refs/heads/")
                    if branch_ref is not None
                    else None
                )
                registrations[Path(path).resolve()] = WorktreeRegistration(
                    head=head,
                    branch=branch,
                )
            entry = {}
            continue
        key, _, value = line.partition(" ")
        entry[key] = value
    return registrations


def _required_binding_identity(binding: RuntimeBinding) -> dict[str, str]:
    values: dict[str, Any] = {
        "repository": binding.repository,
        "plan_digest": binding.plan_digest,
        "node_key": binding.node_key,
        "admission_id": binding.admission_id,
        "attempt_id": binding.attempt_id,
        "agent_id": binding.agent_id,
        "workspace_id": binding.workspace_id,
    }
    if any(not isinstance(value, str) or not value for value in values.values()):
        raise RetirementError(
            "RETIREMENT_BINDING_INCOMPLETE",
            "retirement requires complete repository, Attempt, Agent, and Workspace identity",
        )
    return {key: str(value) for key, value in values.items()}


def assert_authorization_matches(
    binding: RuntimeBinding,
    authorization: RetirementAuthorization,
) -> None:
    authorization.assert_valid_digest()
    binding_identity = _required_binding_identity(binding)
    expected = {
        **binding_identity,
        "candidate_sha": authorization.candidate_sha,
        "integrated_sha": authorization.integrated_sha,
        "target_branch": authorization.target_branch,
        "temporary_branch": authorization.temporary_branch,
    }
    if authorization.identity != expected:
        raise RetirementError(
            "RETIREMENT_AUTHORIZATION_IDENTITY_MISMATCH",
            "authorization does not bind this Runtime Binding",
        )


def validate_disposable_worktree(
    *,
    binding: RuntimeBinding,
    authorization: RetirementAuthorization,
    active_bindings: Iterable[RuntimeBinding] = (),
) -> WorktreeRegistration:
    """Fail closed unless one clean, owned, disposable worktree is identified."""

    assert_authorization_matches(binding, authorization)
    repository = Path(binding.repository_path).resolve()
    workspace = Path(binding.workspace).resolve()
    if workspace == repository:
        raise RetirementError(
            "RETIREMENT_STABLE_WORKSPACE",
            "the repository Integration workspace is never disposable",
        )
    shared = [
        item
        for item in active_bindings
        if item.agent_id != binding.agent_id
        and (
            item.workspace_id == binding.workspace_id
            or Path(item.workspace).resolve() == workspace
        )
    ]
    if shared:
        raise RetirementError(
            "RETIREMENT_SHARED_WORKSPACE",
            "another active Runtime Binding shares the Candidate workspace",
        )
    registrations = _registered_worktrees(repository)
    registration = registrations.get(workspace)
    if registration is None:
        raise RetirementError(
            "RETIREMENT_WORKTREE_AMBIGUOUS",
            "the exact Candidate workspace is not a registered Git worktree",
        )
    if (
        registration.branch != authorization.temporary_branch
        or
        registration.branch == authorization.target_branch
        or (
            registration.branch is not None
            and not registration.branch.startswith(("gwo/", "gwo-"))
        )
    ):
        raise RetirementError(
            "RETIREMENT_STABLE_WORKSPACE",
            "stable or non-temporary worktree branches are never disposable",
        )
    status = _git(
        workspace,
        "status",
        "--porcelain=v1",
        "--untracked-files=all",
    )
    if status:
        raise RetirementError(
            "RETIREMENT_WORKTREE_DIRTY",
            "Candidate workspace contains tracked or untracked WIP",
        )
    head = _git(workspace, "rev-parse", "HEAD")
    if head != authorization.candidate_sha or registration.head != head:
        raise RetirementError(
            "RETIREMENT_CANDIDATE_MISMATCH",
            "Candidate workspace HEAD does not match the authorization",
        )
    target_head = _git(repository, "rev-parse", authorization.target_branch)
    if target_head != authorization.integrated_sha:
        raise RetirementError(
            "INTEGRATION_READBACK_MISMATCH",
            "target branch moved away from the authorized integrated SHA",
        )
    ancestor = subprocess.run(
        [
            "git",
            "merge-base",
            "--is-ancestor",
            authorization.candidate_sha,
            authorization.integrated_sha,
        ],
        cwd=repository,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if ancestor.returncode != 0:
        raise RetirementError(
            "CANDIDATE_NOT_INTEGRATED",
            "Candidate is not an ancestor of the integrated SHA",
        )
    return registration


def delete_temporary_branch_cas(
    repository: Path,
    registration: WorktreeRegistration,
    *,
    candidate_sha: str,
) -> None:
    if registration.branch is None:
        return
    if not registration.branch.startswith(("gwo/", "gwo-")):
        raise RetirementError(
            "RETIREMENT_STABLE_WORKSPACE",
            "only an exact GWO temporary branch may be CAS deleted",
        )
    branch_ref = f"refs/heads/{registration.branch}"
    current = _git(repository, "rev-parse", "--verify", branch_ref)
    if current != candidate_sha:
        raise RetirementError(
            "RETIREMENT_BRANCH_CAS_MISMATCH",
            "temporary branch moved away from the authorized Candidate",
        )
    _git(repository, "update-ref", "-d", branch_ref, candidate_sha)


def read_retirement_completion(
    *,
    binding: RuntimeBinding,
    authorization: RetirementAuthorization,
    agent_archived: bool,
    branch_deleted: bool,
) -> RetirementReadback:
    repository = Path(binding.repository_path).resolve()
    workspace = Path(binding.workspace).resolve()
    worktree_absent = workspace not in _registered_worktrees(repository)
    readback = RetirementReadback(
        repository=authorization.repository,
        admission_id=authorization.admission_id,
        attempt_id=authorization.attempt_id,
        agent_id=authorization.agent_id,
        workspace_id=authorization.workspace_id,
        candidate_sha=authorization.candidate_sha,
        integrated_sha=authorization.integrated_sha,
        target_branch=authorization.target_branch,
        authorization_digest=authorization.authorization_digest,
        agent_archived=agent_archived,
        directory_absent=not workspace.exists(),
        worktree_absent=worktree_absent,
        branch_deleted=branch_deleted,
    )
    if not readback.complete:
        raise RetirementError(
            "RETIREMENT_READBACK_INCOMPLETE",
            "Agent, directory, worktree, and branch retirement did not all read back",
        )
    return readback


def authorize_after_integration(
    *,
    binding: RuntimeBinding,
    candidate_sha: str,
    integrated_sha: str,
    target_branch: str,
) -> RetirementAuthorization:
    """Issue one exact, path-free authorization after Integration readback."""

    identity = _required_binding_identity(binding)
    if (
        re.fullmatch(r"[0-9a-f]{40}", candidate_sha) is None
        or re.fullmatch(r"[0-9a-f]{40}", integrated_sha) is None
        or not isinstance(target_branch, str)
        or not target_branch
    ):
        raise RetirementError(
            "RETIREMENT_INTEGRATION_IDENTITY_INVALID",
            "Candidate, integrated SHA, and target branch are required",
        )
    repository = Path(binding.repository_path).resolve()
    if not repository.is_dir():
        raise RetirementError(
            "RETIREMENT_REPOSITORY_UNREADABLE",
            "Runtime Binding repository cannot be read back",
        )
    target_head = _git(repository, "rev-parse", target_branch)
    if target_head != integrated_sha:
        raise RetirementError(
            "INTEGRATION_READBACK_MISMATCH",
            "target branch does not read back the authorized integrated SHA",
        )
    ancestor = subprocess.run(
        ["git", "merge-base", "--is-ancestor", candidate_sha, integrated_sha],
        cwd=repository,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if ancestor.returncode != 0:
        raise RetirementError(
            "CANDIDATE_NOT_INTEGRATED",
            "Candidate is not an ancestor of the integrated SHA",
        )
    registration = _registered_worktrees(repository).get(
        Path(binding.workspace).resolve()
    )
    if registration is None:
        raise RetirementError(
            "RETIREMENT_WORKTREE_AMBIGUOUS",
            "Integration readback cannot identify the exact Candidate worktree",
        )
    payload = {
        **identity,
        "candidate_sha": candidate_sha,
        "integrated_sha": integrated_sha,
        "target_branch": target_branch,
        "temporary_branch": registration.branch,
    }
    authorization = RetirementAuthorization(
        **payload,
        authorization_digest=digest_value(payload),
    )
    authorization.assert_valid_digest()
    return authorization
