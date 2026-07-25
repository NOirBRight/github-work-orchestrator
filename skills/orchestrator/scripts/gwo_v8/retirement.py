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
from typing import Any, Iterable, Mapping

from ._canonical import digest_value
from .evidence import TypedEvidence
from .runtime import ReviewAxisBinding, RuntimeBinding


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


@dataclass(frozen=True)
class WorktreeRegistration:
    head: str
    branch: str | None


@dataclass(frozen=True)
class ReviewRetirementAuthorization:
    repository: str
    plan_digest: str
    node_key: str
    admission_id: str
    attempt_id: str
    parent_agent_id: str
    parent_workspace_id: str
    action_key: str
    axis: str
    agent_id: str
    session_id: str
    workspace_id: str
    candidate_sha: str
    temporary_branch: str | None
    review_evidence_digest: str
    authorization_digest: str

    @property
    def identity(self) -> dict[str, Any]:
        return {
            "repository": self.repository,
            "plan_digest": self.plan_digest,
            "node_key": self.node_key,
            "admission_id": self.admission_id,
            "attempt_id": self.attempt_id,
            "parent_agent_id": self.parent_agent_id,
            "parent_workspace_id": self.parent_workspace_id,
            "action_key": self.action_key,
            "axis": self.axis,
            "agent_id": self.agent_id,
            "session_id": self.session_id,
            "workspace_id": self.workspace_id,
            "candidate_sha": self.candidate_sha,
            "temporary_branch": self.temporary_branch,
            "review_evidence_digest": self.review_evidence_digest,
        }

    def assert_valid_digest(self) -> None:
        if digest_value(self.identity) != self.authorization_digest:
            raise RetirementError(
                "REVIEW_RETIREMENT_AUTHORIZATION_DIGEST_MISMATCH",
                "Review retirement identity does not match its digest",
            )


@dataclass(frozen=True)
class ReviewRetirementReadback:
    repository: str
    plan_digest: str
    node_key: str
    admission_id: str
    attempt_id: str
    parent_agent_id: str
    parent_workspace_id: str
    action_key: str
    axis: str
    agent_id: str
    session_id: str
    workspace_id: str
    candidate_sha: str
    temporary_branch: str | None
    review_evidence_digest: str
    authorization_digest: str
    workspace_disposition: str
    agent_archived: bool
    directory_absent: bool
    worktree_absent: bool
    branch_deleted: bool

    @property
    def identity(self) -> dict[str, Any]:
        return {
            "repository": self.repository,
            "plan_digest": self.plan_digest,
            "node_key": self.node_key,
            "admission_id": self.admission_id,
            "attempt_id": self.attempt_id,
            "parent_agent_id": self.parent_agent_id,
            "parent_workspace_id": self.parent_workspace_id,
            "action_key": self.action_key,
            "axis": self.axis,
            "agent_id": self.agent_id,
            "session_id": self.session_id,
            "workspace_id": self.workspace_id,
            "candidate_sha": self.candidate_sha,
            "temporary_branch": self.temporary_branch,
            "review_evidence_digest": self.review_evidence_digest,
        }

    @property
    def complete(self) -> bool:
        if self.workspace_disposition == "shared_preserved":
            return (
                self.agent_archived
                and not self.directory_absent
                and not self.worktree_absent
                and not self.branch_deleted
            )
        return (
            self.workspace_disposition == "disposable_removed"
            and self.agent_archived
            and self.directory_absent
            and self.worktree_absent
            and self.branch_deleted
        )


@dataclass(frozen=True)
class ValidatedReviewRetirements:
    records: dict[str, dict[str, Any]]
    children_retired: bool


def _contains_absolute_path(value: Any) -> bool:
    if isinstance(value, str):
        return Path(value).is_absolute()
    if isinstance(value, dict):
        return any(_contains_absolute_path(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return any(_contains_absolute_path(item) for item in value)
    return False


def validate_review_retirement_records(
    *,
    records: Any,
    worker_binding: RuntimeBinding,
    review_bindings: Mapping[str, ReviewAxisBinding],
    review_evidence: TypedEvidence | None,
) -> ValidatedReviewRetirements:
    """Validate durable Review cleanup facts and derive their completion."""

    if not isinstance(records, dict) or any(
        not isinstance(key, str) or not isinstance(value, dict)
        for key, value in records.items()
    ):
        raise RetirementError(
            "REVIEW_RETIREMENT_READBACK_CONTRADICTION",
            "Review retirement records must be an axis-keyed object",
        )
    if set(records) - set(review_bindings):
        raise RetirementError(
            "REVIEW_RETIREMENT_READBACK_CONTRADICTION",
            "Review retirement records contain an unknown axis identity",
        )
    completed: set[str] = set()
    authorization_fields = set(
        ReviewRetirementAuthorization.__dataclass_fields__
    )
    readback_fields = set(ReviewRetirementReadback.__dataclass_fields__)
    record_fields = {"state", "authorization", "error", "evidence"}
    error_fields = {"code", "failure_class"}
    worker_identity = _required_binding_identity(worker_binding)
    for key, record in records.items():
        if set(record) != record_fields or _contains_absolute_path(record):
            raise RetirementError(
                "REVIEW_RETIREMENT_READBACK_CONTRADICTION",
                "Review retirement durable schema is not exact and path-free",
            )
        state = record.get("state")
        if state not in {"pending", "error", "complete"}:
            raise RetirementError(
                "REVIEW_RETIREMENT_READBACK_CONTRADICTION",
                "Review retirement state is not typed",
            )
        error_value = record.get("error")
        if state == "error":
            if (
                not isinstance(error_value, dict)
                or set(error_value) != error_fields
                or any(
                    not isinstance(error_value.get(field), str)
                    or not error_value.get(field)
                    for field in error_fields
                )
                or record.get("evidence") is not None
            ):
                raise RetirementError(
                    "REVIEW_RETIREMENT_READBACK_CONTRADICTION",
                    "Review retirement error is not typed",
                )
        elif error_value is not None:
            raise RetirementError(
                "REVIEW_RETIREMENT_READBACK_CONTRADICTION",
                "non-error Review retirement carries an error",
            )

        authorization_value = record.get("authorization")
        if authorization_value is None:
            if state != "error":
                raise RetirementError(
                    "REVIEW_RETIREMENT_READBACK_CONTRADICTION",
                    "Review retirement omitted its typed authorization",
                )
            continue
        if (
            not isinstance(authorization_value, dict)
            or set(authorization_value) != authorization_fields
        ):
            raise RetirementError(
                "REVIEW_RETIREMENT_READBACK_CONTRADICTION",
                "Review retirement authorization schema is not exact",
            )
        try:
            authorization = ReviewRetirementAuthorization(
                **authorization_value
            )
            authorization.assert_valid_digest()
        except (RetirementError, TypeError) as error:
            raise RetirementError(
                "REVIEW_RETIREMENT_READBACK_CONTRADICTION",
                "Review retirement authorization is invalid",
            ) from error
        child = review_bindings[key]
        review_parent = child.parent_agent_id or child.declared_parent_agent_id
        expected = {
            **worker_identity,
            "parent_agent_id": worker_binding.agent_id,
            "parent_workspace_id": worker_binding.workspace_id,
            "action_key": child.action_key,
            "axis": child.axis,
            "agent_id": child.agent_id,
            "session_id": child.session_id,
            "workspace_id": child.workspace_id,
            "candidate_sha": child.candidate_sha,
        }
        if (
            review_evidence is None
            or review_evidence.kind != "review"
            or not review_evidence.has_valid_digest()
            or review_evidence.subject != child.candidate_sha
            or review_evidence.payload.get("attempt_id")
            != worker_binding.attempt_id
            or review_evidence.payload.get("candidate_sha")
            != child.candidate_sha
            or review_parent != worker_binding.agent_id
            or any(
                authorization.identity.get(field) != value
                for field, value in expected.items()
            )
            or authorization.review_evidence_digest
            != review_evidence.content_digest
        ):
            raise RetirementError(
                "REVIEW_RETIREMENT_READBACK_CONTRADICTION",
                "Review retirement identity does not match its child Evidence",
            )
        evidence_value = record.get("evidence")
        if state != "complete":
            if evidence_value is not None:
                raise RetirementError(
                    "REVIEW_RETIREMENT_READBACK_CONTRADICTION",
                    "incomplete Review retirement carries completion readback",
                )
            continue
        if (
            not isinstance(evidence_value, dict)
            or set(evidence_value) != readback_fields
        ):
            raise RetirementError(
                "REVIEW_RETIREMENT_READBACK_CONTRADICTION",
                "complete Review retirement omitted exact readback",
            )
        try:
            completed_review_retirement(
                authorization,
                ReviewRetirementReadback(**evidence_value),
            )
        except (RetirementError, TypeError) as error:
            raise RetirementError(
                "REVIEW_RETIREMENT_READBACK_CONTRADICTION",
                "Review retirement completion readback is invalid",
            ) from error
        completed.add(key)
    return ValidatedReviewRetirements(
        records={
            key: dict(value)
            for key, value in records.items()
        },
        children_retired=(
            bool(review_bindings)
            and completed == set(review_bindings)
        ),
    )


def pending_review_retirement(
    authorization: ReviewRetirementAuthorization,
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


def failed_review_retirement(
    authorization: ReviewRetirementAuthorization | None,
    *,
    code: str,
    failure_class: str,
) -> dict[str, Any]:
    return {
        "state": "error",
        "authorization": (
            None
            if authorization is None
            else {
                **authorization.identity,
                "authorization_digest": authorization.authorization_digest,
            }
        ),
        "error": {
            "code": code,
            "failure_class": failure_class,
        },
        "evidence": None,
    }


def completed_review_retirement(
    authorization: ReviewRetirementAuthorization,
    readback: ReviewRetirementReadback,
) -> dict[str, Any]:
    if (
        not readback.complete
        or readback.identity != authorization.identity
        or readback.authorization_digest != authorization.authorization_digest
    ):
        raise RetirementError(
            "REVIEW_RETIREMENT_READBACK_IDENTITY_MISMATCH",
            "Review retirement receipt does not match its authorization",
        )
    record = pending_review_retirement(authorization)
    record.update(
        {
            "state": "complete",
            "evidence": {
                **readback.identity,
                "authorization_digest": readback.authorization_digest,
                "workspace_disposition": readback.workspace_disposition,
                "agent_archived": readback.agent_archived,
                "directory_absent": readback.directory_absent,
                "worktree_absent": readback.worktree_absent,
                "branch_deleted": readback.branch_deleted,
            },
        }
    )
    return record


def resolve_native_worktree_name(
    *,
    workspace: Path,
    candidate_sha: str,
    temporary_branch: str | None,
    worktrees: Iterable[Mapping[str, Any]],
) -> str:
    """Resolve one Paseo-native archive name from canonical list readback."""

    expected_path = workspace.resolve()
    exact = [
        item
        for item in worktrees
        if isinstance(item.get("path"), str)
        and Path(str(item["path"])).resolve() == expected_path
    ]
    if len(exact) != 1:
        raise RetirementError(
            "PASEO_WORKTREE_IDENTITY_AMBIGUOUS",
            "Paseo worktree list did not identify one exact canonical path",
        )
    item = exact[0]
    native_name = item.get("native_name")
    if (
        not isinstance(native_name, str)
        or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", native_name) is None
        or item.get("head") != candidate_sha
        or item.get("branch_name") != temporary_branch
    ):
        raise RetirementError(
            "PASEO_WORKTREE_IDENTITY_MISMATCH",
            "Paseo worktree readback does not match Candidate identity",
        )
    return native_name


def authorize_review_after_evidence(
    *,
    worker_binding: RuntimeBinding,
    review_binding: ReviewAxisBinding,
    review_evidence: TypedEvidence,
) -> ReviewRetirementAuthorization:
    """Bind Review retirement only after exact Review Evidence convergence."""

    worker = _required_binding_identity(worker_binding)
    parent_agent_id = str(worker_binding.agent_id)
    parent_workspace_id = str(worker_binding.workspace_id)
    review_parent = (
        review_binding.parent_agent_id
        or review_binding.declared_parent_agent_id
    )
    if (
        review_parent != parent_agent_id
        or review_binding.candidate_sha != review_evidence.subject
        or review_evidence.kind != "review"
        or not review_evidence.has_valid_digest()
        or review_evidence.payload.get("attempt_id") != worker_binding.attempt_id
        or review_evidence.payload.get("candidate_sha")
        != review_binding.candidate_sha
    ):
        raise RetirementError(
            "REVIEW_RETIREMENT_EVIDENCE_MISMATCH",
            "Review retirement requires exact converged Evidence and parent identity",
        )
    registration = _registered_worktrees(
        Path(worker_binding.repository_path).resolve()
    ).get(Path(review_binding.workspace).resolve())
    if registration is None:
        raise RetirementError(
            "REVIEW_RETIREMENT_WORKTREE_AMBIGUOUS",
            "Review retirement cannot identify its exact Git worktree",
        )
    payload = {
        **worker,
        "parent_agent_id": parent_agent_id,
        "parent_workspace_id": parent_workspace_id,
        "action_key": review_binding.action_key,
        "axis": review_binding.axis,
        "agent_id": review_binding.agent_id,
        "session_id": review_binding.session_id,
        "workspace_id": review_binding.workspace_id,
        "candidate_sha": review_binding.candidate_sha,
        "temporary_branch": registration.branch,
        "review_evidence_digest": review_evidence.content_digest,
    }
    authorization = ReviewRetirementAuthorization(
        **payload,
        authorization_digest=digest_value(payload),
    )
    authorization.assert_valid_digest()
    return authorization


def assert_review_authorization_matches(
    binding: ReviewAxisBinding,
    authorization: ReviewRetirementAuthorization,
) -> None:
    authorization.assert_valid_digest()
    expected = {
        "action_key": binding.action_key,
        "axis": binding.axis,
        "agent_id": binding.agent_id,
        "session_id": binding.session_id,
        "workspace_id": binding.workspace_id,
        "candidate_sha": binding.candidate_sha,
    }
    if any(
        authorization.identity.get(key) != value
        for key, value in expected.items()
    ):
        raise RetirementError(
            "REVIEW_RETIREMENT_AUTHORIZATION_IDENTITY_MISMATCH",
            "Review authorization does not bind this child identity",
        )


def validate_disposable_review_worktree(
    *,
    repository: Path,
    workspace: Path,
    authorization: ReviewRetirementAuthorization,
) -> WorktreeRegistration:
    repository = repository.resolve()
    workspace = workspace.resolve()
    if workspace == repository:
        raise RetirementError(
            "REVIEW_RETIREMENT_STABLE_WORKSPACE",
            "Coordinator and Integration workspaces are never disposable",
        )
    registration = _registered_worktrees(repository).get(workspace)
    if registration is None:
        raise RetirementError(
            "REVIEW_RETIREMENT_WORKTREE_AMBIGUOUS",
            "Review workspace is not one exact registered Git worktree",
        )
    if (
        registration.branch != authorization.temporary_branch
        or (
            registration.branch is not None
            and not registration.branch.startswith(("gwo/", "gwo-"))
        )
    ):
        raise RetirementError(
            "REVIEW_RETIREMENT_STABLE_WORKSPACE",
            "only a GWO disposable Review worktree may be removed",
        )
    status = _git(
        workspace,
        "status",
        "--porcelain=v1",
        "--untracked-files=all",
    )
    if status:
        raise RetirementError(
            "REVIEW_RETIREMENT_WORKTREE_DIRTY",
            "Review workspace contains tracked or untracked WIP",
        )
    head = _git(workspace, "rev-parse", "HEAD")
    if head != authorization.candidate_sha or registration.head != head:
        raise RetirementError(
            "REVIEW_RETIREMENT_CANDIDATE_MISMATCH",
            "Review workspace does not read back the authorized Candidate",
        )
    return registration


def review_retirement_readback(
    *,
    authorization: ReviewRetirementAuthorization,
    workspace_disposition: str,
    agent_archived: bool,
    directory_absent: bool,
    worktree_absent: bool,
    branch_deleted: bool,
) -> ReviewRetirementReadback:
    readback = ReviewRetirementReadback(
        **authorization.identity,
        authorization_digest=authorization.authorization_digest,
        workspace_disposition=workspace_disposition,
        agent_archived=agent_archived,
        directory_absent=directory_absent,
        worktree_absent=worktree_absent,
        branch_deleted=branch_deleted,
    )
    if not readback.complete:
        raise RetirementError(
            "REVIEW_RETIREMENT_READBACK_INCOMPLETE",
            "Review Agent/worktree retirement did not read back complete",
        )
    return readback


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
    authorization: RetirementAuthorization | None,
    *,
    code: str,
    failure_class: str,
) -> dict[str, Any]:
    record = (
        {
            "state": "error",
            "authorization": None,
            "error": None,
            "evidence": None,
        }
        if authorization is None
        else pending_retirement(authorization)
    )
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
        or readback.identity != authorization.identity
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
                **readback.identity,
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
    present = subprocess.run(
        ["git", "show-ref", "--verify", "--quiet", branch_ref],
        cwd=repository,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if present.returncode == 1:
        return
    if present.returncode != 0:
        raise RetirementError(
            "RETIREMENT_GIT_READBACK_FAILED",
            present.stderr.strip() or "temporary branch readback failed",
        )
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
        plan_digest=authorization.plan_digest,
        node_key=authorization.node_key,
        admission_id=authorization.admission_id,
        attempt_id=authorization.attempt_id,
        agent_id=authorization.agent_id,
        workspace_id=authorization.workspace_id,
        candidate_sha=authorization.candidate_sha,
        integrated_sha=authorization.integrated_sha,
        target_branch=authorization.target_branch,
        temporary_branch=authorization.temporary_branch,
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
