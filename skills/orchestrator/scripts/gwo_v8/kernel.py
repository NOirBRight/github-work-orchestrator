"""Deterministic V8 Kernel reconciliation over a private SQLite Store."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import time
from typing import Any, Protocol
from urllib.parse import quote

from ._canonical import canonical_bytes, digest_bytes, digest_value
from .activation import LocalPlanPublication
from .evidence import (
    EvidenceVerifier,
    ResultClaim,
    TypedEvidence,
    blocking_review_findings,
)
from .integration_batch import (
    GitIntegrationBatchAssembler,
    IntegrationBatchError,
    IntegrationBatchMember,
)
from .review_convergence import ReviewConvergence, ReviewConvergenceError
from .runtime import (
    RuntimeAdapter,
    RuntimeAdapterError,
    RuntimeAdmission,
    RuntimeBinding,
    RuntimeObservation,
    RuntimeProfile,
    RuntimePrompt,
    SkillCatalog,
    _environment_snapshot,
    _input_projection_digest,
    _run,
    resolve_active_turn_pools,
    resolve_worker_profile,
)

REPAIR_PACKET_MAX_BYTES = 64 * 1024
REPAIR_CHANGED_FILES_MAX_BYTES = 4 * 1024
REPAIR_CHANGED_FILE_MAX_CHARACTERS = 256


class KernelError(RuntimeError):
    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code = code
        self.detail = detail


class DeliveryControlError(RuntimeError):
    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class CandidatePublication:
    repository: str
    candidate_sha: str
    evidence_manifest_digest: str
    source_ref: str


@dataclass(frozen=True)
class HostedCheckReadback:
    candidate_sha: str
    status: str
    source_ref: str
    definition_digests: tuple[str, ...] = ()


@dataclass(frozen=True)
class IntegrationReadback:
    target_branch: str
    candidate_sha: str
    source_ref: str


class DeliveryControl(Protocol):
    def read_publication(
        self,
        repository: str,
        candidate_sha: str,
    ) -> CandidatePublication | None: ...

    def publish_once(
        self,
        repository: str,
        candidate_sha: str,
        evidence_manifest_digest: str,
        *,
        target_branch: str | None = None,
    ) -> CandidatePublication: ...

    def read_hosted_checks(
        self,
        repository: str,
        candidate_sha: str,
        required_checks: tuple[dict[str, Any], ...] = (),
    ) -> HostedCheckReadback: ...

    def retry_hosted_checks(
        self,
        repository: str,
        candidate_sha: str,
    ) -> None: ...

    def integrate_serially(
        self,
        repository: str,
        candidate_sha: str,
        target_branch: str,
    ) -> IntegrationReadback: ...


class InMemoryDeliveryControl:
    """Deterministic exact-SHA delivery fake used through the Kernel seam."""

    def __init__(self, *, hosted_outcomes: tuple[str, ...]):
        if not hosted_outcomes:
            raise DeliveryControlError(
                "HOSTED_OUTCOMES_MISSING",
                "at least one hosted outcome is required",
            )
        self._hosted_outcomes = list(hosted_outcomes)
        self._last_hosted_outcome = hosted_outcomes[-1]
        self._publications: dict[tuple[str, str], CandidatePublication] = {}
        self._last_publication: CandidatePublication | None = None
        self.publication_count = 0
        self.hosted_retry_count = 0
        self.hosted_read_candidates: list[str] = []
        self.integrated_candidates: list[str] = []

    @property
    def published_candidate_sha(self) -> str | None:
        return (
            None
            if self._last_publication is None
            else self._last_publication.candidate_sha
        )

    def read_publication(
        self,
        repository: str,
        candidate_sha: str,
    ) -> CandidatePublication | None:
        return self._publications.get((repository, candidate_sha))

    def publish_once(
        self,
        repository: str,
        candidate_sha: str,
        evidence_manifest_digest: str,
        *,
        target_branch: str | None = None,
    ) -> CandidatePublication:
        del target_branch
        existing = self.read_publication(repository, candidate_sha)
        if existing is not None:
            if existing.evidence_manifest_digest != evidence_manifest_digest:
                raise DeliveryControlError(
                    "PUBLICATION_EVIDENCE_CONFLICT",
                    "published Candidate has another Evidence Manifest",
                )
            return existing
        self.publication_count += 1
        publication = CandidatePublication(
            repository=repository,
            candidate_sha=candidate_sha,
            evidence_manifest_digest=evidence_manifest_digest,
            source_ref=f"memory://publication/{candidate_sha}",
        )
        self._publications[(repository, candidate_sha)] = publication
        self._last_publication = publication
        return publication

    def read_hosted_checks(
        self,
        repository: str,
        candidate_sha: str,
        required_checks: tuple[dict[str, Any], ...] = (),
    ) -> HostedCheckReadback:
        publication = self.read_publication(repository, candidate_sha)
        if publication is None:
            raise DeliveryControlError(
                "HOSTED_CHECK_BEFORE_PUBLICATION",
                "hosted checks cannot run before exact Candidate publication",
            )
        self.hosted_read_candidates.append(candidate_sha)
        if self._hosted_outcomes:
            self._last_hosted_outcome = self._hosted_outcomes.pop(0)
        return HostedCheckReadback(
            candidate_sha=candidate_sha,
            status=self._last_hosted_outcome,
            source_ref=f"memory://hosted-checks/{candidate_sha}",
            definition_digests=tuple(
                sorted(str(check["definition_digest"]) for check in required_checks)
            ),
        )

    def retry_hosted_checks(
        self,
        repository: str,
        candidate_sha: str,
    ) -> None:
        if self.read_publication(repository, candidate_sha) is None:
            raise DeliveryControlError(
                "HOSTED_RETRY_BEFORE_PUBLICATION",
                "hosted retry has no published Candidate",
            )
        self.hosted_retry_count += 1

    def integrate_serially(
        self,
        repository: str,
        candidate_sha: str,
        target_branch: str,
    ) -> IntegrationReadback:
        if self.read_publication(repository, candidate_sha) is None:
            raise DeliveryControlError(
                "INTEGRATION_BEFORE_PUBLICATION",
                "Integration requires one published exact Candidate",
            )
        self.integrated_candidates.append(candidate_sha)
        return IntegrationReadback(
            target_branch=target_branch,
            candidate_sha=candidate_sha,
            source_ref=f"memory://target/{target_branch}/{candidate_sha}",
        )


class GitHubCliDeliveryControl:
    """Production exact-SHA publication and hosted-check readback via git/gh."""

    evidence_context = "gwo/evidence-manifest"

    def __init__(
        self,
        *,
        repository_path: Path,
        remote: str = "origin",
        executable: str = "gh",
    ):
        self.repository_path = Path(repository_path).resolve()
        self.remote = remote
        self.executable = executable
        self._retry_run_ids: dict[tuple[str, str], str] = {}

    def _command(self, command: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            command,
            cwd=self.repository_path,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )

    def _checked(self, command: list[str]) -> str:
        result = self._command(command)
        if result.returncode != 0:
            raise DeliveryControlError(
                "DELIVERY_COMMAND_FAILED",
                result.stderr.strip()
                or result.stdout.strip()
                or "delivery command failed",
            )
        return result.stdout.strip()

    @staticmethod
    def _wait_for_publication_readback() -> None:
        time.sleep(1)

    @staticmethod
    def _branch(candidate_sha: str) -> str:
        return f"gwo/candidates/{candidate_sha}"

    def _evidence_status(
        self,
        repository: str,
        candidate_sha: str,
    ) -> tuple[str, str] | None:
        rendered = self._checked(
            [
                self.executable,
                "api",
                f"repos/{repository}/commits/{candidate_sha}/status",
            ]
        )
        try:
            payload = json.loads(rendered)
        except json.JSONDecodeError as error:
            raise DeliveryControlError(
                "GITHUB_STATUS_INVALID",
                "GitHub status readback was not valid JSON",
            ) from error
        statuses = payload.get("statuses") if isinstance(payload, dict) else None
        if not isinstance(statuses, list):
            raise DeliveryControlError(
                "GITHUB_STATUS_INVALID",
                "GitHub status readback omitted statuses",
            )
        for status in statuses:
            if (
                isinstance(status, dict)
                and status.get("context") == self.evidence_context
            ):
                description = status.get("description")
                state = status.get("state")
                if (
                    isinstance(description, str)
                    and description
                    and isinstance(state, str)
                    and state
                ):
                    return description, state
        return None

    @staticmethod
    def _pull_request_identity(
        pull_request: dict[str, Any],
    ) -> tuple[str, str, str] | None:
        head = pull_request.get("head")
        base = pull_request.get("base")
        if not isinstance(head, dict) or not isinstance(base, dict):
            return None
        head_ref = head.get("ref")
        head_sha = head.get("sha")
        base_ref = base.get("ref")
        if not all(
            isinstance(value, str) and value
            for value in (head_ref, head_sha, base_ref)
        ):
            return None
        return head_ref, head_sha, base_ref

    def _read_candidate_pull_request(
        self,
        repository: str,
        candidate_sha: str,
        target_branch: str,
    ) -> str | None:
        branch = self._branch(candidate_sha)
        owner = repository.split("/", 1)[0]
        command = [
            self.executable,
            "api",
            "--method",
            "GET",
            f"repos/{repository}/pulls",
            "-f",
            "state=all",
            "-f",
            f"head={owner}:{branch}",
            "-f",
            "per_page=100",
        ]
        result = self._command(command)
        if result.returncode != 0:
            raise DeliveryControlError(
                "PULL_REQUEST_READBACK_AMBIGUOUS",
                result.stderr.strip()
                or result.stdout.strip()
                or "Candidate pull request readback failed",
            )
        rendered = result.stdout.strip()
        try:
            payload = json.loads(rendered)
        except json.JSONDecodeError as error:
            raise DeliveryControlError(
                "PULL_REQUEST_READBACK_INVALID",
                "GitHub pull request readback was not valid JSON",
            ) from error
        if not isinstance(payload, list):
            raise DeliveryControlError(
                "PULL_REQUEST_READBACK_INVALID",
                "GitHub pull request readback was not a list",
            )
        identified = [
            (item, self._pull_request_identity(item))
            for item in payload
            if isinstance(item, dict)
        ]
        conflicting = [
            item
            for item, identity in identified
            if identity is not None
            and identity[0] == branch
            and identity != (branch, candidate_sha, target_branch)
        ]
        if conflicting:
            raise DeliveryControlError(
                "PULL_REQUEST_IDENTITY_CONFLICT",
                "Candidate branch already has a pull request with another SHA or base",
            )
        exact = [
            item
            for item, identity in identified
            if identity == (branch, candidate_sha, target_branch)
        ]
        if len(exact) > 1:
            raise DeliveryControlError(
                "PULL_REQUEST_IDENTITY_CONFLICT",
                "Candidate branch has multiple pull requests for the same target",
            )
        if not exact:
            return None
        pull_request = exact[0]
        state = pull_request.get("state")
        merged_at = pull_request.get("merged_at")
        if state != "open" and not (
            state == "closed" and isinstance(merged_at, str) and merged_at
        ):
            raise DeliveryControlError(
                "PULL_REQUEST_CLOSED_UNMERGED",
                "Candidate pull request is closed without Integration",
            )
        source_ref = pull_request.get("html_url")
        if not isinstance(source_ref, str) or not source_ref:
            raise DeliveryControlError(
                "PULL_REQUEST_READBACK_INVALID",
                "Candidate pull request omitted its durable URL",
            )
        return source_ref

    def _ensure_candidate_pull_request(
        self,
        repository: str,
        candidate_sha: str,
        target_branch: str,
    ) -> str:
        existing = self._read_candidate_pull_request(
            repository,
            candidate_sha,
            target_branch,
        )
        if existing is not None:
            return existing
        branch = self._branch(candidate_sha)
        result = self._command(
            [
                self.executable,
                "api",
                "--method",
                "POST",
                f"repos/{repository}/pulls",
                "-f",
                f"title=GWO Candidate {candidate_sha[:12]}",
                "-f",
                f"head={branch}",
                "-f",
                f"base={target_branch}",
                "-f",
                (
                    "body=Automated exact-SHA Candidate publication by "
                    "GitHub Work Orchestrator V8."
                ),
            ]
        )
        try:
            readback = self._read_candidate_pull_request(
                repository,
                candidate_sha,
                target_branch,
            )
        except DeliveryControlError as error:
            if error.code != "PULL_REQUEST_READBACK_AMBIGUOUS":
                raise
            raise DeliveryControlError(
                "PULL_REQUEST_CREATE_AMBIGUOUS",
                result.stderr.strip()
                or result.stdout.strip()
                or error.detail,
            ) from error
        if readback is not None:
            return readback
        detail = (
            result.stderr.strip()
            or result.stdout.strip()
            or "pull request creation did not read back"
        )
        raise DeliveryControlError(
            "PULL_REQUEST_CREATE_AMBIGUOUS",
            detail,
        )

    def _read_publication_status(
        self,
        repository: str,
        candidate_sha: str,
    ) -> tuple[CandidatePublication | None, str | None, bool]:
        branch = self._branch(candidate_sha)
        result = self._command(
            [
                "git",
                "ls-remote",
                "--exit-code",
                self.remote,
                f"refs/heads/{branch}",
            ]
        )
        if result.returncode == 2:
            return None, None, False
        if result.returncode != 0:
            raise DeliveryControlError(
                "PUBLICATION_READBACK_FAILED",
                result.stderr.strip()
                or result.stdout.strip()
                or "remote Candidate readback failed",
            )
        remote_sha = result.stdout.strip().split(maxsplit=1)[0]
        if remote_sha != candidate_sha:
            raise DeliveryControlError(
                "PUBLICATION_IDENTITY_CONFLICT",
                "remote Candidate branch does not point at the exact SHA",
            )
        evidence = self._evidence_status(repository, candidate_sha)
        if evidence is None:
            return None, None, True
        return (
            CandidatePublication(
                repository=repository,
                candidate_sha=candidate_sha,
                evidence_manifest_digest=evidence[0],
                source_ref=(
                    f"https://github.com/{repository}/tree/"
                    f"{quote(branch, safe='/')}"
                ),
            ),
            evidence[1],
            True,
        )

    def read_publication(
        self,
        repository: str,
        candidate_sha: str,
    ) -> CandidatePublication | None:
        publication, _evidence_state, _branch_exists = (
            self._read_publication_status(
                repository,
                candidate_sha,
            )
        )
        return publication

    def publish_once(
        self,
        repository: str,
        candidate_sha: str,
        evidence_manifest_digest: str,
        *,
        target_branch: str | None = None,
    ) -> CandidatePublication:
        existing, evidence_state, branch_exists = self._read_publication_status(
            repository,
            candidate_sha,
        )
        if existing is not None:
            if existing.evidence_manifest_digest != evidence_manifest_digest:
                raise DeliveryControlError(
                    "PUBLICATION_EVIDENCE_CONFLICT",
                    "published Candidate has another Evidence Manifest",
                )
        elif not branch_exists:
            branch = self._branch(candidate_sha)
            self._checked(
                [
                    "git",
                    "push",
                    self.remote,
                    f"{candidate_sha}:refs/heads/{branch}",
                ]
            )
        if existing is None or evidence_state != "success":
            self._checked(
                [
                    self.executable,
                    "api",
                    "--method",
                    "POST",
                    f"repos/{repository}/statuses/{candidate_sha}",
                    "-f",
                    "state=success",
                    "-f",
                    f"context={self.evidence_context}",
                    "-f",
                    f"description={evidence_manifest_digest}",
                ]
            )
        source_ref = (
            None
            if target_branch is None
            else self._ensure_candidate_pull_request(
                repository,
                candidate_sha,
                target_branch,
            )
        )
        receipt = None
        for attempt in range(5):
            receipt, current_evidence_state, _branch_exists = (
                self._read_publication_status(
                    repository,
                    candidate_sha,
                )
            )
            if (
                receipt is not None
                and receipt.evidence_manifest_digest == evidence_manifest_digest
                and current_evidence_state == "success"
            ):
                break
            if attempt < 4:
                self._wait_for_publication_readback()
        if receipt is None:
            raise DeliveryControlError(
                "PUBLICATION_READBACK_FAILED",
                "Candidate publication was not visible after push",
            )
        if (
            receipt.evidence_manifest_digest != evidence_manifest_digest
            or current_evidence_state != "success"
        ):
            raise DeliveryControlError(
                "PUBLICATION_READBACK_FAILED",
                "Evidence Manifest status did not read back successful",
            )
        return replace(
            receipt,
            source_ref=source_ref or receipt.source_ref,
        )

    def read_hosted_checks(
        self,
        repository: str,
        candidate_sha: str,
        required_checks: tuple[dict[str, Any], ...] = (),
    ) -> HostedCheckReadback:
        rendered = self._checked(
            [
                self.executable,
                "run",
                "list",
                "--repo",
                repository,
                "--commit",
                candidate_sha,
                "--limit",
                "100",
                "--json",
                "databaseId,status,conclusion,url,headSha,name,workflowName",
            ]
        )
        try:
            payload = json.loads(rendered)
        except json.JSONDecodeError as error:
            raise DeliveryControlError(
                "HOSTED_CHECK_READBACK_INVALID",
                "GitHub Actions readback was not valid JSON",
            ) from error
        if not isinstance(payload, list):
            raise DeliveryControlError(
                "HOSTED_CHECK_READBACK_INVALID",
                "GitHub Actions readback was not a run list",
            )
        runs = [
            run
            for run in payload
            if isinstance(run, dict) and run.get("headSha") == candidate_sha
        ]
        source_ref = (
            f"https://github.com/{repository}/actions?query=branch%3A{candidate_sha}"
        )
        expected = {
            str(check["hosted_name"]): str(check["definition_digest"])
            for check in required_checks
        }
        if expected:

            def _is_simple_workflow_name(run_name: Any) -> bool:
                return isinstance(run_name, str) and " / " not in run_name

            observations = [
                run
                for run in runs
                if (
                    _is_simple_workflow_name(run.get("name"))
                    and run.get("name") in expected
                )
                or (
                    _is_simple_workflow_name(run.get("workflowName"))
                    and run.get("workflowName") in expected
                )
            ]
            matched_names = {
                str(run.get("name"))
                for run in observations
                if run.get("name") in expected
            } | {
                str(run.get("workflowName"))
                for run in observations
                if run.get("workflowName") in expected
            }
            combined_expected = {
                name
                for name in expected
                if " / " in name and name not in matched_names
            }
            for run in runs:
                workflow_name = str(
                    run.get("workflowName") or run.get("name") or ""
                )
                if not any(
                    name.startswith(f"{workflow_name} / ")
                    for name in combined_expected
                ):
                    continue
                run_id = run.get("databaseId")
                if not isinstance(run_id, int) or isinstance(run_id, bool):
                    raise DeliveryControlError(
                        "HOSTED_CHECK_READBACK_INVALID",
                        "GitHub Actions run omitted its database identity",
                    )
                jobs_rendered = self._checked(
                    [
                        self.executable,
                        "api",
                        "--method",
                        "GET",
                        f"repos/{repository}/actions/runs/{run_id}/jobs",
                        "-f",
                        "per_page=100",
                    ]
                )
                try:
                    jobs_payload = json.loads(jobs_rendered)
                except json.JSONDecodeError as error:
                    raise DeliveryControlError(
                        "HOSTED_CHECK_READBACK_INVALID",
                        "GitHub Actions job readback was not valid JSON",
                    ) from error
                jobs = (
                    jobs_payload.get("jobs")
                    if isinstance(jobs_payload, dict)
                    else None
                )
                if not isinstance(jobs, list):
                    raise DeliveryControlError(
                        "HOSTED_CHECK_READBACK_INVALID",
                        "GitHub Actions job readback omitted jobs",
                    )
                for job in jobs:
                    if (
                        not isinstance(job, dict)
                        or job.get("head_sha") != candidate_sha
                    ):
                        continue
                    combined_name = f"{workflow_name} / {job.get('name')}"
                    if combined_name not in combined_expected:
                        continue
                    observations.append(
                        {
                            "databaseId": run_id,
                            "status": job.get("status"),
                            "conclusion": job.get("conclusion"),
                            "url": job.get("html_url") or run.get("url"),
                            "headSha": job.get("head_sha"),
                            "name": combined_name,
                            "workflowName": combined_name,
                        }
                    )
                    matched_names.add(combined_name)
            if matched_names != set(expected):
                return HostedCheckReadback(
                    candidate_sha,
                    "pending",
                    source_ref,
                    tuple(sorted(expected.values())),
                )
            runs = observations
        definition_digests = tuple(sorted(expected.values()))
        if not runs or any(run.get("status") != "completed" for run in runs):
            return HostedCheckReadback(
                candidate_sha,
                "pending",
                source_ref,
                definition_digests,
            )
        conclusions = {str(run.get("conclusion") or "") for run in runs}
        infrastructure = {
            "stale",
            "startup_failure",
        }
        non_candidate_verdicts = infrastructure | {"cancelled", "success"}
        candidate_failures = conclusions - non_candidate_verdicts
        if candidate_failures:
            status = "code_failure"
        elif "cancelled" in conclusions:
            status = "cancelled"
        elif conclusions & infrastructure:
            run = next(
                run
                for run in runs
                if str(run.get("conclusion") or "") in infrastructure
            )
            self._retry_run_ids[(repository, candidate_sha)] = str(
                run.get("databaseId")
            )
            status = "infrastructure_failure"
        elif conclusions == {"success"}:
            status = "passed"
        else:
            status = "code_failure"
        urls = [str(run.get("url")) for run in runs if run.get("url")]
        if urls:
            source_ref = urls[0]
        return HostedCheckReadback(
            candidate_sha,
            status,
            source_ref,
            definition_digests,
        )

    def retry_hosted_checks(
        self,
        repository: str,
        candidate_sha: str,
    ) -> None:
        run_id = self._retry_run_ids.get((repository, candidate_sha))
        if run_id is None:
            raise DeliveryControlError(
                "HOSTED_RETRY_RUN_MISSING",
                "no exact-SHA infrastructure failure is available to retry",
            )
        self._checked(
            [
                self.executable,
                "run",
                "rerun",
                run_id,
                "--failed",
                "--repo",
                repository,
            ]
        )

    def integrate_serially(
        self,
        repository: str,
        candidate_sha: str,
        target_branch: str,
    ) -> IntegrationReadback:
        target_ref = f"refs/heads/{target_branch}"
        target = self._checked(["git", "ls-remote", self.remote, target_ref])
        if not target:
            raise DeliveryControlError(
                "INTEGRATION_TARGET_MISSING",
                f"remote target branch is missing: {target_branch}",
            )
        target_sha = target.split(maxsplit=1)[0]
        self._checked(
            [
                "git",
                "fetch",
                "--no-tags",
                self.remote,
                target_ref,
            ]
        )
        ancestry = self._command(
            [
                "git",
                "merge-base",
                "--is-ancestor",
                target_sha,
                candidate_sha,
            ]
        )
        if ancestry.returncode != 0:
            raise DeliveryControlError(
                "INTEGRATION_NOT_FAST_FORWARD",
                "Candidate is not a fast-forward of the remote target branch",
            )
        self._checked(
            [
                "git",
                "push",
                self.remote,
                f"{candidate_sha}:{target_ref}",
            ]
        )
        readback = self._checked(["git", "ls-remote", self.remote, target_ref])
        integrated_sha = readback.split(maxsplit=1)[0] if readback else ""
        if integrated_sha != candidate_sha:
            raise DeliveryControlError(
                "INTEGRATION_READBACK_FAILED",
                "remote target branch did not reach the exact Candidate",
            )
        return IntegrationReadback(
            target_branch=target_branch,
            candidate_sha=candidate_sha,
            source_ref=(
                f"https://github.com/{repository}/tree/{quote(target_branch, safe='/')}"
            ),
        )


@dataclass(frozen=True)
class RecoveryDirective:
    action: str
    consumes_semantic_attempt: bool
    plan_node_failed: bool = False


class RecoveryLadder:
    """Deterministic semantic recovery policy; tool/test loops are not steps."""

    def __init__(self, *, semantic_attempts: int, repair_rounds: int):
        if (
            not isinstance(semantic_attempts, int)
            or isinstance(semantic_attempts, bool)
            or not 1 <= semantic_attempts <= 2
            or not isinstance(repair_rounds, int)
            or isinstance(repair_rounds, bool)
            or not 0 <= repair_rounds <= 1
        ):
            raise KernelError(
                "RECOVERY_POLICY_INVALID",
                "V8 recovery allows at most two Attempts and one Repair Round",
            )
        self.semantic_attempts = semantic_attempts
        self.repair_rounds = repair_rounds

    def decide(
        self,
        *,
        terminal_reason: str,
        attempt_ordinal: int,
        repair_rounds_used: int,
    ) -> RecoveryDirective:
        if terminal_reason == "runtime_lost":
            return RecoveryDirective(
                action="block_runtime_unavailable",
                consumes_semantic_attempt=False,
            )
        if terminal_reason == "superseded":
            return RecoveryDirective(
                action="record_superseded",
                consumes_semantic_attempt=False,
            )
        if terminal_reason not in {"rejected", "no_result"}:
            raise KernelError(
                "ATTEMPT_TERMINAL_REASON_INVALID",
                f"unknown Attempt terminal reason: {terminal_reason}",
            )
        if not 1 <= attempt_ordinal <= self.semantic_attempts:
            raise KernelError(
                "ATTEMPT_ORDINAL_INVALID",
                "Attempt ordinal is outside the compiled Recovery Ladder",
            )
        if repair_rounds_used < self.repair_rounds:
            return RecoveryDirective(
                action="repair_same_attempt",
                consumes_semantic_attempt=False,
            )
        if attempt_ordinal < self.semantic_attempts:
            return RecoveryDirective(
                action="start_frontier_attempt",
                consumes_semantic_attempt=True,
            )
        return RecoveryDirective(
            action="fail_plan_node",
            consumes_semantic_attempt=True,
            plan_node_failed=True,
        )

    @staticmethod
    def recovery_packet(
        *,
        candidate_sha: str,
        acceptance_digest: str,
        changed_files: list[str],
        causes: list[dict[str, Any]],
    ) -> str:
        def bounded(values: list[str], *, each: int, total: int) -> list[str]:
            result: list[str] = []
            used = 0
            for value in values:
                text = str(value)[:each]
                size = len(text.encode("utf-8"))
                if used + size > total:
                    remaining = max(0, total - used)
                    text = text.encode("utf-8")[:remaining].decode(
                        "utf-8",
                        errors="ignore",
                    )
                    if text:
                        result.append(text)
                    break
                result.append(text)
                used += size
            return result

        bounded_causes: list[dict[str, Any]] = []
        cause_bytes = 0
        for cause in causes:
            cause_type = str(cause.get("type") or "candidate_rejection")[:64]
            if cause_type == "review_blocker":
                finding = cause.get("finding")
                if (
                    not isinstance(finding, dict)
                    or set(finding)
                    != {"severity", "code", "source", "location", "message"}
                ):
                    raise KernelError(
                        "REPAIR_CAUSE_INVALID",
                        "Review repair cause does not contain one exact typed finding",
                    )
                normalized = {
                    "type": cause_type,
                    "axis": str(cause.get("axis") or "")[:128],
                    "finding": dict(finding),
                }
            else:
                normalized = {"type": cause_type}
                for key, value in sorted(cause.items()):
                    if key == "type":
                        continue
                    if isinstance(value, str):
                        normalized[str(key)] = value[:2_048]
                    elif isinstance(value, int) and not isinstance(value, bool):
                        normalized[str(key)] = value
                    elif isinstance(value, list):
                        normalized[str(key)] = bounded(
                            [str(item) for item in value],
                            each=1_024,
                            total=2_048,
                        )
            encoded = canonical_bytes(normalized)
            if (
                cause_type != "review_blocker"
                and cause_bytes + len(encoded) > 10_240
            ):
                break
            bounded_causes.append(normalized)
            cause_bytes += len(encoded)

        exact_changed_files = list(changed_files)
        if (
            any(
                not isinstance(path, str)
                or len(path) > REPAIR_CHANGED_FILE_MAX_CHARACTERS
                for path in exact_changed_files
            )
            or len(canonical_bytes(exact_changed_files))
            > REPAIR_CHANGED_FILES_MAX_BYTES
        ):
            raise KernelError(
                "REPAIR_CHANGED_FILES_TOO_LARGE",
                (
                    "exact changed-file metadata exceeds its bounded "
                    "Repair Packet allocation"
                ),
            )
        packet = {
            "schema_version": 2,
            "candidate_sha": candidate_sha,
            "acceptance_digest": acceptance_digest,
            "changed_files": exact_changed_files,
            "causes": bounded_causes,
        }
        rendered = canonical_bytes(packet).decode("utf-8")
        if len(rendered.encode("utf-8")) > REPAIR_PACKET_MAX_BYTES:
            raise KernelError(
                "RECOVERY_PACKET_TOO_LARGE",
                "Repair Packet exceeds the 64 KiB UTF-8 envelope bound",
            )
        return rendered


@dataclass(frozen=True)
class ReconcileOutcome:
    status: str
    directive: str
    repository: str
    plan_digest: str
    goal_key: str
    goal_state: str
    work_item_key: str
    work_item_state: str
    node_key: str
    admission_id: str
    admission_state: str
    attempt_id: str | None
    attempt_state: str | None
    candidate_sha: str | None
    result_digest: str | None
    materialization_executions: int
    wait_condition: str | None
    attempt_ordinal: int = 1
    repair_rounds_used: int = 0
    attempt_terminal_reason: str | None = None
    runtime_circuit: str | None = None
    wait_source_ref: str | None = None
    wait_event_identity: str | None = None
    next_check_at: str | None = None
    publication_eligible: bool | None = None
    publication_state: str | None = None
    publication_ref: str | None = None
    hosted_check_state: str | None = None
    hosted_retry_count: int = 0
    integration_batch_id: str | None = None
    integration_batch_sha: str | None = None
    admitted_node_keys: tuple[str, ...] = field(default=(), compare=False)
    active_worker_turns: int = 0
    worker_turn_capacity: int = 1
    coordinator_turn_capacity: int = 1
    node_outcomes: tuple[ReconcileOutcome, ...] = ()
    completed_work_item_keys: tuple[str, ...] = ()


@dataclass(frozen=True)
class KernelPreviewAction:
    kind: str
    node_key: str | None
    source_ref: str


@dataclass(frozen=True)
class KernelReconciliationPlan:
    plan_digest: str
    actions: tuple[KernelPreviewAction, ...]
    admissible_node_keys: tuple[str, ...]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _runtime_error_record(error: RuntimeAdapterError) -> dict[str, str]:
    return {
        "code": error.code,
        "failure_class": error.failure_class,
        "detail": error.detail[:1_024],
    }


def _git(repository: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repository), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if result.returncode != 0:
        raise KernelError(
            "GIT_OPERATION_FAILED",
            result.stderr.strip() or result.stdout.strip() or "git failed",
        )
    return result.stdout.strip()


class Kernel:
    """Own one public convergence pass; no external call holds a Store transaction."""

    def __init__(
        self,
        *,
        store_path: Path,
        publication: LocalPlanPublication,
        runtime: RuntimeAdapter,
        verifier: EvidenceVerifier,
        repository_path: Path,
        integration_branch: str,
        writer_generation: str,
        runtime_profile: RuntimeProfile | None = None,
        frontier_runtime_profile: RuntimeProfile | None = None,
        runtime_config: dict[str, Any] | None = None,
        delivery_control: DeliveryControl | None = None,
        parent_agent_id: str | None = None,
        skill_catalog: SkillCatalog | None = None,
    ):
        self.store_path = Path(store_path)
        self.publication = publication
        self.runtime = runtime
        self.verifier = verifier
        if (runtime_profile is not None or frontier_runtime_profile is not None) and (
            runtime_config is not None
        ):
            raise KernelError(
                "RUNTIME_PROFILE_INJECTION_CONFLICT",
                "runtime_profile injection is only allowed when runtime_config is None",
            )
        self.repository_path = Path(repository_path).resolve()
        self.integration_branch = integration_branch
        self.writer_generation = writer_generation
        self.runtime_profile = runtime_profile
        self.frontier_runtime_profile = frontier_runtime_profile
        self.runtime_config = runtime_config
        self.delivery_control = delivery_control
        self.parent_agent_id = parent_agent_id
        self.skill_catalog = skill_catalog
        self._review_convergence = ReviewConvergence(
            runtime=runtime,
            verifier=verifier,
            runtime_config=runtime_config,
            assert_writer=self._assert_state_writer,
            persist_state=self._persist_state_snapshot,
        )
        with self._connect() as connection:
            self.ensure_store_schema(connection)

    def _assert_state_writer(self, state: dict[str, Any]) -> None:
        self.publication.assert_writer(
            repository=state["repository"],
            writer_generation=self.writer_generation,
            plan_digest=state["plan_digest"],
            activation_id=state["activation_id"],
        )

    def _persist_state_snapshot(self, state: dict[str, Any]) -> None:
        self._write_state(state["repository"], state["plan_digest"], state)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.store_path)
        connection.row_factory = sqlite3.Row
        return connection

    @staticmethod
    def _profile_to_dict(profile: RuntimeProfile) -> dict[str, Any]:
        return {
            "name": profile.name,
            "provider": profile.provider,
            "model": profile.model,
            "thinking": profile.thinking,
            "mode": profile.mode,
            "features": dict(profile.features),
        }

    _RUNTIME_PROFILE_KEYS = {
        "name",
        "provider",
        "model",
        "thinking",
        "mode",
        "features",
    }

    @staticmethod
    def _profile_from_dict(data: dict[str, Any]) -> RuntimeProfile:
        """Strictly parse a frozen RuntimeProfile snapshot from durable state.

        Missing, extra, null, or malformed fields fail closed so restart/config
        drift cannot silently alter an Admission's profile.
        """

        if set(data.keys()) != Kernel._RUNTIME_PROFILE_KEYS:
            raise KernelError(
                "RUNTIME_PROFILE_FROZEN_INVALID",
                f"frozen runtime profile has invalid keys: {sorted(data.keys())!r}",
            )

        def _field(key: str) -> str:
            value = data.get(key)
            if not isinstance(value, str):
                raise KernelError(
                    "RUNTIME_PROFILE_FROZEN_INVALID",
                    f"frozen runtime profile has invalid {key}: {value!r}",
                )
            stripped = value.strip()
            if not stripped:
                raise KernelError(
                    "RUNTIME_PROFILE_FROZEN_INVALID",
                    f"frozen runtime profile has invalid {key}: {value!r}",
                )
            return stripped

        name = _field("name")
        provider = _field("provider")
        model = _field("model")
        thinking = _field("thinking")
        mode = _field("mode")
        features = data.get("features")
        if not isinstance(features, dict):
            raise KernelError(
                "RUNTIME_PROFILE_FROZEN_INVALID",
                f"frozen runtime profile has invalid features: {features!r}",
            )
        return RuntimeProfile(
            name=name,
            provider=provider,
            model=model,
            thinking=thinking,
            mode=mode,
            features=dict(features),
        )

    def _resolve_worker_profile(
        self,
        *,
        repository: str,
        difficulty: str,
    ) -> RuntimeProfile:
        """Resolve and normalize the Worker profile for the configured Adapter."""

        if self.runtime_config is not None:
            profile = resolve_worker_profile(
                self.runtime_config,
                repository=repository,
                difficulty=difficulty,
            )
        elif self.runtime_profile is not None:
            profile = self.runtime_profile
        else:
            raise KernelError(
                "RUNTIME_PROFILE_MISSING",
                "Kernel has no runtime_config or runtime_profile to resolve a Worker profile",
            )
        normalize = getattr(self.runtime, "normalize_profile", None)
        if not callable(normalize):
            raise KernelError(
                "RUNTIME_ADAPTER_INVALID",
                f"Runtime adapter {self.runtime.adapter_name} does not advertise normalize_profile",
            )
        try:
            return normalize(profile)
        except RuntimeAdapterError as error:
            raise KernelError(
                error.code,
                error.detail,
            ) from error

    def _resolve_frontier_profile(self, *, repository: str) -> RuntimeProfile:
        """Resolve and normalize the frontier/recovery profile."""

        if self.frontier_runtime_profile is not None:
            profile = self.frontier_runtime_profile
        elif self.runtime_config is not None:
            profile = resolve_worker_profile(
                self.runtime_config,
                repository=repository,
                difficulty="frontier",
            )
        else:
            raise KernelError(
                "RUNTIME_PROFILE_MISSING",
                "Kernel has no runtime_config or frontier_runtime_profile to resolve a frontier profile",
            )
        normalize = getattr(self.runtime, "normalize_profile", None)
        if not callable(normalize):
            raise KernelError(
                "RUNTIME_ADAPTER_INVALID",
                f"Runtime adapter {self.runtime.adapter_name} does not advertise normalize_profile",
            )
        try:
            return normalize(profile)
        except RuntimeAdapterError as error:
            raise KernelError(
                error.code,
                error.detail,
            ) from error

    @staticmethod
    def _profile_from_frozen_state(
        state: dict[str, Any],
        profile_key: str,
        digest_key: str,
    ) -> RuntimeProfile:
        """Parse a frozen profile snapshot and enforce digest parity."""

        frozen = state.get(profile_key)
        if not isinstance(frozen, dict):
            raise KernelError(
                "RUNTIME_PROFILE_FROZEN_INVALID",
                f"{profile_key} is missing or not a snapshot",
            )
        profile = Kernel._profile_from_dict(frozen)
        expected_digest = state.get(digest_key)
        if not isinstance(expected_digest, str) or not expected_digest:
            raise KernelError(
                "RUNTIME_PROFILE_FROZEN_INVALID",
                f"{digest_key} is missing or invalid",
            )
        if expected_digest != profile.digest:
            raise KernelError(
                "RUNTIME_PROFILE_FROZEN_INVALID",
                f"{digest_key} does not match the frozen profile",
            )
        return profile

    def _freeze_legacy_frontier_profile(
        self,
        state: dict[str, Any],
    ) -> RuntimeProfile:
        """Return the frozen frontier profile, resolving once for legacy state.

        - Key absent: resolve+normalize current configured/injected frontier,
          persist snapshot+digest in ``state``, and return it.
        - Key dict: return the frozen RuntimeProfile without re-resolving.
        - Key present but null/malformed: fail closed.
        """

        repository = state["repository"]
        if "frontier_runtime_profile" not in state:
            profile = self._resolve_frontier_profile(repository=repository)
            state["frontier_runtime_profile"] = self._profile_to_dict(profile)
            state["frontier_profile_digest"] = profile.digest
            self._write_state(
                repository,
                state["plan_digest"],
                state,
            )
            return profile
        return self._profile_from_frozen_state(
            state,
            profile_key="frontier_runtime_profile",
            digest_key="frontier_profile_digest",
        )

    @staticmethod
    def ensure_store_schema(connection: sqlite3.Connection) -> None:
        """Create the one native Store schema used by live and reconstructed Kernel."""
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS v8_execution_state (
                repository TEXT NOT NULL,
                plan_digest TEXT NOT NULL,
                state_json TEXT NOT NULL,
                PRIMARY KEY (repository, plan_digest)
            );
            CREATE TABLE IF NOT EXISTS v8_node_execution_state (
                repository TEXT NOT NULL,
                plan_digest TEXT NOT NULL,
                node_key TEXT NOT NULL,
                state_json TEXT NOT NULL,
                PRIMARY KEY (repository, plan_digest, node_key)
            );
            CREATE TABLE IF NOT EXISTS v8_integration_leases (
                repository TEXT PRIMARY KEY,
                holder TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS v8_admissions (
                admission_id TEXT PRIMARY KEY,
                repository TEXT NOT NULL,
                plan_digest TEXT NOT NULL,
                node_key TEXT NOT NULL,
                goal_key TEXT NOT NULL,
                state TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS v8_attempts (
                attempt_id TEXT PRIMARY KEY,
                repository TEXT NOT NULL,
                plan_digest TEXT NOT NULL,
                node_key TEXT NOT NULL,
                admission_id TEXT NOT NULL,
                state TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS v8_resource_claims (
                repository TEXT NOT NULL,
                resource_key TEXT NOT NULL,
                admission_id TEXT,
                attempt_id TEXT,
                PRIMARY KEY (repository, resource_key)
            );
            CREATE TABLE IF NOT EXISTS v8_goal_holds (
                repository TEXT NOT NULL,
                goal_key TEXT NOT NULL,
                reason TEXT NOT NULL,
                PRIMARY KEY (repository, goal_key)
            );
            CREATE TABLE IF NOT EXISTS v8_node_states (
                repository TEXT NOT NULL,
                plan_digest TEXT NOT NULL,
                node_key TEXT NOT NULL,
                state TEXT NOT NULL,
                PRIMARY KEY (repository, plan_digest, node_key)
            );
            CREATE TABLE IF NOT EXISTS v8_verified_results (
                repository TEXT NOT NULL,
                plan_digest TEXT NOT NULL,
                node_key TEXT NOT NULL,
                contract_digest TEXT NOT NULL,
                candidate_sha TEXT NOT NULL,
                result_digest TEXT NOT NULL,
                base_sha TEXT NOT NULL,
                evidence_manifest_digest TEXT,
                evidence_json TEXT,
                superseded INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (
                    repository,
                    plan_digest,
                    node_key,
                    candidate_sha
                )
            );
            CREATE TABLE IF NOT EXISTS v8_integration_batches (
                repository TEXT NOT NULL,
                plan_digest TEXT NOT NULL,
                batch_id TEXT NOT NULL,
                state_json TEXT NOT NULL,
                PRIMARY KEY (repository, plan_digest, batch_id)
            );
            """
        )
        result_columns = {
            str(row["name"])
            for row in connection.execute(
                "PRAGMA table_info(v8_verified_results)"
            ).fetchall()
        }
        if "evidence_manifest_digest" not in result_columns:
            connection.execute(
                """
                ALTER TABLE v8_verified_results
                ADD COLUMN evidence_manifest_digest TEXT
                """
            )
        if "evidence_json" not in result_columns:
            connection.execute(
                """
                ALTER TABLE v8_verified_results
                ADD COLUMN evidence_json TEXT
                """
            )

    @classmethod
    def drain_store_ownership(
        cls,
        store_path: Path,
        *,
        repository: str,
        source_ref: str,
    ) -> None:
        """Supersede active native execution through one Kernel-owned transaction."""
        if not source_ref:
            raise KernelError(
                "DRAIN_SOURCE_MISSING",
                "writer drain requires a durable transition source",
            )
        connection = sqlite3.connect(Path(store_path))
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("BEGIN IMMEDIATE")
            cls.ensure_store_schema(connection)
            rows = connection.execute(
                """
                SELECT plan_digest, node_key, state_json
                FROM v8_node_execution_state
                WHERE repository = ?
                """,
                (repository,),
            ).fetchall()
            for row in rows:
                state = json.loads(row["state_json"])
                if state.get("status") in {
                    "complete",
                    "failed",
                    "superseded",
                }:
                    continue
                state.update(
                    {
                        "status": "superseded",
                        "directive": "invoke_coordinator",
                        "supersession_source_ref": source_ref,
                        "wait_condition": None,
                        "wait_source_ref": None,
                        "wait_event_identity": None,
                        "next_check_at": None,
                    }
                )
                if state.get("attempt_id"):
                    state["attempt_state"] = "superseded"
                    state["attempt_terminal_reason"] = "superseded"
                elif state.get("admission_id"):
                    state["admission_state"] = "abandoned"
                cls._upsert_state(
                    connection,
                    repository=repository,
                    plan_digest=str(row["plan_digest"]),
                    rendered=cls._render_state(state),
                )
                connection.execute(
                    """
                    UPDATE v8_node_states SET state = 'superseded'
                    WHERE repository = ? AND plan_digest = ? AND node_key = ?
                    """,
                    (
                        repository,
                        row["plan_digest"],
                        row["node_key"],
                    ),
                )
                connection.execute(
                    """
                    UPDATE v8_verified_results SET superseded = 1
                    WHERE repository = ? AND plan_digest = ? AND node_key = ?
                    """,
                    (
                        repository,
                        row["plan_digest"],
                        row["node_key"],
                    ),
                )
            connection.execute(
                """
                UPDATE v8_attempts SET state = 'terminal'
                WHERE repository = ?
                  AND state NOT IN ('verified', 'terminal')
                """,
                (repository,),
            )
            connection.execute(
                """
                UPDATE v8_admissions SET state = 'abandoned'
                WHERE repository = ?
                  AND state NOT IN ('consumed', 'abandoned')
                """,
                (repository,),
            )
            connection.execute(
                "DELETE FROM v8_resource_claims WHERE repository = ?",
                (repository,),
            )
            connection.commit()
        finally:
            connection.close()

    def _read_states(
        self,
        repository: str,
        plan_digest: str,
    ) -> tuple[dict[str, Any], ...]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT state_json
                FROM v8_node_execution_state
                WHERE repository = ? AND plan_digest = ?
                ORDER BY node_key
                """,
                (repository, plan_digest),
            ).fetchall()
            if not rows:
                legacy = connection.execute(
                    """
                    SELECT state_json
                    FROM v8_execution_state
                    WHERE repository = ? AND plan_digest = ?
                    """,
                    (repository, plan_digest),
                ).fetchone()
                rows = [] if legacy is None else [legacy]
        return tuple(json.loads(row["state_json"]) for row in rows)

    def _read_state(
        self,
        repository: str,
        plan_digest: str,
        node_key: str | None = None,
    ) -> dict[str, Any] | None:
        if node_key is None:
            states = self._read_states(repository, plan_digest)
            return None if not states else states[0]
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT state_json
                FROM v8_node_execution_state
                WHERE repository = ? AND plan_digest = ? AND node_key = ?
                """,
                (repository, plan_digest, node_key),
            ).fetchone()
            if row is None:
                legacy = connection.execute(
                    """
                    SELECT state_json
                    FROM v8_execution_state
                    WHERE repository = ? AND plan_digest = ?
                    """,
                    (repository, plan_digest),
                ).fetchone()
                if legacy is not None:
                    value = json.loads(legacy["state_json"])
                    if value.get("node_key") == node_key:
                        return value
        return None if row is None else json.loads(row["state_json"])

    def _write_state(
        self, repository: str, plan_digest: str, state: dict[str, Any]
    ) -> None:
        rendered = self._render_state(state)
        with self._connect() as connection:
            self._upsert_state(
                connection,
                repository=repository,
                plan_digest=plan_digest,
                rendered=rendered,
            )

    def _read_integration_batch(
        self,
        repository: str,
        plan_digest: str,
        batch_id: str,
    ) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT state_json
                FROM v8_integration_batches
                WHERE repository = ? AND plan_digest = ? AND batch_id = ?
                """,
                (repository, plan_digest, batch_id),
            ).fetchone()
        return None if row is None else json.loads(row["state_json"])

    def _write_integration_batch(
        self,
        repository: str,
        plan_digest: str,
        batch_id: str,
        state: dict[str, Any],
    ) -> None:
        rendered = self._render_state(state)
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO v8_integration_batches (
                    repository, plan_digest, batch_id, state_json
                ) VALUES (?, ?, ?, ?)
                ON CONFLICT(repository, plan_digest, batch_id) DO UPDATE SET
                    state_json = excluded.state_json
                """,
                (repository, plan_digest, batch_id, rendered),
            )

    def record_human_decision(
        self,
        *,
        repository: str,
        candidate_sha: str,
        approved: bool,
        source_ref: str,
    ) -> None:
        active = self.publication.read_active(repository)
        if active is None or active.writer_generation != self.writer_generation:
            raise KernelError(
                "DECISION_WRITER_FENCED",
                "human decision requires the active writer generation",
            )
        matches = [
            state
            for state in self._read_states(repository, active.plan_digest)
            if state.get("candidate_sha") == candidate_sha
            and state.get("wait_condition") == "human_decision"
        ]
        if len(matches) != 1 or not isinstance(approved, bool) or not source_ref:
            raise KernelError(
                "HUMAN_DECISION_INVALID",
                "decision does not bind the waiting exact Candidate",
            )
        state = matches[0]
        state["human_decision"] = {
            "candidate_sha": candidate_sha,
            "approved": approved,
            "source_ref": source_ref,
        }
        state["status"] = "running"
        state["directive"] = "run_again"
        state["wait_condition"] = None
        state["wait_source_ref"] = None
        state["wait_event_identity"] = None
        self._write_state(repository, active.plan_digest, state)

    def place_replan_hold(
        self,
        *,
        repository: str,
        goal_key: str,
        reason: str,
    ) -> None:
        active = self.publication.read_active(repository)
        if (
            active is None
            or active.writer_generation != self.writer_generation
            or not goal_key
            or not reason
        ):
            raise KernelError(
                "REPLAN_HOLD_INVALID",
                "Replan Hold requires the active writer, Goal, and reason",
            )
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO v8_goal_holds (repository, goal_key, reason)
                VALUES (?, ?, ?)
                ON CONFLICT(repository, goal_key) DO UPDATE SET
                    reason = excluded.reason
                """,
                (repository, goal_key, reason),
            )

    def clear_replan_hold(
        self,
        *,
        repository: str,
        goal_key: str,
    ) -> None:
        active = self.publication.read_active(repository)
        if active is None or active.writer_generation != self.writer_generation:
            raise KernelError(
                "REPLAN_HOLD_WRITER_FENCED",
                "only the active writer may clear a Replan Hold",
            )
        with self._connect() as connection:
            connection.execute(
                """
                DELETE FROM v8_goal_holds
                WHERE repository = ? AND goal_key = ?
                """,
                (repository, goal_key),
            )

    def supersede_attempt(
        self,
        *,
        repository: str,
        plan_digest: str,
        attempt_id: str,
        source_ref: str,
    ) -> None:
        active = self.publication.read_active(repository)
        if (
            active is None
            or active.writer_generation != self.writer_generation
            or not source_ref
        ):
            raise KernelError(
                "SUPERSESSION_WRITER_FENCED",
                "explicit supersession requires the active writer and source",
            )
        states = [
            state
            for state in self._read_states(repository, plan_digest)
            if state.get("attempt_id") == attempt_id
        ]
        if (
            len(states) != 1
            or states[0].get("attempt_id") != attempt_id
            or states[0].get("attempt_state") in {"terminal", "verified", "superseded"}
        ):
            raise KernelError(
                "SUPERSESSION_INVALID",
                "supersession does not identify one non-terminal Attempt",
            )
        state = states[0]
        prompt = self._prompt_from_state(state)
        admission = RuntimeAdmission(
            repository=repository,
            plan_digest=plan_digest,
            node_key=str(state["node_key"]),
            admission_id=str(state["admission_id"]),
            repository_path=self.repository_path,
            base_sha=str(state["base_sha"]),
        )
        binding = self.runtime.read_binding(admission, prompt)
        if binding is None:
            raise KernelError(
                "SUPERSESSION_RUNTIME_UNREADABLE",
                "Runtime must be read back before supersession releases claims",
            )
        self.runtime.retire(binding)
        state.update(
            {
                "status": "superseded",
                "directive": "invoke_coordinator",
                "attempt_state": "superseded",
                "attempt_terminal_reason": "superseded",
                "supersession_source_ref": source_ref,
                "wait_condition": None,
                "wait_source_ref": None,
                "wait_event_identity": None,
                "next_check_at": None,
            }
        )
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                UPDATE v8_attempts SET state = 'terminal'
                WHERE attempt_id = ?
                """,
                (attempt_id,),
            )
            connection.execute(
                """
                DELETE FROM v8_resource_claims WHERE attempt_id = ?
                """,
                (attempt_id,),
            )
            connection.execute(
                """
                UPDATE v8_verified_results SET superseded = 1
                WHERE repository = ? AND plan_digest = ?
                  AND node_key = ?
                """,
                (repository, plan_digest, state["node_key"]),
            )
            self._upsert_state(
                connection,
                repository=repository,
                plan_digest=plan_digest,
                rendered=self._render_state(state),
            )

    def supersede_verified_result(
        self,
        *,
        repository: str,
        plan_digest: str,
        node_key: str,
        candidate_sha: str,
        source_ref: str,
    ) -> None:
        active = self.publication.read_active(repository)
        if (
            active is None
            or active.writer_generation != self.writer_generation
            or not source_ref
        ):
            raise KernelError(
                "RESULT_SUPERSESSION_WRITER_FENCED",
                "Result supersession requires the active writer and source",
            )
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                """
                UPDATE v8_verified_results SET superseded = 1
                WHERE repository = ? AND plan_digest = ? AND node_key = ?
                  AND candidate_sha = ? AND superseded = 0
                """,
                (repository, plan_digest, node_key, candidate_sha),
            )
            if cursor.rowcount != 1:
                raise KernelError(
                    "RESULT_SUPERSESSION_INVALID",
                    "verified Result identity was missing or already superseded",
                )
            state_row = connection.execute(
                """
                SELECT state_json FROM v8_node_execution_state
                WHERE repository = ? AND plan_digest = ? AND node_key = ?
                """,
                (repository, plan_digest, node_key),
            ).fetchone()
            if state_row is not None:
                state = json.loads(state_row["state_json"])
                state["result_supersession"] = {
                    "candidate_sha": candidate_sha,
                    "source_ref": source_ref,
                }
                self._upsert_state(
                    connection,
                    repository=repository,
                    plan_digest=plan_digest,
                    rendered=self._render_state(state),
                )

    @staticmethod
    def _render_state(state: dict[str, Any]) -> str:
        return json.dumps(
            state,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )

    @staticmethod
    def _upsert_state(
        connection: sqlite3.Connection,
        *,
        repository: str,
        plan_digest: str,
        rendered: str,
    ) -> None:
        state = json.loads(rendered)
        node_key = state.get("node_key")
        if not isinstance(node_key, str) or not node_key:
            raise KernelError(
                "EXECUTION_STATE_IDENTITY_MISSING",
                "node execution state has no Node Key",
            )
        connection.execute(
            """
            INSERT INTO v8_node_execution_state (
                repository,
                plan_digest,
                node_key,
                state_json
            ) VALUES (?, ?, ?, ?)
            ON CONFLICT(repository, plan_digest, node_key) DO UPDATE SET
                state_json = excluded.state_json
            """,
            (repository, plan_digest, node_key, rendered),
        )

    def _commit_admission(
        self,
        state: dict[str, Any],
        work_node: dict[str, Any],
        *,
        activation_id: str,
        dependency_keys: tuple[str, ...] = (),
        worker_turn_capacity: int = 1,
    ) -> None:
        self.publication.assert_new_work(
            state["repository"],
            writer_generation=self.writer_generation,
            activation_id=activation_id,
        )
        claims = work_node.get("resource_claims") or []
        if not isinstance(claims, list) or any(
            not isinstance(claim, str) or not claim for claim in claims
        ):
            raise KernelError(
                "RESOURCE_CLAIMS_INVALID",
                "Plan Node Resource Claims are invalid",
            )

        # Resolve and freeze the Worker Runtime Profile before committing durable
        # Admission/Attempt state. Configuration changes after this point cannot
        # alter the profile selected for this Admission.
        difficulty = str(work_node.get("difficulty"))
        runtime_profile = self._resolve_worker_profile(
            repository=state["repository"],
            difficulty=difficulty,
        )
        state["runtime_profile"] = self._profile_to_dict(runtime_profile)
        state["profile_digest"] = runtime_profile.digest
        recovery_policy = work_node.get("recovery_policy") or {}
        if (
            isinstance(recovery_policy, dict)
            and recovery_policy.get("semantic_attempts", 1) > 1
        ):
            recovery_profile = self._resolve_frontier_profile(
                repository=state["repository"],
            )
            state["frontier_runtime_profile"] = self._profile_to_dict(recovery_profile)
            state["frontier_profile_digest"] = recovery_profile.digest
        else:
            state["frontier_runtime_profile"] = None
            state["frontier_profile_digest"] = None

        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            active = connection.execute(
                """
                SELECT plan_digest, writer_generation, activation_id
                FROM v8_active_plans
                WHERE repository = ?
                """,
                (state["repository"],),
            ).fetchone()
            pending = connection.execute(
                """
                SELECT 1
                FROM v8_pending_activations
                WHERE repository = ?
                """,
                (state["repository"],),
            ).fetchone()
            fence = connection.execute(
                """
                SELECT state FROM v8_writer_fences
                WHERE repository = ? AND writer_generation = ?
                  AND activation_id = ?
                """,
                (
                    state["repository"],
                    self.writer_generation,
                    activation_id,
                ),
            ).fetchone()
            hold = connection.execute(
                """
                SELECT reason
                FROM v8_goal_holds
                WHERE repository = ? AND goal_key = ?
                """,
                (state["repository"], state["goal_key"]),
            ).fetchone()
            if (
                active is None
                or active["plan_digest"] != state["plan_digest"]
                or active["writer_generation"] != self.writer_generation
                or active["activation_id"] != activation_id
                or pending is not None
                or (fence is not None and fence["state"] == "draining")
            ):
                raise KernelError(
                    "ADMISSION_PLAN_FENCED",
                    "active Plan or writer changed before Admission commit",
                )
            if hold is not None:
                raise KernelError(
                    "GOAL_ON_REPLAN_HOLD",
                    str(hold["reason"]),
                )
            active_turn_states = connection.execute(
                """
                SELECT state_json
                FROM v8_node_execution_state
                WHERE repository = ?
                  AND NOT (plan_digest = ? AND node_key = ?)
                """,
                (
                    state["repository"],
                    state["plan_digest"],
                    state["node_key"],
                ),
            ).fetchall()
            active_turns = 0
            for active_turn_row in active_turn_states:
                try:
                    active_turn_state = json.loads(active_turn_row["state_json"])
                except json.JSONDecodeError as error:
                    raise KernelError(
                        "CAPACITY_STATE_INVALID",
                        "Worker Active Turn occupancy cannot be read",
                    ) from error
                if self._state_holds_worker_turn(active_turn_state):
                    active_turns += 1
            if active_turns >= worker_turn_capacity:
                raise KernelError(
                    "WORKER_CAPACITY_UNAVAILABLE",
                    "configured or observed Worker Active Turn capacity is full",
                )
            prior_states = connection.execute(
                """
                SELECT plan_digest, state_json
                FROM v8_node_execution_state
                WHERE repository = ?
                  AND NOT (plan_digest = ? AND node_key = ?)
                """,
                (
                    state["repository"],
                    state["plan_digest"],
                    state["node_key"],
                ),
            ).fetchall()
            for prior_row in prior_states:
                try:
                    prior = json.loads(prior_row["state_json"])
                except json.JSONDecodeError as error:
                    raise KernelError(
                        "REPLACEMENT_STATE_INVALID",
                        "prior execution state cannot be checked for exclusion",
                    ) from error
                if prior.get("work_item_key") == state["work_item_key"] and prior.get(
                    "status"
                ) not in {"complete", "failed", "superseded"}:
                    raise KernelError(
                        "REPLACEMENT_PREDECESSOR_ACTIVE",
                        (
                            "replacement work cannot run beside its "
                            "non-terminal predecessor"
                        ),
                    )
            for dependency_key in dependency_keys:
                predecessor = connection.execute(
                    """
                    SELECT state FROM v8_node_states
                    WHERE repository = ? AND plan_digest = ? AND node_key = ?
                    """,
                    (
                        state["repository"],
                        state["plan_digest"],
                        dependency_key,
                    ),
                ).fetchone()
                if predecessor is None or predecessor["state"] not in {
                    "verified",
                    "integrated",
                    "complete",
                }:
                    raise KernelError(
                        "ADMISSION_DEPENDENCY_UNSATISFIED",
                        f"Plan Node dependency is not satisfied: {dependency_key}",
                    )
            existing = connection.execute(
                """
                SELECT admission_id, state
                FROM v8_admissions
                WHERE repository = ? AND node_key = ?
                  AND state NOT IN ('consumed', 'abandoned')
                """,
                (state["repository"], state["node_key"]),
            ).fetchone()
            attempt = connection.execute(
                """
                SELECT attempt_id
                FROM v8_attempts
                WHERE repository = ? AND node_key = ?
                  AND state NOT IN ('verified', 'terminal')
                """,
                (state["repository"], state["node_key"]),
            ).fetchone()
            if existing is not None or attempt is not None:
                raise KernelError(
                    "ADMISSION_ALREADY_EXISTS",
                    "Plan Node already has non-terminal execution",
                )
            for claim in sorted(set(claims)):
                occupied = connection.execute(
                    """
                    SELECT admission_id, attempt_id
                    FROM v8_resource_claims
                    WHERE repository = ? AND resource_key = ?
                    """,
                    (state["repository"], claim),
                ).fetchone()
                if occupied is not None:
                    raise KernelError(
                        "RESOURCE_CLAIM_UNAVAILABLE",
                        f"Resource Claim is already reserved: {claim}",
                    )
            connection.execute(
                """
                INSERT INTO v8_admissions (
                    admission_id,
                    repository,
                    plan_digest,
                    node_key,
                    goal_key,
                    state
                ) VALUES (?, ?, ?, ?, ?, 'materializing')
                """,
                (
                    state["admission_id"],
                    state["repository"],
                    state["plan_digest"],
                    state["node_key"],
                    state["goal_key"],
                ),
            )
            connection.execute(
                """
                INSERT INTO v8_node_states (
                    repository, plan_digest, node_key, state
                ) VALUES (?, ?, ?, 'materializing')
                ON CONFLICT(repository, plan_digest, node_key) DO UPDATE SET
                    state = excluded.state
                """,
                (
                    state["repository"],
                    state["plan_digest"],
                    state["node_key"],
                ),
            )
            for claim in sorted(set(claims)):
                connection.execute(
                    """
                    INSERT INTO v8_resource_claims (
                        repository,
                        resource_key,
                        admission_id,
                        attempt_id
                    ) VALUES (?, ?, ?, NULL)
                    """,
                    (state["repository"], claim, state["admission_id"]),
                )
            self._upsert_state(
                connection,
                repository=state["repository"],
                plan_digest=state["plan_digest"],
                rendered=self._render_state(state),
            )

    def _commit_attempt(
        self,
        state: dict[str, Any],
        *,
        attempt_id: str,
    ) -> None:
        self.publication.assert_new_work(
            state["repository"],
            writer_generation=self.writer_generation,
            activation_id=state["activation_id"],
        )
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            active = connection.execute(
                """
                SELECT plan_digest, writer_generation, activation_id
                FROM v8_active_plans
                WHERE repository = ?
                """,
                (state["repository"],),
            ).fetchone()
            pending = connection.execute(
                """
                SELECT 1 FROM v8_pending_activations WHERE repository = ?
                """,
                (state["repository"],),
            ).fetchone()
            fence = connection.execute(
                """
                SELECT state FROM v8_writer_fences
                WHERE repository = ? AND writer_generation = ?
                  AND activation_id = ?
                """,
                (
                    state["repository"],
                    self.writer_generation,
                    state["activation_id"],
                ),
            ).fetchone()
            admission = connection.execute(
                """
                SELECT state FROM v8_admissions
                WHERE admission_id = ? AND repository = ? AND plan_digest = ?
                """,
                (
                    state["admission_id"],
                    state["repository"],
                    state["plan_digest"],
                ),
            ).fetchone()
            if (
                active is None
                or active["plan_digest"] != state["plan_digest"]
                or active["writer_generation"] != self.writer_generation
                or active["activation_id"] != state["activation_id"]
                or pending is not None
                or (fence is not None and fence["state"] == "draining")
            ):
                raise KernelError(
                    "ATTEMPT_PLAN_FENCED",
                    "active Plan or writer changed before Attempt commit",
                )
            if admission is None or admission["state"] != "materializing":
                raise KernelError(
                    "ADMISSION_NOT_CONSUMABLE",
                    "Admission is not available for one atomic Attempt transition",
                )
            connection.execute(
                """
                INSERT INTO v8_attempts (
                    attempt_id, repository, plan_digest, node_key,
                    admission_id, state
                ) VALUES (?, ?, ?, ?, ?, 'running')
                """,
                (
                    attempt_id,
                    state["repository"],
                    state["plan_digest"],
                    state["node_key"],
                    state["admission_id"],
                ),
            )
            connection.execute(
                """
                UPDATE v8_admissions SET state = 'consumed'
                WHERE admission_id = ? AND state = 'materializing'
                """,
                (state["admission_id"],),
            )
            connection.execute(
                """
                UPDATE v8_node_states SET state = 'running'
                WHERE repository = ? AND plan_digest = ? AND node_key = ?
                """,
                (
                    state["repository"],
                    state["plan_digest"],
                    state["node_key"],
                ),
            )
            connection.execute(
                """
                UPDATE v8_resource_claims
                SET admission_id = NULL, attempt_id = ?
                WHERE repository = ? AND admission_id = ?
                """,
                (attempt_id, state["repository"], state["admission_id"]),
            )
            self._upsert_state(
                connection,
                repository=state["repository"],
                plan_digest=state["plan_digest"],
                rendered=self._render_state(state),
            )

    def _release_attempt_claims(self, state: dict[str, Any]) -> None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                UPDATE v8_attempts SET state = 'verified'
                WHERE attempt_id = ? AND state = 'running'
                """,
                (state["attempt_id"],),
            )
            connection.execute(
                """
                DELETE FROM v8_resource_claims
                WHERE repository = ? AND attempt_id = ?
                """,
                (state["repository"], state["attempt_id"]),
            )
            connection.execute(
                """
                UPDATE v8_node_states SET state = 'verified'
                WHERE repository = ? AND plan_digest = ? AND node_key = ?
                """,
                (
                    state["repository"],
                    state["plan_digest"],
                    state["node_key"],
                ),
            )
            self._upsert_state(
                connection,
                repository=state["repository"],
                plan_digest=state["plan_digest"],
                rendered=self._render_state(state),
            )

    def _acquire_integration_lease(
        self,
        repository: str,
        holder: str,
        *,
        activation_id: str,
    ) -> None:
        try:
            with self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                active = connection.execute(
                    """
                    SELECT writer_generation, activation_id
                    FROM v8_active_plans WHERE repository = ?
                    """,
                    (repository,),
                ).fetchone()
                fence = connection.execute(
                    """
                    SELECT writer_generation, activation_id, state
                    FROM v8_writer_fences WHERE repository = ?
                    """,
                    (repository,),
                ).fetchone()
                if (
                    active is None
                    or active["writer_generation"] != self.writer_generation
                    or active["activation_id"] != activation_id
                    or fence is not None
                ):
                    raise KernelError(
                        "WRITER_NEW_WORK_FENCED",
                        "integration lease acquisition crossed a writer drain",
                    )
                connection.execute(
                    """
                    INSERT INTO v8_integration_leases (repository, holder)
                    VALUES (?, ?)
                    """,
                    (repository, holder),
                )
        except sqlite3.IntegrityError as error:
            raise KernelError(
                "INTEGRATION_LEASE_UNAVAILABLE",
                "another integration owns the repository lease",
            ) from error

    def _release_integration_lease(self, repository: str, holder: str) -> None:
        with self._connect() as connection:
            deleted = connection.execute(
                """
                DELETE FROM v8_integration_leases
                WHERE repository = ? AND holder = ?
                """,
                (repository, holder),
            ).rowcount
        if deleted != 1:
            raise KernelError(
                "INTEGRATION_LEASE_LOST", "integration lease identity changed"
            )

    @staticmethod
    def _outcome(state: dict[str, Any]) -> ReconcileOutcome:
        return ReconcileOutcome(
            **{
                field: state[field]
                for field in ReconcileOutcome.__dataclass_fields__
                if field in state
            }
        )

    @staticmethod
    def _kernel_sweep_allowed(
        outcomes: tuple[ReconcileOutcome, ...],
    ) -> bool:
        pending = tuple(
            outcome
            for outcome in outcomes
            if outcome.status not in {"complete", "failed", "superseded"}
            and outcome.wait_condition is not None
        )
        has_runnable = any(
            outcome.status not in {"complete", "failed", "superseded"}
            and outcome.wait_condition is None
            for outcome in outcomes
        )
        has_semantic_directive = any(
            outcome.directive
            in {
                "invoke_coordinator",
                "request_decision",
                "wait_for_decision",
            }
            for outcome in pending
        )
        return len(pending) > 1 and not has_runnable and not has_semantic_directive

    @staticmethod
    def _representative_outcome(
        outcomes: tuple[ReconcileOutcome, ...],
    ) -> ReconcileOutcome:
        return next(
            (
                outcome
                for outcome in outcomes
                if outcome.directive
                in {
                    "invoke_coordinator",
                    "request_decision",
                    "wait_for_decision",
                }
            ),
            next(
                (
                    outcome
                    for outcome in outcomes
                    if outcome.status not in {"complete", "failed", "superseded"}
                    and outcome.wait_condition is None
                ),
                next(
                    (
                        outcome
                        for outcome in outcomes
                        if outcome.status
                        not in {"complete", "failed", "superseded"}
                    ),
                    outcomes[0],
                ),
            ),
        )

    @staticmethod
    def _state_holds_worker_turn(state: dict[str, Any]) -> bool:
        if state.get("status") in {"complete", "failed", "superseded"}:
            return False
        if state.get("status") == "blocked" and state.get("wait_condition") is None:
            return False
        if state.get("attempt_state") in {
            "batch_ready",
            "batch_wait",
            "integration_refresh_required",
            "integration_wait",
            "parked",
            "verified",
        }:
            return False
        if state.get("wait_condition") in {
            "evidence_source",
            "hosted_ci",
            "hosted_ci_cancelled",
            "human_decision",
            "integration_lease",
            "integration_refresh",
            "integration_turn",
            "runtime_available",
            "worker_capacity",
        }:
            return False
        return state.get("admission_state") not in {
            "adopted",
            "materialization_blocked",
        }

    def _other_active_worker_turns(
        self,
        connection: sqlite3.Connection,
        *,
        repository: str,
        plan_digest: str,
        node_key: str,
    ) -> int:
        rows = connection.execute(
            """
            SELECT state_json
            FROM v8_node_execution_state
            WHERE repository = ?
              AND NOT (plan_digest = ? AND node_key = ?)
            """,
            (repository, plan_digest, node_key),
        ).fetchall()
        active_turns = 0
        for row in rows:
            try:
                other = json.loads(row["state_json"])
            except json.JSONDecodeError as error:
                raise KernelError(
                    "CAPACITY_STATE_INVALID",
                    "Worker Active Turn occupancy cannot be read",
                ) from error
            if self._state_holds_worker_turn(other):
                active_turns += 1
        return active_turns

    def _reserve_or_park_recovery_turn(
        self,
        state: dict[str, Any],
        *,
        worker_turn_capacity: int,
    ) -> str:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT state_json
                FROM v8_node_execution_state
                WHERE repository = ? AND plan_digest = ? AND node_key = ?
                """,
                (
                    state["repository"],
                    state["plan_digest"],
                    state["node_key"],
                ),
            ).fetchone()
            if row is None:
                raise KernelError(
                    "RECOVERY_STATE_MISSING",
                    "recovery reservation has no durable Node state",
                )
            try:
                current = json.loads(row["state_json"])
            except json.JSONDecodeError as error:
                raise KernelError(
                    "RECOVERY_STATE_INVALID",
                    "recovery reservation state cannot be read",
                ) from error
            if (
                current.get("attempt_id") == state.get("attempt_id")
                and current.get("attempt_state") == "recovery_reserved"
            ):
                reserved_at = current.get("recovery_reserved_at")
                try:
                    still_owned = (
                        reserved_at is not None
                        and datetime.fromisoformat(str(reserved_at))
                        > datetime.now(timezone.utc) - timedelta(seconds=30)
                    )
                except ValueError:
                    still_owned = False
                if still_owned:
                    state.clear()
                    state.update(current)
                    return "adopted"
            if (
                current.get("attempt_id") == state.get("attempt_id")
                and current.get("attempt_state")
                not in {"candidate_rejected", "recovery_reserved"}
            ):
                state.clear()
                state.update(current)
                return "adopted"
            available = (
                self._other_active_worker_turns(
                    connection,
                    repository=state["repository"],
                    plan_digest=state["plan_digest"],
                    node_key=state["node_key"],
                )
                < worker_turn_capacity
            )
            if available:
                state.update(
                    {
                        "status": "waiting",
                        "directive": "reconcile_again",
                        "attempt_state": "recovery_reserved",
                        "recovery_reserved_at": _now(),
                        "wait_condition": "recovery_dispatch",
                        "wait_source_ref": (
                            f"store://recovery-dispatch/{state['attempt_id']}"
                        ),
                        "wait_event_identity": (
                            f"recovery-dispatch:{state['attempt_id']}"
                        ),
                        "next_check_at": (
                            datetime.now(timezone.utc) + timedelta(seconds=30)
                        ).isoformat(),
                    }
                )
            else:
                state.update(
                    {
                        "status": "waiting",
                        "directive": "wait_for_capacity",
                        "attempt_state": "candidate_rejected",
                        "recovery_reserved_at": None,
                        "wait_condition": "worker_capacity",
                        "wait_source_ref": (
                            f"capacity://{state['repository']}/workers"
                        ),
                        "wait_event_identity": (
                            f"worker-capacity:{state['attempt_id']}"
                        ),
                        "next_check_at": None,
                    }
                )
            self._upsert_state(
                connection,
                repository=state["repository"],
                plan_digest=state["plan_digest"],
                rendered=self._render_state(state),
            )
            return "reserved" if available else "parked"

    def _reacquire_waiting_worker_turns(
        self,
        states: dict[str, dict[str, Any]],
        *,
        worker_turn_capacity: int,
    ) -> None:
        projected = self._project_reacquired_worker_turns(
            states,
            worker_turn_capacity=worker_turn_capacity,
        )
        for node_key, state in projected.items():
            if state == states[node_key]:
                continue
            self._write_state(
                state["repository"],
                state["plan_digest"],
                state,
            )
        states.clear()
        states.update(projected)

    @classmethod
    def _project_reacquired_worker_turns(
        cls,
        states: dict[str, dict[str, Any]],
        *,
        worker_turn_capacity: int,
    ) -> dict[str, dict[str, Any]]:
        projected = {
            node_key: dict(state) for node_key, state in states.items()
        }
        for node_key in sorted(projected):
            state = projected[node_key]
            if state.get("wait_condition") != "worker_capacity":
                continue
            other_turns = sum(
                cls._state_holds_worker_turn(other)
                for other_key, other in projected.items()
                if other_key != node_key
            )
            if other_turns >= worker_turn_capacity:
                continue
            state.update(
                {
                    "status": "rejected",
                    "directive": "invoke_coordinator",
                    "wait_condition": None,
                    "wait_source_ref": None,
                    "wait_event_identity": None,
                    "next_check_at": None,
                }
            )
        return projected

    def _turn_capacities(self, repository: str) -> tuple[int, int]:
        try:
            pools = resolve_active_turn_pools(
                self.runtime_config,
                repository=repository,
            )
        except RuntimeAdapterError as error:
            raise KernelError(error.code, error.detail) from error
        workers = pools.workers
        coordinators = pools.coordinators
        active = self.publication.read_active(repository)
        if active is None:
            raise KernelError(
                "PLAN_NOT_ACTIVE",
                "repository has no active Plan Revision",
            )
        authority_limits = self.publication.capacity_limits(
            repository,
            writer_generation=self.writer_generation,
            activation_id=active.activation_id,
        )
        if authority_limits is not None:
            workers = min(workers, authority_limits[0])
            coordinators = min(coordinators, authority_limits[1])
        observed = getattr(
            type(self.runtime),
            "observed_worker_turn_capacity",
            None,
        )
        if callable(observed):
            runtime_capacity = observed(self.runtime, self.runtime_profile)
            if runtime_capacity is not None:
                if (
                    not isinstance(runtime_capacity, int)
                    or isinstance(runtime_capacity, bool)
                    or runtime_capacity < 0
                ):
                    raise KernelError(
                        "RUNTIME_CAPACITY_INVALID",
                        "Runtime Worker capacity observation is invalid",
                    )
                workers = min(workers, runtime_capacity)
        return workers, coordinators

    def _materialize_admitted_frontier(
        self,
        repository: str,
        plan_digest: str,
        work_nodes: tuple[dict[str, Any], ...],
        *,
        worker_turn_capacity: int,
    ) -> None:
        def materialize(work_node: dict[str, Any]) -> None:
            state = self._read_state(
                repository,
                plan_digest,
                str(work_node["node_key"]),
            )
            if state is None:
                raise KernelError(
                    "ADMISSION_STATE_MISSING",
                    "committed Admission has no execution state",
                )
            self._adopt_or_materialize(state, work_node)

        if len(work_nodes) <= 1:
            return
        with ThreadPoolExecutor(
            max_workers=min(worker_turn_capacity, len(work_nodes)),
            thread_name_prefix="gwo-materialize",
        ) as executor:
            futures = tuple(executor.submit(materialize, node) for node in work_nodes)
            for future in futures:
                future.result()

    @staticmethod
    def _prompt_from_state(state: dict[str, Any]) -> RuntimePrompt:
        snapshot = state.get("prompt_snapshot")
        if not isinstance(snapshot, dict):
            raise KernelError(
                "PROMPT_SNAPSHOT_MISSING",
                "Admission has no frozen Prompt snapshot",
            )
        return RuntimePrompt(
            text=str(snapshot["text"]),
            digest=str(snapshot["digest"]),
            authority_digest=snapshot.get("authority_digest"),
            skill_name=snapshot.get("skill_name"),
            skill_digest=snapshot.get("skill_digest"),
            warnings=tuple(snapshot.get("warnings") or ()),
            contract_node=(
                snapshot.get("contract_node")
                if isinstance(snapshot.get("contract_node"), dict)
                else None
            ),
        )

    def _materialization_failure(
        self,
        state: dict[str, Any],
        error: RuntimeAdapterError,
        *,
        operation: str,
    ) -> ReconcileOutcome:
        actions = state.setdefault("materialization_actions", {})
        executions = int(actions.get(operation, 0))
        circuit_key = f"{self.runtime.adapter_name}:{operation}:{error.failure_class}"
        circuits = state.setdefault("runtime_circuits", {})
        previous = circuits.get(circuit_key)
        consecutive = (
            int(previous.get("consecutive_failures", 0)) + 1
            if isinstance(previous, dict)
            else 1
        )
        next_check_at = (datetime.now(timezone.utc) + timedelta(seconds=30)).isoformat()
        if error.failure_class == "ambiguous":
            state.update(
                {
                    "status": "waiting",
                    "directive": "wait_for_runtime_readback",
                    "admission_state": "materialization_ambiguous",
                    "wait_condition": "runtime_identity_readback",
                    "wait_source_ref": (
                        f"{self.runtime.adapter_name}://"
                        f"admission/{state['admission_id']}"
                    ),
                    "wait_event_identity": f"{operation}:identity_readback",
                    "next_check_at": next_check_at,
                }
            )
        elif (
            error.failure_class == "transient"
            and consecutive < 3
            and (operation not in {"create", "prompt"} or executions < 3)
        ):
            opened = consecutive >= 2
            circuit_state = {
                "key": circuit_key,
                "state": "open" if opened else "closed",
                "consecutive_failures": consecutive,
                "probe_executed": bool(
                    isinstance(previous, dict) and previous.get("probe_executed")
                ),
            }
            circuits[circuit_key] = circuit_state
            state.update(
                {
                    "status": "waiting",
                    "directive": (
                        "wait_for_runtime_circuit"
                        if opened
                        else "retry_materialization"
                    ),
                    "admission_state": "materialization_retry",
                    "wait_condition": (
                        "runtime_circuit_probe" if opened else "runtime_retry_due"
                    ),
                    "runtime_circuit": circuit_key,
                    "runtime_circuit_state": circuit_state,
                    "wait_source_ref": (
                        f"{self.runtime.adapter_name}://"
                        f"admission/{state['admission_id']}"
                    ),
                    "wait_event_identity": f"{operation}:retry",
                    "next_check_at": next_check_at,
                }
            )
        else:
            circuit_state = {
                "key": circuit_key,
                "state": "open",
                "consecutive_failures": consecutive,
                "probe_executed": True,
            }
            circuits[circuit_key] = circuit_state
            state.update(
                {
                    "status": "blocked",
                    "directive": "request_decision",
                    "admission_state": "materialization_blocked",
                    "wait_condition": None,
                    "runtime_circuit": circuit_key,
                    "runtime_circuit_state": circuit_state,
                    "wait_source_ref": None,
                    "wait_event_identity": None,
                    "next_check_at": None,
                }
            )
        state["last_runtime_error"] = _runtime_error_record(error)
        self._write_state(state["repository"], state["plan_digest"], state)
        return self._outcome(state)

    def _prepare_runtime_operation(
        self,
        state: dict[str, Any],
        operation: str,
    ) -> ReconcileOutcome | None:
        prefix = f"{self.runtime.adapter_name}:{operation}:"
        circuits = state.setdefault("runtime_circuits", {})
        for key, circuit in tuple(circuits.items()):
            if (
                not key.startswith(prefix)
                or not isinstance(circuit, dict)
                or circuit.get("state") != "open"
            ):
                continue
            if circuit.get("probe_executed"):
                return self._materialization_failure(
                    state,
                    RuntimeAdapterError(
                        "RUNTIME_CIRCUIT_PROBE_EXHAUSTED",
                        f"the single {operation} circuit probe was already used",
                    ),
                    operation=operation,
                )
            updated = {
                **circuit,
                "state": "half_open",
                "probe_executed": True,
            }
            circuits[key] = updated
            state["runtime_circuit"] = key
            state["runtime_circuit_state"] = updated
            self._write_state(
                state["repository"],
                state["plan_digest"],
                state,
            )
        return None

    def _clear_runtime_operation(
        self,
        state: dict[str, Any],
        operation: str,
    ) -> None:
        prefix = f"{self.runtime.adapter_name}:{operation}:"
        circuits = state.setdefault("runtime_circuits", {})
        for key in tuple(circuits):
            if key.startswith(prefix):
                del circuits[key]
        if str(state.get("runtime_circuit") or "").startswith(prefix):
            state["runtime_circuit"] = None
            state["runtime_circuit_state"] = None

    def _initial_state(
        self,
        *,
        repository: str,
        plan_digest: str,
        goal: dict[str, Any],
        work_item: dict[str, Any],
        work_node: dict[str, Any],
    ) -> dict[str, Any]:
        prompt = RuntimePrompt.from_node(
            work_node,
            skill_catalog=self.skill_catalog,
        )
        state = {
            "status": "running",
            "directive": "run_again",
            "repository": repository,
            "plan_digest": plan_digest,
            "goal_key": goal["goal_key"],
            "goal_state": "active",
            "work_item_key": work_item["work_item_key"],
            "work_item_state": "active",
            "node_key": work_node["node_key"],
            "contract_digest": work_node["contract_digest"],
            "admission_id": (
                f"admission:{plan_digest[:12]}:{work_node['node_key'][-12:]}"
            ),
            "admission_state": "materializing",
            "attempt_id": None,
            "attempt_state": None,
            "attempt_ordinal": 1,
            "repair_rounds_used": 0,
            "attempt_terminal_reason": None,
            "candidate_sha": None,
            "result_digest": None,
            "publication_eligible": None,
            "publication_state": None,
            "publication_ref": None,
            "hosted_check_state": None,
            "hosted_retry_count": 0,
            "integration_batch_id": None,
            "integration_batch_sha": None,
            "materialization_executions": 0,
            "materialization_actions": {"create": 0, "prompt": 0},
            "wait_condition": None,
            "runtime_circuit": None,
            "runtime_circuit_state": None,
            "runtime_circuits": {},
            "runtime_observation_failures": 0,
            "wait_source_ref": None,
            "wait_event_identity": None,
            "next_check_at": None,
            "base_sha": _git(
                self.repository_path,
                "rev-parse",
                self.integration_branch,
            ),
            "prompt_snapshot": {
                "text": prompt.text,
                "digest": prompt.digest,
                "authority_digest": prompt.authority_digest,
                "skill_name": prompt.skill_name,
                "skill_digest": prompt.skill_digest,
                "warnings": list(prompt.warnings),
                "contract_node": prompt.contract_node,
            },
            "resume_sent": False,
        }
        state.update(ReviewConvergence.initial_fields())
        return state

    def _adopt_verified_result(
        self,
        state: dict[str, Any],
        work_node: dict[str, Any],
    ) -> bool:
        current_base = _git(
            self.repository_path,
            "rev-parse",
            self.integration_branch,
        )
        checks = (work_node.get("output_contract") or {}).get("checks") or ()
        base_sensitive = any(
            isinstance(check, dict) and check.get("base_sensitive") is True
            for check in checks
        )
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT
                    plan_digest,
                    candidate_sha,
                    result_digest,
                    base_sha,
                    evidence_manifest_digest,
                    evidence_json
                FROM v8_verified_results
                WHERE repository = ? AND node_key = ?
                  AND contract_digest = ? AND superseded = 0
                ORDER BY rowid DESC
                """,
                (
                    state["repository"],
                    state["node_key"],
                    state["contract_digest"],
                ),
            ).fetchall()
        for row in rows:
            if not isinstance(row["evidence_json"], str) or not isinstance(
                row["evidence_manifest_digest"], str
            ):
                continue
            try:
                evidence_record = json.loads(row["evidence_json"])
                if (
                    not isinstance(evidence_record, dict)
                    or digest_value(evidence_record) != row["evidence_manifest_digest"]
                ):
                    continue
                historical_binding = RuntimeBinding(**evidence_record["binding"])
                historical_claim = ResultClaim(**evidence_record["result_claim"])
                historical_observation = RuntimeObservation(
                    binding=historical_binding,
                    lifecycle=str(evidence_record.get("lifecycle") or "completed"),
                    result_claim=historical_claim,
                    evidence=tuple(
                        TypedEvidence(**item) for item in evidence_record["evidence"]
                    ),
                )
            except (KeyError, TypeError, json.JSONDecodeError):
                continue
            candidate_sha = str(row["candidate_sha"])
            ancestry = subprocess.run(
                [
                    "git",
                    "-C",
                    str(self.repository_path),
                    "merge-base",
                    "--is-ancestor",
                    candidate_sha,
                    current_base,
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
            if ancestry.returncode != 0:
                continue
            refresh_evidence_digests: tuple[str, ...] = ()
            if base_sensitive and row["base_sha"] != current_base:
                refreshed = self._refresh_base_sensitive_evidence(
                    candidate_sha=candidate_sha,
                    current_base=current_base,
                    checks=checks,
                    evidence=historical_observation.evidence,
                )
                if refreshed is None:
                    continue
                refreshed_evidence, refresh_evidence_digests = refreshed
                historical_observation = replace(
                    historical_observation,
                    evidence=refreshed_evidence,
                )
            verified = self.verifier.verify(
                historical_claim,
                work_node["output_contract"],
                historical_observation,
            )
            hosted_definitions = tuple(
                check
                for check in checks
                if isinstance(check, dict) and check.get("hosted_only") is True
            )
            hosted_evidence = evidence_record.get("hosted_check_evidence") or ()
            hosted_findings = self.verifier.verify_hosted_checks(
                historical_claim.candidate_sha,
                hosted_definitions,
                tuple(
                    TypedEvidence(**value)
                    for value in hosted_evidence
                    if isinstance(value, dict)
                ),
            )
            if hosted_definitions and hosted_findings:
                batch_record = evidence_record.get("integration_batch")
                try:
                    batch_sha = str(batch_record["batch_sha"])
                    batch_hosted = tuple(
                        TypedEvidence(**value)
                        for value in batch_record["hosted_check_evidence"]
                        if isinstance(value, dict)
                    )
                    batch_integration = TypedEvidence(
                        **batch_record["integration_evidence"]
                    )
                except (KeyError, TypeError):
                    batch_sha = ""
                    batch_hosted = ()
                    batch_integration = None
                batch_ancestry = (
                    subprocess.run(
                        [
                            "git",
                            "-C",
                            str(self.repository_path),
                            "merge-base",
                            "--is-ancestor",
                            batch_sha,
                            current_base,
                        ],
                        capture_output=True,
                        text=True,
                        encoding="utf-8",
                    )
                    if batch_sha
                    else None
                )
                expected_hosted_names = {
                    str(definition["hosted_name"])
                    for definition in hosted_definitions
                }
                observed_hosted_names = {
                    str(item.payload.get("hosted_name"))
                    for item in batch_hosted
                    if item.kind == "check"
                    and item.subject == batch_sha
                    and item.payload.get("candidate_sha") == batch_sha
                    and item.payload.get("outcome") == "passed"
                    and item.has_valid_digest()
                }
                batch_is_valid = (
                    batch_ancestry is not None
                    and batch_ancestry.returncode == 0
                    and batch_integration is not None
                    and batch_integration.kind == "integration"
                    and batch_integration.subject == batch_sha
                    and batch_integration.payload.get("candidate_sha")
                    == candidate_sha
                    and state["node_key"]
                    in (
                        batch_integration.payload.get("member_node_keys")
                        or ()
                    )
                    and batch_integration.has_valid_digest()
                    and expected_hosted_names <= observed_hosted_names
                )
                if batch_is_valid:
                    hosted_findings = ()
            if (
                verified.status != "accepted"
                or verified.result is None
                or (
                    not refresh_evidence_digests
                    and verified.result.result_digest != row["result_digest"]
                )
                or hosted_findings
            ):
                continue
            adoption = TypedEvidence._capture(
                kind="integration",
                subject=candidate_sha,
                observer_type="kernel",
                observer_id=self.writer_generation,
                observed_at=_now(),
                source_ref=(
                    f"store://verified-result/{row['plan_digest']}/{state['node_key']}"
                ),
                payload={
                    "adopted_from_plan_digest": row["plan_digest"],
                    "node_key": state["node_key"],
                    "contract_digest": state["contract_digest"],
                    "candidate_sha": candidate_sha,
                    "target_head": current_base,
                    "base_sensitive_refresh_evidence_digests": list(
                        refresh_evidence_digests
                    ),
                },
            )
            state.update(
                {
                    "status": "complete",
                    "directive": "goal_complete",
                    "goal_state": "completed",
                    "work_item_state": "integrated",
                    "admission_state": "adopted",
                    "attempt_state": "adopted",
                    "candidate_sha": candidate_sha,
                    "result_digest": verified.result.result_digest,
                    "adopted_from_plan_digest": row["plan_digest"],
                    "base_sensitive_refresh_evidence_digests": list(
                        refresh_evidence_digests
                    ),
                    "integration_evidence_digest": adoption.content_digest,
                }
            )
            self._write_state(
                state["repository"],
                state["plan_digest"],
                state,
            )
            return True
        return False

    def _refresh_base_sensitive_evidence(
        self,
        *,
        candidate_sha: str,
        current_base: str,
        checks: Any,
        evidence: tuple[TypedEvidence, ...],
    ) -> tuple[tuple[TypedEvidence, ...], tuple[str, ...]] | None:
        definitions = tuple(
            check
            for check in checks
            if isinstance(check, dict) and check.get("base_sensitive") is True
        )
        if not definitions:
            return evidence, ()
        if any(check.get("hosted_only") is True for check in definitions):
            return None
        check_ids = {str(check["check_id"]) for check in definitions}
        retained = [
            item
            for item in evidence
            if not (item.kind == "check" and item.payload.get("check_id") in check_ids)
        ]
        try:
            candidate_tree = _git(
                self.repository_path,
                "rev-parse",
                f"{candidate_sha}^{{tree}}",
            )
            base_tree = _git(
                self.repository_path,
                "rev-parse",
                f"{current_base}^{{tree}}",
            )
            with tempfile.TemporaryDirectory(prefix="gwo-adoption-") as parent:
                workspace = Path(parent) / "workspace"
                add = _run(
                    [
                        "git",
                        "worktree",
                        "add",
                        "--detach",
                        str(workspace),
                        current_base,
                    ],
                    cwd=self.repository_path,
                )
                if add.returncode != 0:
                    return None
                try:
                    refreshed: list[TypedEvidence] = []
                    for check in definitions:
                        command = [str(part) for part in check.get("command") or ()]
                        if not command:
                            return None
                        result = _run(command, cwd=workspace)
                        environment_requirements = tuple(
                            str(item)
                            for item in check.get("environment_requirements") or ()
                        )
                        environment = _environment_snapshot(
                            environment_requirements,
                            cwd=workspace,
                        )
                        if (
                            result.returncode != 0
                            or _git(workspace, "rev-parse", "HEAD") != current_base
                            or _git(
                                workspace,
                                "status",
                                "--porcelain=v1",
                                "--untracked-files=all",
                            )
                        ):
                            return None
                        log = f"{result.stdout}\n{result.stderr}".encode("utf-8")
                        refreshed.append(
                            TypedEvidence._capture(
                                kind="check",
                                subject=candidate_sha,
                                observer_type="kernel",
                                observer_id=self.writer_generation,
                                observed_at=_now(),
                                source_ref=(
                                    "kernel://result-adoption/"
                                    f"{candidate_sha}/check/{check['check_id']}"
                                ),
                                payload={
                                    "check_id": check["check_id"],
                                    "definition_digest": check.get("definition_digest"),
                                    "command_digest": digest_value(command),
                                    "observed_tree_digest": candidate_tree,
                                    "base_sha": current_base,
                                    "observed_base_tree_digest": base_tree,
                                    "execution_sha": current_base,
                                    "execution_tree_digest": base_tree,
                                    "environment_requirements": list(
                                        environment_requirements
                                    ),
                                    "environment_identity": environment,
                                    "environment_digest": digest_value(environment),
                                    "input_projection_digest": (
                                        _input_projection_digest(
                                            workspace,
                                            current_base,
                                            tuple(check.get("input_selector") or ()),
                                        )
                                    ),
                                    "exit_code": result.returncode,
                                    "outcome": "passed",
                                    "stdout_digest": digest_bytes(
                                        result.stdout.encode("utf-8")
                                    ),
                                    "stderr_digest": digest_bytes(
                                        result.stderr.encode("utf-8")
                                    ),
                                    "log_digest": digest_bytes(log),
                                },
                            )
                        )
                finally:
                    subprocess.run(
                        [
                            "git",
                            "-C",
                            str(self.repository_path),
                            "worktree",
                            "remove",
                            "--force",
                            str(workspace),
                        ],
                        capture_output=True,
                        text=True,
                        encoding="utf-8",
                    )
        except (OSError, RuntimeAdapterError):
            return None
        combined = tuple(retained + refreshed)
        return combined, tuple(item.content_digest for item in refreshed)

    def _record_verified_result(
        self,
        state: dict[str, Any],
        *,
        candidate_sha: str,
        result_digest: str,
    ) -> None:
        candidate_observation = state.get("candidate_observation")
        if not isinstance(candidate_observation, dict):
            raise KernelError(
                "VERIFIED_RESULT_EVIDENCE_MISSING",
                "verified Result has no persisted Evidence record",
            )
        evidence_record = {
            **candidate_observation,
            "hosted_check_evidence": list(state.get("hosted_check_evidence") or ()),
            "integration_batch": {
                "batch_id": state.get("integration_batch_id"),
                "batch_sha": state.get("integration_batch_sha"),
                "hosted_check_evidence": list(
                    state.get("integration_batch_hosted_check_evidence") or ()
                ),
                "integration_evidence": state.get("integration_evidence"),
            },
        }
        evidence_json = self._render_state(evidence_record)
        evidence_manifest_digest = digest_value(evidence_record)
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO v8_verified_results (
                    repository,
                    plan_digest,
                    node_key,
                    contract_digest,
                    candidate_sha,
                    result_digest,
                    base_sha,
                    evidence_manifest_digest,
                    evidence_json,
                    superseded
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
                ON CONFLICT(
                    repository,
                    plan_digest,
                    node_key,
                    candidate_sha
                ) DO UPDATE SET
                    result_digest = excluded.result_digest,
                    base_sha = excluded.base_sha,
                    evidence_manifest_digest = excluded.evidence_manifest_digest,
                    evidence_json = excluded.evidence_json,
                    superseded = 0
                """,
                (
                    state["repository"],
                    state["plan_digest"],
                    state["node_key"],
                    state["contract_digest"],
                    candidate_sha,
                    result_digest,
                    state["base_sha"],
                    evidence_manifest_digest,
                    evidence_json,
                ),
            )

    @staticmethod
    def _validate_plan(repository: str, canonical_bytes_value: bytes) -> dict[str, Any]:
        try:
            plan = json.loads(canonical_bytes_value)
        except json.JSONDecodeError as error:
            raise KernelError(
                "ACTIVE_PLAN_INVALID", "active Compiler bytes are not valid PlanSpec"
            ) from error
        if plan.get("schema_version") != 2 or plan.get("repository") != repository:
            raise KernelError(
                "ACTIVE_PLAN_INVALID", "active PlanSpec identity is invalid"
            )
        return plan

    @staticmethod
    def _work_units(
        plan: dict[str, Any],
    ) -> tuple[
        tuple[
            dict[str, Any],
            dict[str, Any],
            dict[str, Any],
            dict[str, Any],
        ],
        ...,
    ]:
        work_nodes = [
            node for node in plan.get("nodes") or [] if node.get("kind") == "work"
        ]
        integration_nodes = [
            node
            for node in plan.get("nodes") or []
            if node.get("kind") == "integration"
        ]
        goals = {
            goal.get("goal_key"): goal
            for goal in plan.get("goals") or ()
            if isinstance(goal, dict) and isinstance(goal.get("goal_key"), str)
        }
        work_items = {
            work_item.get("work_item_key"): work_item
            for work_item in plan.get("work_items") or ()
            if isinstance(work_item, dict)
            and isinstance(work_item.get("work_item_key"), str)
        }
        integration_by_item: dict[str, dict[str, Any]] = {}
        for integration in integration_nodes:
            work_item_key = integration.get("work_item_key")
            if (
                not isinstance(work_item_key, str)
                or work_item_key in integration_by_item
            ):
                raise KernelError(
                    "ACTIVE_PLAN_INVALID",
                    "Integration Plan Nodes must map one-to-one to Work Items",
                )
            integration_by_item[work_item_key] = integration
        if not work_nodes or len(work_nodes) != len(integration_nodes):
            raise KernelError(
                "ACTIVE_PLAN_UNSUPPORTED",
                "each Work Plan Node requires one Integration Plan Node",
            )
        units = []
        for work_node in sorted(work_nodes, key=lambda node: node["node_key"]):
            goal = goals.get(work_node.get("goal_key"))
            work_item = work_items.get(work_node.get("work_item_key"))
            integration = integration_by_item.get(
                str(work_node.get("work_item_key") or "")
            )
            if (
                not isinstance(goal, dict)
                or not isinstance(work_item, dict)
                or not isinstance(integration, dict)
                or integration.get("goal_key") != work_node.get("goal_key")
            ):
                raise KernelError(
                    "ACTIVE_PLAN_INVALID",
                    "Work, Goal, Work Item, and Integration identities do not agree",
                )
            units.append((work_node, integration, goal, work_item))
        return tuple(units)

    def plan_reconciliation(
        self,
        repository: str,
    ) -> KernelReconciliationPlan:
        """Plan the same Admission frontier consumed by ``reconcile_once``."""
        active = self.publication.read_active(repository)
        if active is None:
            raise KernelError(
                "PLAN_NOT_ACTIVE",
                "repository has no active Plan Revision",
            )
        if active.writer_generation != self.writer_generation:
            raise KernelError(
                "WRITER_GENERATION_MISMATCH",
                "Kernel does not own the active writer generation",
            )
        plan = self._validate_plan(repository, active.canonical_bytes)
        units = self._work_units(plan)
        worker_capacity, _coordinator_capacity = self._turn_capacities(repository)
        existing = {
            str(state["node_key"]): state
            for state in self._read_states(repository, active.plan_digest)
            if isinstance(state.get("node_key"), str)
        }
        existing = self._project_reacquired_worker_turns(
            existing,
            worker_turn_capacity=worker_capacity,
        )
        active_turns = sum(
            self._state_holds_worker_turn(state)
            for state in existing.values()
        )
        with self._connect() as connection:
            holds = {
                str(row["goal_key"])
                for row in connection.execute(
                    """
                    SELECT goal_key FROM v8_goal_holds WHERE repository = ?
                    """,
                    (repository,),
                ).fetchall()
            }
            node_states = {
                str(row["node_key"]): str(row["state"])
                for row in connection.execute(
                    """
                    SELECT node_key, state FROM v8_node_states
                    WHERE repository = ? AND plan_digest = ?
                    """,
                    (repository, active.plan_digest),
                ).fetchall()
            }
            occupied_claims = {
                str(row["resource_key"])
                for row in connection.execute(
                    """
                    SELECT resource_key FROM v8_resource_claims
                    WHERE repository = ?
                    """,
                    (repository,),
                ).fetchall()
            }
            open_nodes = {
                str(row["node_key"])
                for row in connection.execute(
                    """
                    SELECT node_key FROM v8_admissions
                    WHERE repository = ?
                      AND state NOT IN ('consumed', 'abandoned')
                    UNION
                    SELECT node_key FROM v8_attempts
                    WHERE repository = ?
                      AND state NOT IN ('verified', 'terminal')
                    """,
                    (repository, repository),
                ).fetchall()
            }
        dependencies = {
            str(node["node_key"]): tuple(
                str(edge["from_node"])
                for edge in plan.get("edges") or ()
                if isinstance(edge, dict)
                and edge.get("to_node") == node["node_key"]
                and isinstance(edge.get("from_node"), str)
            )
            for node, _integration, _goal, _work_item in units
        }
        actions: list[KernelPreviewAction] = []
        admissible: list[str] = []
        projected_claims = set(occupied_claims)
        for work_node, _integration, goal, work_item in units:
            node_key = str(work_node["node_key"])
            state = existing.get(node_key)
            if state is not None:
                if state.get("status") == "complete":
                    continue
                wait_condition = state.get("wait_condition")
                kind = {
                    "hosted_ci": "would_wait_for_hosted_ci",
                    "integration_refresh": "would_request_integration_refresh",
                }.get(
                    str(wait_condition),
                    "would_wait" if wait_condition else "would_reconcile",
                )
                actions.append(
                    KernelPreviewAction(
                        kind=kind,
                        node_key=node_key,
                        source_ref=str(
                            state.get("wait_source_ref")
                            or f"store://state/{node_key}"
                        ),
                    )
                )
                continue
            blockers: list[str] = []
            if active_turns >= worker_capacity:
                blockers.append("worker_capacity")
            if str(goal["goal_key"]) in holds:
                blockers.append("goal_hold")
            if node_key in open_nodes:
                blockers.append("existing_execution")
            if any(
                node_states.get(key)
                not in {"verified", "integrated", "complete"}
                for key in dependencies[node_key]
            ):
                blockers.append("dependency")
            claims = {
                str(claim) for claim in work_node.get("resource_claims") or ()
            }
            if claims & projected_claims:
                blockers.append("resource_claim")
            if any(
                prior.get("work_item_key") == work_item["work_item_key"]
                and prior.get("status")
                not in {"complete", "failed", "superseded"}
                for prior in existing.values()
            ):
                blockers.append("replacement")
            if blockers:
                actions.append(
                    KernelPreviewAction(
                        kind="would_wait",
                        node_key=node_key,
                        source_ref=f"store://admission/{blockers[0]}",
                    )
                )
                continue
            admissible.append(node_key)
            active_turns += 1
            projected_claims.update(claims)
            actions.append(
                KernelPreviewAction(
                    kind="would_admit",
                    node_key=node_key,
                    source_ref=f"plan://{node_key}",
                )
            )
        return KernelReconciliationPlan(
            plan_digest=active.plan_digest,
            actions=tuple(
                actions
                or (
                    KernelPreviewAction(
                        kind="idle",
                        node_key=None,
                        source_ref="plan://idle",
                    ),
                )
            ),
            admissible_node_keys=tuple(admissible),
        )

    def _adopt_or_materialize(
        self,
        state: dict[str, Any],
        work_node: dict[str, Any],
    ):
        prompt = self._prompt_from_state(state)
        attempt_ordinal = int(state.get("attempt_ordinal", 1))

        # Admissions committed after this repair persist an immutable profile
        # snapshot. Materialization must consume that frozen selection; it must
        # not re-resolve from mutable runtime_config or constructor injection.
        if attempt_ordinal == 1:
            frozen_profile_data = state.get("runtime_profile")
            if isinstance(frozen_profile_data, dict):
                selected_profile = self._profile_from_frozen_state(
                    state,
                    profile_key="runtime_profile",
                    digest_key="profile_digest",
                )
            elif self.runtime_profile is not None:
                # Legacy constructor-injection seam: only reachable for pre-repair
                # state or when runtime_config is None.
                selected_profile = self.runtime_profile
            elif self.runtime_config is not None:
                selected_profile = resolve_worker_profile(
                    self.runtime_config,
                    repository=state["repository"],
                    difficulty=str(work_node.get("difficulty")),
                )
            else:
                selected_profile = None
        else:
            selected_profile = self._freeze_legacy_frontier_profile(state)
        admission = RuntimeAdmission(
            repository=state["repository"],
            plan_digest=state["plan_digest"],
            node_key=work_node["node_key"],
            admission_id=state["admission_id"],
            repository_path=self.repository_path,
            base_sha=state["base_sha"],
            runtime_profile=selected_profile,
            parent_agent_id=self.parent_agent_id,
        )
        circuit_outcome = self._prepare_runtime_operation(
            state,
            "read_binding",
        )
        if circuit_outcome is not None:
            return None, circuit_outcome
        try:
            binding = self.runtime.read_binding(admission, prompt)
        except RuntimeAdapterError as error:
            return None, self._materialization_failure(
                state,
                error,
                operation="read_binding",
            )
        self._clear_runtime_operation(state, "read_binding")

        if binding is None:
            if state["admission_state"] == "materialization_ambiguous":
                state.update(
                    {
                        "status": "waiting",
                        "directive": "wait_for_runtime_readback",
                        "wait_condition": "runtime_identity_readback",
                        "wait_source_ref": (
                            f"{self.runtime.adapter_name}://"
                            f"admission/{state['admission_id']}"
                        ),
                        "wait_event_identity": "create:identity_readback",
                        "next_check_at": (
                            datetime.now(timezone.utc) + timedelta(seconds=30)
                        ).isoformat(),
                    }
                )
                self._write_state(
                    state["repository"],
                    state["plan_digest"],
                    state,
                )
                return None, self._outcome(state)
            actions = state.setdefault("materialization_actions", {})
            if int(actions.get("create", 0)) >= 3:
                blocked = RuntimeAdapterError(
                    "MATERIALIZATION_RETRIES_EXHAUSTED",
                    "three unchanged Materialization executions were exhausted",
                )
                return None, self._materialization_failure(
                    state,
                    blocked,
                    operation="create",
                )
            circuit_outcome = self._prepare_runtime_operation(
                state,
                "create",
            )
            if circuit_outcome is not None:
                return None, circuit_outcome
            actions["create"] = int(actions.get("create", 0)) + 1
            state["materialization_executions"] = sum(
                int(value) for value in actions.values()
            )
            state.update(
                {
                    "status": "running",
                    "directive": "run_again",
                    "admission_state": "materializing",
                    "wait_condition": None,
                    "wait_source_ref": None,
                    "wait_event_identity": None,
                    "next_check_at": None,
                }
            )
            self._write_state(state["repository"], state["plan_digest"], state)
            try:
                self.runtime.materialize(admission, prompt)
                self._clear_runtime_operation(state, "create")
                binding = self.runtime.read_binding(admission, prompt)
            except RuntimeAdapterError as error:
                return None, self._materialization_failure(
                    state,
                    error,
                    operation="create",
                )
            if binding is None:
                ambiguous = RuntimeAdapterError(
                    "MATERIALIZATION_READBACK_MISSING",
                    "Runtime creation acknowledgement has no identity readback",
                    failure_class="ambiguous",
                )
                return None, self._materialization_failure(
                    state,
                    ambiguous,
                    operation="create",
                )

        if (
            binding.repository != admission.repository
            or binding.plan_digest != admission.plan_digest
            or binding.node_key != admission.node_key
            or binding.admission_id != admission.admission_id
        ):
            ambiguous = RuntimeAdapterError(
                "MATERIALIZATION_READBACK_FAILED",
                "Runtime Binding did not round-trip Admission identity",
                failure_class="ambiguous",
            )
            return None, self._materialization_failure(
                state,
                ambiguous,
                operation="read_binding",
            )

        if not binding.prompt_accepted:
            actions = state.setdefault("materialization_actions", {})
            circuit_outcome = self._prepare_runtime_operation(
                state,
                "prompt",
            )
            if circuit_outcome is not None:
                return None, circuit_outcome
            if int(actions.get("prompt", 0)) == 0:
                # Record authorization of the one initial Prompt effect.
                # Later calls only reconcile its authoritative readback and
                # cannot exhaust Materialization while delivery is ambiguous.
                actions["prompt"] = 1
                state["materialization_executions"] = sum(
                    int(value) for value in actions.values()
                )
                self._write_state(
                    state["repository"],
                    state["plan_digest"],
                    state,
                )
            try:
                self.runtime.accept_prompt(binding, prompt)
                self._clear_runtime_operation(state, "prompt")
                binding = self.runtime.read_binding(admission, prompt)
            except RuntimeAdapterError as error:
                return None, self._materialization_failure(
                    state,
                    error,
                    operation="prompt",
                )
        if (
            binding is None
            or not binding.prompt_accepted
            or binding.prompt_digest != prompt.digest
        ):
            ambiguous = RuntimeAdapterError(
                "PROMPT_READBACK_FAILED",
                "Runtime did not confirm the exact frozen Prompt",
                failure_class="ambiguous",
            )
            return None, self._materialization_failure(
                state,
                ambiguous,
                operation="prompt",
            )
        state["runtime_circuit"] = None
        state["runtime_circuit_state"] = None
        state["wait_source_ref"] = None
        state["wait_event_identity"] = None
        state["next_check_at"] = None
        self._write_state(state["repository"], state["plan_digest"], state)
        return binding, None

    def _begin_or_adopt_attempt(
        self,
        state: dict[str, Any],
        binding,
    ):
        attempt_ordinal = int(state.get("attempt_ordinal", 1))
        attempt_id = (
            f"attempt:{state['plan_digest'][:12]}:"
            f"{state['node_key'][-12:]}:{attempt_ordinal}"
        )
        if binding.attempt_id not in {None, attempt_id}:
            raise KernelError(
                "ATTEMPT_IDENTITY_MISMATCH",
                "Runtime Binding belongs to another Attempt",
            )
        if state.get("attempt_id") not in {None, attempt_id}:
            raise KernelError(
                "ATTEMPT_IDENTITY_MISMATCH",
                "Store Attempt identity changed",
            )
        if state.get("attempt_id") is None:
            state.update(
                {
                    "admission_state": "consumed",
                    "attempt_id": attempt_id,
                    "attempt_state": "running",
                    "status": "running",
                    "directive": "run_again",
                    "wait_condition": None,
                    "wait_source_ref": None,
                    "wait_event_identity": None,
                    "next_check_at": None,
                }
            )
            self._commit_attempt(state, attempt_id=attempt_id)
        if binding.attempt_id is None:
            binding = self.runtime.attach_attempt(binding, attempt_id)
        if binding is None or binding.attempt_id != attempt_id:
            raise KernelError(
                "ATTEMPT_READBACK_FAILED",
                "Runtime Binding did not round-trip the Attempt identity",
            )
        return binding

    def _replace_with_frontier_admission(
        self,
        state: dict[str, Any],
        work_node: dict[str, Any],
        *,
        old_attempt_id: str,
    ) -> None:
        claims = sorted(set(work_node.get("resource_claims") or ()))
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            current = connection.execute(
                """
                SELECT state FROM v8_attempts
                WHERE attempt_id = ? AND repository = ? AND plan_digest = ?
                """,
                (
                    old_attempt_id,
                    state["repository"],
                    state["plan_digest"],
                ),
            ).fetchone()
            if current is None or current["state"] != "running":
                raise KernelError(
                    "RECOVERY_ATTEMPT_NOT_RUNNING",
                    "frontier replacement requires one running Attempt",
                )
            connection.execute(
                """
                UPDATE v8_attempts SET state = ?
                WHERE attempt_id = ? AND state = 'running'
                """,
                ("terminal", old_attempt_id),
            )
            connection.execute(
                """
                DELETE FROM v8_resource_claims
                WHERE repository = ? AND attempt_id = ?
                """,
                (state["repository"], old_attempt_id),
            )
            for claim in claims:
                occupied = connection.execute(
                    """
                    SELECT 1 FROM v8_resource_claims
                    WHERE repository = ? AND resource_key = ?
                    """,
                    (state["repository"], claim),
                ).fetchone()
                if occupied is not None:
                    raise KernelError(
                        "RESOURCE_CLAIM_UNAVAILABLE",
                        f"Recovery Resource Claim is unavailable: {claim}",
                    )
            connection.execute(
                """
                INSERT INTO v8_admissions (
                    admission_id, repository, plan_digest, node_key,
                    goal_key, state
                ) VALUES (?, ?, ?, ?, ?, 'materializing')
                """,
                (
                    state["admission_id"],
                    state["repository"],
                    state["plan_digest"],
                    state["node_key"],
                    state["goal_key"],
                ),
            )
            for claim in claims:
                connection.execute(
                    """
                    INSERT INTO v8_resource_claims (
                        repository, resource_key, admission_id, attempt_id
                    ) VALUES (?, ?, ?, NULL)
                    """,
                    (
                        state["repository"],
                        claim,
                        state["admission_id"],
                    ),
                )
            connection.execute(
                """
                UPDATE v8_node_states SET state = 'materializing'
                WHERE repository = ? AND plan_digest = ? AND node_key = ?
                """,
                (
                    state["repository"],
                    state["plan_digest"],
                    state["node_key"],
                ),
            )
            self._upsert_state(
                connection,
                repository=state["repository"],
                plan_digest=state["plan_digest"],
                rendered=self._render_state(state),
            )

    def _mark_plan_node_failed(
        self,
        state: dict[str, Any],
    ) -> None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                UPDATE v8_attempts SET state = ?
                WHERE attempt_id = ? AND state = 'running'
                """,
                (
                    "terminal",
                    state["attempt_id"],
                ),
            )
            connection.execute(
                """
                DELETE FROM v8_resource_claims
                WHERE repository = ? AND attempt_id = ?
                """,
                (state["repository"], state["attempt_id"]),
            )
            connection.execute(
                """
                UPDATE v8_node_states SET state = 'failed'
                WHERE repository = ? AND plan_digest = ? AND node_key = ?
                """,
                (
                    state["repository"],
                    state["plan_digest"],
                    state["node_key"],
                ),
            )
            self._upsert_state(
                connection,
                repository=state["repository"],
                plan_digest=state["plan_digest"],
                rendered=self._render_state(state),
            )

    def _recovery_prompt(
        self,
        work_node: dict[str, Any],
        packet: str,
        *,
        same_attempt: bool,
    ) -> RuntimePrompt:
        packet_value = json.loads(packet)
        if same_attempt:
            payload = {
                "repair_round": packet_value,
                "result_protocol": {
                    "marker": "GWO_RESULT",
                    "action_key": work_node["node_key"],
                    "instruction": (
                        "Produce a changed clean Candidate, then end with "
                        "exactly: GWO_RESULT "
                        '{"schema_version":1,"action_key":"'
                        f'{work_node["node_key"]}","candidate_sha":'
                        '"<40-hex-sha>"}. Use one compact JSON line and no '
                        "code fence."
                    ),
                    "schema_version": 1,
                },
            }
        else:
            base = RuntimePrompt.from_node(
                work_node,
                skill_catalog=self.skill_catalog,
            )
            payload = json.loads(base.text)
            payload["recovery_packet"] = packet_value
        text = canonical_bytes(payload).decode("utf-8")
        return RuntimePrompt(
            text=text,
            digest=digest_value(payload),
            authority_digest=work_node.get("contract_digest"),
            contract_node=work_node,
        )

    @staticmethod
    def _acceptance_digest(
        goal: dict[str, Any],
        work_item: dict[str, Any],
    ) -> str:
        spec_text = canonical_bytes(
            {
                "goal_acceptance": goal.get("acceptance") or [],
                "outcome_contract": ReviewConvergence.compact_review_contract(
                    work_item.get("outcome_contract") or {}
                ),
            }
        ).decode("utf-8")
        return digest_value(
            {
                "source_ref": str(work_item.get("source_ref") or ""),
                "text": spec_text,
            }
        )

    def _repair_changed_files(
        self,
        state: dict[str, Any],
        work_node: dict[str, Any],
        binding,
    ) -> list[str]:
        del work_node
        candidate_sha = state.get("candidate_sha")
        base_sha = state.get("base_sha")
        if not (
            isinstance(candidate_sha, str)
            and len(candidate_sha) == 40
            and isinstance(base_sha, str)
            and len(base_sha) == 40
        ):
            raise KernelError(
                "REPAIR_CHANGED_FILES_UNAVAILABLE",
                "exact changed-file collection requires Candidate and base SHAs",
            )
        result = subprocess.run(
            [
                "git",
                "-C",
                str(Path(binding.workspace).resolve()),
                "-c",
                "core.quotepath=false",
                "diff",
                "--name-only",
                "-z",
                f"{base_sha}...{candidate_sha}",
            ],
            capture_output=True,
        )
        if result.returncode != 0:
            detail = result.stderr.decode("utf-8", errors="replace").strip()
            raise KernelError(
                "GIT_OPERATION_FAILED",
                detail or "git changed-file collection failed",
            )
        output = result.stdout
        if output and not output.endswith(b"\0"):
            raise KernelError(
                "GIT_CHANGED_FILES_INVALID",
                "git changed-file output was not NUL-terminated",
            )
        encoded_paths = output.split(b"\0")
        encoded_paths.pop()
        if any(not path for path in encoded_paths):
            raise KernelError(
                "GIT_CHANGED_FILES_INVALID",
                "git changed-file output contained an empty path",
            )
        try:
            return [path.decode("utf-8") for path in encoded_paths]
        except UnicodeDecodeError as error:
            raise KernelError(
                "GIT_CHANGED_FILES_INVALID",
                "git changed-file output was not valid UTF-8",
            ) from error

    @staticmethod
    def _repair_causes(
        state: dict[str, Any],
        work_node: dict[str, Any],
        *,
        cause_type: str,
        findings: tuple[str, ...],
    ) -> list[dict[str, Any]]:
        causes: list[dict[str, Any]] = []
        saved_review = state.get("review_evidence")
        if isinstance(saved_review, dict):
            try:
                review_evidence = TypedEvidence(**saved_review)
            except TypeError:
                review_evidence = None
            for blocker in blocking_review_findings(review_evidence):
                causes.append(
                    {
                        "type": "review_blocker",
                        "axis": blocker["axis"],
                        "finding": blocker["finding"],
                    }
                )

        definitions = {
            str(definition.get("check_id")): definition
            for definition in (work_node.get("output_contract") or {}).get("checks")
            or ()
            if isinstance(definition, dict)
            and definition.get("hosted_only") is not True
            and definition.get("check_id")
        }
        candidate_observation = state.get("candidate_observation")
        for evidence in (
            candidate_observation.get("evidence") or ()
            if isinstance(candidate_observation, dict)
            else ()
        ):
            if (
                not isinstance(evidence, dict)
                or evidence.get("kind") != "check"
                or (evidence.get("payload") or {}).get("outcome") != "failed"
            ):
                continue
            payload = evidence.get("payload") or {}
            check_id = str(payload.get("check_id") or "")
            definition = definitions.get(check_id)
            if definition is None:
                continue
            causes.append(
                {
                    "type": "local_check_failure",
                    "check_id": check_id,
                    "suite": str(definition.get("suite") or "local"),
                    "source_ref": str(evidence.get("source_ref") or ""),
                    "exit_code": int(payload.get("exit_code") or 0),
                }
            )
        if not causes:
            causes.append(
                {
                    "type": cause_type,
                    "messages": [str(finding) for finding in findings],
                }
            )
        return causes

    def _handle_semantic_rejection(
        self,
        state: dict[str, Any],
        work_node: dict[str, Any],
        goal: dict[str, Any],
        work_item: dict[str, Any],
        binding,
        *,
        terminal_reason: str,
        findings: tuple[str, ...],
        cause_type: str = "candidate_rejection",
    ) -> ReconcileOutcome:
        self.publication.assert_writer(
            repository=state["repository"],
            writer_generation=self.writer_generation,
            plan_digest=state["plan_digest"],
            activation_id=state["activation_id"],
        )
        policy = work_node.get("recovery_policy") or {}
        ladder = RecoveryLadder(
            semantic_attempts=int(policy.get("semantic_attempts", 1)),
            repair_rounds=int(policy.get("repair_rounds", 0)),
        )
        attempt_ordinal = int(state.get("attempt_ordinal", 1))
        repair_rounds_used = int(state.get("repair_rounds_used", 0))
        directive = ladder.decide(
            terminal_reason=terminal_reason,
            attempt_ordinal=attempt_ordinal,
            repair_rounds_used=repair_rounds_used,
        )
        prior_review_context = self._review_convergence.prior_context(state)

        def recovery_packet() -> str:
            candidate_sha = state.get("candidate_sha")
            changed_files = (
                []
                if candidate_sha is None
                else self._repair_changed_files(state, work_node, binding)
            )
            return ladder.recovery_packet(
                candidate_sha=str(candidate_sha or ""),
                acceptance_digest=self._acceptance_digest(goal, work_item),
                changed_files=changed_files,
                causes=self._repair_causes(
                    state,
                    work_node,
                    cause_type=cause_type,
                    findings=findings,
                ),
            )

        if directive.action == "repair_same_attempt":
            packet = recovery_packet()
            prompt = self._recovery_prompt(
                work_node,
                packet,
                same_attempt=True,
            )
            repair_round = repair_rounds_used + 1
            repair_action_key = (
                f"repair:{state['attempt_id']}:{repair_round}:{prompt.digest}"
            )
            repair_record = {
                "round": repair_round,
                "action_key": repair_action_key,
                "prompt_digest": prompt.digest,
                "payload_digest": digest_value(json.loads(packet)),
                "delivery_state": "reserved",
            }
            existing_repair = state.get("repair_prompt")
            if (
                isinstance(existing_repair, dict)
                and existing_repair.get("action_key") == repair_action_key
            ):
                self._review_convergence.invalidate_candidate(state)
                state.update(
                    {
                        "status": "waiting",
                        "directive": "wait_for_runtime",
                        "attempt_state": "repair_delivery_ambiguous",
                        "candidate_sha": None,
                        "candidate_observation": None,
                        "wait_condition": "runtime_result",
                        "wait_source_ref": (
                            f"{self.runtime.adapter_name}://attempt/"
                            f"{state['attempt_id']}/repair/{prompt.digest}"
                        ),
                        "wait_event_identity": repair_action_key,
                        "next_check_at": (
                            datetime.now(timezone.utc) + timedelta(seconds=30)
                        ).isoformat(),
                    }
                )
                self._write_state(
                    state["repository"],
                    state["plan_digest"],
                    state,
                )
                return self._outcome(state)
            state.update(
                {
                    "status": "waiting",
                    "directive": "reconcile_again",
                    "attempt_state": "repair_dispatching",
                    "recovery_reserved_at": _now(),
                    "repair_prompt": repair_record,
                    "repair_prompt_digest": prompt.digest,
                    "repair_prompt_action_key": repair_action_key,
                    "wait_condition": "repair_prompt_delivery",
                    "wait_source_ref": (
                        f"{self.runtime.adapter_name}://attempt/"
                        f"{state['attempt_id']}/repair/{prompt.digest}"
                    ),
                    "wait_event_identity": repair_action_key,
                    "next_check_at": (
                        datetime.now(timezone.utc) + timedelta(seconds=30)
                    ).isoformat(),
                }
            )
            self._write_state(
                state["repository"],
                state["plan_digest"],
                state,
            )
            try:
                self.runtime.repair(binding, prompt)
            except RuntimeAdapterError as error:
                repair_record["delivery_state"] = "ambiguous"
                self._review_convergence.invalidate_candidate(state)
                state.update(
                    {
                        "directive": "wait_for_runtime",
                        "attempt_state": "repair_delivery_ambiguous",
                        "candidate_sha": None,
                        "candidate_observation": None,
                        "repair_prompt": repair_record,
                        "last_runtime_error": _runtime_error_record(error),
                        "wait_condition": "runtime_result",
                    }
                )
                self._write_state(
                    state["repository"],
                    state["plan_digest"],
                    state,
                )
                return self._outcome(state)
            repair_record["delivery_state"] = "accepted"
            self._review_convergence.invalidate_candidate(state)
            state.update(
                {
                    "status": "waiting",
                    "directive": "wait_for_runtime",
                    "attempt_state": "repairing",
                    "recovery_reserved_at": None,
                    "repair_rounds_used": repair_rounds_used + 1,
                    "attempt_terminal_reason": None,
                    "candidate_sha": None,
                    "publication_eligible": None,
                    "publication_state": None,
                    "publication_ref": None,
                    "hosted_check_state": None,
                    "hosted_retry_count": 0,
                    "candidate_observation": None,
                    "prior_review_context": prior_review_context,
                    "repair_prompt": repair_record,
                    "worker_parked_for_ci": False,
                    "wait_condition": "runtime_result",
                    "wait_source_ref": (
                        f"{self.runtime.adapter_name}://attempt/"
                        f"{state['attempt_id']}/repair"
                    ),
                    "wait_event_identity": (
                        repair_action_key
                    ),
                    "next_check_at": (
                        datetime.now(timezone.utc) + timedelta(seconds=30)
                    ).isoformat(),
                }
            )
            self._write_state(
                state["repository"],
                state["plan_digest"],
                state,
            )
            return self._outcome(state)
        if directive.action == "start_frontier_attempt":
            self._freeze_legacy_frontier_profile(state)
            old_attempt_id = str(state["attempt_id"])
            packet = recovery_packet()
            prompt = self._recovery_prompt(
                work_node,
                packet,
                same_attempt=False,
            )
            next_ordinal = attempt_ordinal + 1
            self._review_convergence.invalidate_candidate(state)
            state.update(
                {
                    "status": "running",
                    "directive": "run_again",
                    "admission_id": (
                        f"admission:{state['plan_digest'][:12]}:"
                        f"{state['node_key'][-12:]}:{next_ordinal}"
                    ),
                    "admission_state": "materializing",
                    "attempt_id": None,
                    "attempt_state": None,
                    "recovery_reserved_at": None,
                    "attempt_ordinal": next_ordinal,
                    "repair_rounds_used": 0,
                    "attempt_terminal_reason": None,
                    "candidate_sha": None,
                    "publication_eligible": None,
                    "publication_state": None,
                    "publication_ref": None,
                    "hosted_check_state": None,
                    "hosted_retry_count": 0,
                    "candidate_observation": None,
                    "prior_review_context": prior_review_context,
                    "worker_parked_for_ci": False,
                    "materialization_actions": {"create": 0, "prompt": 0},
                    "materialization_executions": 0,
                    "runtime_circuits": {},
                    "runtime_circuit": None,
                    "runtime_circuit_state": None,
                    "resume_sent": False,
                    "wait_condition": None,
                    "wait_source_ref": None,
                    "wait_event_identity": None,
                    "next_check_at": None,
                    "base_sha": _git(
                        self.repository_path,
                        "rev-parse",
                        self.integration_branch,
                    ),
                    "prompt_snapshot": {
                        "text": prompt.text,
                        "digest": prompt.digest,
                        "authority_digest": prompt.authority_digest,
                        "skill_name": prompt.skill_name,
                        "skill_digest": prompt.skill_digest,
                        "warnings": list(prompt.warnings),
                        "contract_node": prompt.contract_node,
                    },
                }
            )
            self._replace_with_frontier_admission(
                state,
                work_node,
                old_attempt_id=old_attempt_id,
            )
            self.runtime.retire(binding)
            return self._outcome(state)
        if directive.action == "block_runtime_unavailable":
            state.update(
                {
                    "status": "blocked",
                    "directive": "wait_for_runtime",
                    "attempt_state": "runtime_unavailable",
                    "attempt_terminal_reason": "runtime_lost",
                    "wait_condition": "runtime_available",
                    "wait_source_ref": (
                        f"{self.runtime.adapter_name}://attempt/{state['attempt_id']}"
                    ),
                    "wait_event_identity": "runtime_available",
                    "next_check_at": None,
                }
            )
            self._write_state(
                state["repository"],
                state["plan_digest"],
                state,
            )
            return self._outcome(state)
        state.update(
            {
                "status": "failed",
                "directive": "invoke_coordinator",
                "attempt_state": "terminal",
                "attempt_terminal_reason": terminal_reason,
                "work_item_state": "failed",
                "goal_state": "active",
                "wait_condition": None,
                "wait_source_ref": None,
                "wait_event_identity": None,
                "next_check_at": None,
            }
        )
        self._mark_plan_node_failed(state)
        self.runtime.retire(binding)
        return self._outcome(state)

    @staticmethod
    def _persisted_runtime_observation(
        state: dict[str, Any],
        binding,
    ):
        saved = state.get("candidate_observation")
        if not isinstance(saved, dict):
            return None
        claim_value = saved.get("result_claim")
        evidence_value = saved.get("evidence")
        if not isinstance(claim_value, dict) or not isinstance(evidence_value, list):
            raise KernelError(
                "PERSISTED_OBSERVATION_INVALID",
                "persisted Candidate observation is malformed",
            )
        claim = ResultClaim(**claim_value)
        if (
            claim.attempt_id != state.get("attempt_id")
            or claim.candidate_sha != state.get("candidate_sha")
            or claim.node_key != state.get("node_key")
        ):
            raise KernelError(
                "PERSISTED_OBSERVATION_IDENTITY_MISMATCH",
                "persisted Candidate observation changed GWO identity",
            )
        evidence = tuple(TypedEvidence(**item) for item in evidence_value)
        if any(not item.has_valid_digest() for item in evidence):
            raise KernelError(
                "PERSISTED_OBSERVATION_DIGEST_INVALID",
                "persisted Candidate Evidence failed integrity validation",
            )
        workspace = Path(binding.workspace).resolve()
        if _git(workspace, "rev-parse", "HEAD") != claim.candidate_sha or _git(
            workspace,
            "status",
            "--porcelain=v1",
            "--untracked-files=all",
        ):
            raise KernelError(
                "PERSISTED_CANDIDATE_READBACK_FAILED",
                "persisted Candidate no longer matches the clean Runtime workspace",
            )
        return RuntimeObservation(
            binding=binding,
            lifecycle=str(saved.get("lifecycle") or "completed"),
            result_claim=claim,
            evidence=evidence,
        )

    @staticmethod
    def _persist_runtime_observation(
        state: dict[str, Any],
        observation,
    ) -> None:
        if observation.result_claim is None:
            return
        state["candidate_observation"] = {
            "lifecycle": observation.lifecycle,
            "binding": asdict(observation.binding),
            "result_claim": asdict(observation.result_claim),
            "evidence": [asdict(item) for item in observation.evidence],
        }

    def _verify_pre_review_checks(
        self,
        work_node: dict[str, Any],
        observation,
    ):
        output_contract = work_node.get("output_contract") or {}
        affected = [
            check
            for check in output_contract.get("checks") or ()
            if isinstance(check, dict)
            and check.get("hosted_only") is not True
            and check.get("suite") == "affected"
        ]
        contract = {
            "required_evidence": [
                {"kind": "candidate"},
                *(
                    {"kind": "check", "check_id": check["check_id"]}
                    for check in affected
                ),
            ],
            "checks": list(output_contract.get("checks") or ()),
            "review_requirement": {
                "mode": "none",
                "axes": [],
                "specialist_requirements": [],
                "human_decision_required": False,
            },
        }
        return self.verifier.verify(
            observation.result_claim,
            contract,
            observation,
        )

    @staticmethod
    def _batch_hosted_definitions(
        units: tuple[
            tuple[
                dict[str, Any],
                dict[str, Any],
                dict[str, Any],
                dict[str, Any],
            ],
            ...,
        ],
        member_node_keys: set[str],
    ) -> tuple[dict[str, Any], ...]:
        by_name: dict[str, list[dict[str, Any]]] = {}
        for work_node, _integration, _goal, _work_item in units:
            if work_node["node_key"] not in member_node_keys:
                continue
            for check in (work_node.get("output_contract") or {}).get("checks") or ():
                if not isinstance(check, dict) or check.get("hosted_only") is not True:
                    continue
                hosted_name = str(check["hosted_name"])
                by_name.setdefault(hosted_name, []).append(check)
        return tuple(
            {
                "check_id": f"integration-batch:{hosted_name}",
                "hosted_name": hosted_name,
                "definition_digest": digest_value(
                    {
                        "hosted_name": hosted_name,
                        "member_definition_digests": sorted(
                            str(check["definition_digest"]) for check in checks
                        ),
                    }
                ),
                "hosted_only": True,
                "suite": "hosted",
            }
            for hosted_name, checks in sorted(by_name.items())
        )

    def _write_batch_members(
        self,
        repository: str,
        plan_digest: str,
        states: dict[str, dict[str, Any]],
    ) -> None:
        for state in states.values():
            self._write_state(repository, plan_digest, state)

    def _advance_integration_batch(
        self,
        repository: str,
        *,
        active: Any,
        units: tuple[
            tuple[
                dict[str, Any],
                dict[str, Any],
                dict[str, Any],
                dict[str, Any],
            ],
            ...,
        ],
    ) -> None:
        all_states = {
            str(state["node_key"]): state
            for state in self._read_states(repository, active.plan_digest)
        }
        in_flight_ids = {
            str(state["integration_batch_id"])
            for state in all_states.values()
            if state.get("attempt_state") == "batch_wait"
            and isinstance(state.get("integration_batch_id"), str)
        }
        if len(in_flight_ids) > 1:
            raise KernelError(
                "INTEGRATION_BATCH_CONCURRENCY_INVALID",
                "one Plan Revision cannot have multiple active Integration Batches",
            )
        batch_state: dict[str, Any] | None = None
        member_states: dict[str, dict[str, Any]]
        hosted_definitions: tuple[dict[str, Any], ...]
        if in_flight_ids:
            batch_id = next(iter(in_flight_ids))
            batch_state = self._read_integration_batch(
                repository,
                active.plan_digest,
                batch_id,
            )
            if batch_state is None:
                raise KernelError(
                    "INTEGRATION_BATCH_STATE_MISSING",
                    "member state references an absent Integration Batch",
                )
            member_node_keys = set(batch_state["member_node_keys"])
            member_states = {
                node_key: all_states[node_key]
                for node_key in member_node_keys
                if node_key in all_states
            }
            if len(member_states) != len(member_node_keys):
                raise KernelError(
                    "INTEGRATION_BATCH_MEMBER_MISSING",
                    "Integration Batch member state is absent",
                )
            hosted_definitions = tuple(batch_state["hosted_definitions"])
        else:
            ready = {
                node_key: state
                for node_key, state in all_states.items()
                if state.get("attempt_state") == "batch_ready"
            }
            if not ready:
                return
            if any(self._state_holds_worker_turn(state) for state in all_states.values()):
                return
            refreshed_plan = self.plan_reconciliation(repository)
            if any(
                node_key not in all_states
                for node_key in refreshed_plan.admissible_node_keys
            ):
                return
            integration_by_work = {
                str(work_node["node_key"]): integration
                for work_node, integration, _goal, _work_item in units
            }
            members = tuple(
                IntegrationBatchMember(
                    node_key=node_key,
                    integration_node_key=str(
                        integration_by_work[node_key]["node_key"]
                    ),
                    candidate_sha=str(state["candidate_sha"]),
                    base_sha=str(state["base_sha"]),
                    result_digest=str(state["result_digest"]),
                    evidence_manifest_digest=str(
                        state["candidate_evidence_manifest_digest"]
                    ),
                )
                for node_key, state in sorted(ready.items())
            )
            try:
                batch = GitIntegrationBatchAssembler(
                    self.repository_path
                ).prepare(
                    plan_digest=active.plan_digest,
                    members=members,
                )
            except IntegrationBatchError as error:
                for state in ready.values():
                    state.update(
                        {
                            "status": "blocked",
                            "directive": "invoke_coordinator",
                            "attempt_state": "integration_batch_blocked",
                            "wait_condition": None,
                            "last_integration_batch_error": {
                                "code": error.code,
                            },
                        }
                    )
                self._write_batch_members(
                    repository,
                    active.plan_digest,
                    ready,
                )
                return
            batch_id = batch.batch_id
            member_node_keys = {member.node_key for member in batch.members}
            hosted_definitions = self._batch_hosted_definitions(
                units,
                member_node_keys,
            )
            batch_state = {
                "repository": repository,
                "plan_digest": active.plan_digest,
                "batch_id": batch.batch_id,
                "base_sha": batch.base_sha,
                "batch_sha": batch.batch_sha,
                "member_node_keys": [
                    member.node_key for member in batch.members
                ],
                "candidate_shas": [
                    member.candidate_sha for member in batch.members
                ],
                "candidate_evidence_manifest_digests": [
                    member.evidence_manifest_digest for member in batch.members
                ],
                "hosted_definitions": list(hosted_definitions),
                "state": "prepared",
                "publication_state": None,
                "publication_ref": None,
                "hosted_check_state": None,
                "hosted_retry_count": 0,
            }
            self._write_integration_batch(
                repository,
                active.plan_digest,
                batch_id,
                batch_state,
            )
            member_states = ready
            for state in member_states.values():
                state.update(
                    {
                        "status": "waiting",
                        "directive": "reconcile_again",
                        "attempt_state": "batch_wait",
                        "wait_condition": "integration_batch",
                        "wait_source_ref": batch.source_ref,
                        "wait_event_identity": f"integration-batch:{batch_id}",
                        "next_check_at": (
                            datetime.now(timezone.utc) + timedelta(seconds=1)
                        ).isoformat(),
                        "integration_batch_id": batch_id,
                        "integration_batch_sha": batch.batch_sha,
                    }
                )
            self._write_batch_members(
                repository,
                active.plan_digest,
                member_states,
            )

        assert batch_state is not None
        batch_id = str(batch_state["batch_id"])
        batch_sha = str(batch_state["batch_sha"])
        delivery_required = bool(hosted_definitions) or any(
            bool((work_node.get("output_contract") or {}).get("delivery_required"))
            for work_node, _integration, _goal, _work_item in units
            if work_node["node_key"] in member_states
        )
        if delivery_required and self.delivery_control is None:
            for state in member_states.values():
                state.update(
                    {
                        "status": "blocked",
                        "directive": "request_decision",
                        "attempt_state": "delivery_control_missing",
                        "wait_condition": None,
                    }
                )
            batch_state["state"] = "blocked"
            batch_state["last_delivery_error"] = {
                "code": "DELIVERY_CONTROL_MISSING"
            }
            self._write_integration_batch(
                repository,
                active.plan_digest,
                batch_id,
                batch_state,
            )
            self._write_batch_members(
                repository,
                active.plan_digest,
                member_states,
            )
            return

        publication_ref = None
        if self.delivery_control is not None:
            manifest_digest = digest_value(
                {
                    "batch_id": batch_id,
                    "batch_sha": batch_sha,
                    "candidate_shas": batch_state["candidate_shas"],
                    "candidate_evidence_manifest_digests": (
                        batch_state["candidate_evidence_manifest_digests"]
                    ),
                    "hosted_definition_digests": [
                        definition["definition_digest"]
                        for definition in hosted_definitions
                    ],
                }
            )
            try:
                receipt = self.delivery_control.publish_once(
                    repository,
                    batch_sha,
                    manifest_digest,
                    target_branch=self.integration_branch,
                )
                if (
                    receipt.candidate_sha != batch_sha
                    or receipt.evidence_manifest_digest != manifest_digest
                ):
                    raise DeliveryControlError(
                        "PUBLICATION_READBACK_FAILED",
                        "Integration Batch publication changed exact identity",
                    )
            except DeliveryControlError as error:
                readback_ambiguous = error.code in {
                    "PULL_REQUEST_CREATE_AMBIGUOUS",
                    "PULL_REQUEST_READBACK_AMBIGUOUS",
                }
                batch_state["state"] = (
                    "waiting" if readback_ambiguous else "blocked"
                )
                batch_state["last_delivery_error"] = {"code": error.code}
                for state in member_states.values():
                    if readback_ambiguous:
                        state.update(
                            {
                                "status": "waiting",
                                "directive": "reconcile_again",
                                "attempt_state": "batch_wait",
                                "wait_condition": "publication_readback",
                                "wait_source_ref": (
                                    f"delivery://publication/{batch_sha}"
                                ),
                                "wait_event_identity": (
                                    f"publication-readback:{batch_sha}"
                                ),
                                "next_check_at": (
                                    datetime.now(timezone.utc)
                                    + timedelta(seconds=30)
                                ).isoformat(),
                                "last_delivery_error": {"code": error.code},
                            }
                        )
                    else:
                        state.update(
                            {
                                "status": "blocked",
                                "directive": "request_decision",
                                "attempt_state": "publication_blocked",
                                "wait_condition": None,
                                "wait_source_ref": None,
                                "wait_event_identity": None,
                                "next_check_at": None,
                                "last_delivery_error": {"code": error.code},
                            }
                        )
                self._write_integration_batch(
                    repository,
                    active.plan_digest,
                    batch_id,
                    batch_state,
                )
                self._write_batch_members(
                    repository,
                    active.plan_digest,
                    member_states,
                )
                return
            publication_ref = receipt.source_ref
            batch_state.update(
                {
                    "state": "published",
                    "publication_state": "published",
                    "publication_ref": publication_ref,
                    "evidence_manifest_digest": manifest_digest,
                }
            )
            for state in member_states.values():
                state.update(
                    {
                        "publication_state": "published",
                        "publication_ref": publication_ref,
                    }
                )

        hosted_evidence: tuple[TypedEvidence, ...] = ()
        if hosted_definitions:
            assert self.delivery_control is not None
            try:
                hosted = self.delivery_control.read_hosted_checks(
                    repository,
                    batch_sha,
                    hosted_definitions,
                )
            except DeliveryControlError as error:
                batch_state.update(
                    {
                        "state": "waiting",
                        "hosted_check_state": "unavailable",
                        "last_delivery_error": {"code": error.code},
                    }
                )
                for state in member_states.values():
                    state.update(
                        {
                            "status": "waiting",
                            "directive": "wait_for_hosted_ci",
                            "wait_condition": "hosted_ci",
                            "wait_source_ref": publication_ref,
                            "wait_event_identity": f"hosted-ci:{batch_sha}",
                            "next_check_at": (
                                datetime.now(timezone.utc)
                                + timedelta(seconds=30)
                            ).isoformat(),
                            "hosted_check_state": "unavailable",
                        }
                    )
                self._write_integration_batch(
                    repository,
                    active.plan_digest,
                    batch_id,
                    batch_state,
                )
                self._write_batch_members(
                    repository,
                    active.plan_digest,
                    member_states,
                )
                return
            expected_digests = tuple(
                sorted(
                    str(definition["definition_digest"])
                    for definition in hosted_definitions
                )
            )
            if (
                hosted.candidate_sha != batch_sha
                or hosted.definition_digests != expected_digests
            ):
                raise KernelError(
                    "INTEGRATION_BATCH_HOSTED_IDENTITY_MISMATCH",
                    "hosted Check readback changed Batch identity or definitions",
                )
            batch_state["hosted_check_state"] = hosted.status
            if hosted.status == "pending":
                for state in member_states.values():
                    state.update(
                        {
                            "status": "waiting",
                            "directive": "wait_for_hosted_ci",
                            "wait_condition": "hosted_ci",
                            "wait_source_ref": hosted.source_ref,
                            "wait_event_identity": f"hosted-ci:{batch_sha}",
                            "next_check_at": (
                                datetime.now(timezone.utc)
                                + timedelta(seconds=30)
                            ).isoformat(),
                            "hosted_check_state": "pending",
                        }
                    )
                batch_state["state"] = "waiting"
                self._write_integration_batch(
                    repository,
                    active.plan_digest,
                    batch_id,
                    batch_state,
                )
                self._write_batch_members(
                    repository,
                    active.plan_digest,
                    member_states,
                )
                return
            if hosted.status == "cancelled":
                batch_state["state"] = "waiting"
                for state in member_states.values():
                    state.update(
                        {
                            "status": "waiting",
                            "directive": "request_decision",
                            "attempt_state": "batch_wait",
                            "wait_condition": "hosted_ci_cancelled",
                            "wait_source_ref": hosted.source_ref,
                            "wait_event_identity": (
                                f"hosted-ci-cancelled:{batch_sha}"
                            ),
                            "next_check_at": None,
                            "hosted_check_state": "cancelled",
                        }
                    )
                self._write_integration_batch(
                    repository,
                    active.plan_digest,
                    batch_id,
                    batch_state,
                )
                self._write_batch_members(
                    repository,
                    active.plan_digest,
                    member_states,
                )
                return
            if hosted.status == "infrastructure_failure":
                retries = int(batch_state.get("hosted_retry_count", 0))
                if retries < 2:
                    self.delivery_control.retry_hosted_checks(
                        repository,
                        batch_sha,
                    )
                    batch_state["hosted_retry_count"] = retries + 1
                    batch_state["state"] = "waiting"
                    for state in member_states.values():
                        state.update(
                            {
                                "status": "waiting",
                                "directive": "wait_for_hosted_ci",
                                "wait_condition": "hosted_ci",
                                "wait_source_ref": hosted.source_ref,
                                "wait_event_identity": f"hosted-ci:{batch_sha}",
                                "next_check_at": (
                                    datetime.now(timezone.utc)
                                    + timedelta(seconds=30)
                                ).isoformat(),
                                "hosted_check_state": (
                                    "infrastructure_failure"
                                ),
                                "hosted_retry_count": retries + 1,
                            }
                        )
                    self._write_integration_batch(
                        repository,
                        active.plan_digest,
                        batch_id,
                        batch_state,
                    )
                    self._write_batch_members(
                        repository,
                        active.plan_digest,
                        member_states,
                    )
                    return
            if hosted.status != "passed":
                batch_state["state"] = "blocked"
                for state in member_states.values():
                    state.update(
                        {
                            "status": "blocked",
                            "directive": "invoke_coordinator",
                            "attempt_state": "integration_batch_failed",
                            "wait_condition": None,
                            "wait_source_ref": hosted.source_ref,
                            "hosted_check_state": hosted.status,
                        }
                    )
                self._write_integration_batch(
                    repository,
                    active.plan_digest,
                    batch_id,
                    batch_state,
                )
                self._write_batch_members(
                    repository,
                    active.plan_digest,
                    member_states,
                )
                return
            hosted_evidence = tuple(
                TypedEvidence._capture(
                    kind="check",
                    subject=batch_sha,
                    observer_type="github",
                    observer_id=self.delivery_control.__class__.__name__,
                    observed_at=_now(),
                    source_ref=hosted.source_ref,
                    payload={
                        "check_id": definition["check_id"],
                        "definition_digest": definition["definition_digest"],
                        "hosted_name": definition["hosted_name"],
                        "candidate_sha": batch_sha,
                        "outcome": "passed",
                        "integration_batch_id": batch_id,
                    },
                )
                for definition in hosted_definitions
            )

        lease_holder = f"integration-batch:{batch_id}"
        self.publication.assert_new_work(
            repository,
            writer_generation=self.writer_generation,
            activation_id=active.activation_id,
        )
        try:
            self._acquire_integration_lease(
                repository,
                lease_holder,
                activation_id=active.activation_id,
            )
        except KernelError as error:
            if error.code != "INTEGRATION_LEASE_UNAVAILABLE":
                raise
            for state in member_states.values():
                state.update(
                    {
                        "status": "waiting",
                        "directive": "wait_for_integration",
                        "wait_condition": "integration_lease",
                        "next_check_at": (
                            datetime.now(timezone.utc) + timedelta(seconds=5)
                        ).isoformat(),
                    }
                )
            self._write_batch_members(
                repository,
                active.plan_digest,
                member_states,
            )
            return
        try:
            current_branch = _git(
                self.repository_path,
                "rev-parse",
                "--abbrev-ref",
                "HEAD",
            )
            current_head = _git(self.repository_path, "rev-parse", "HEAD")
            if current_branch != self.integration_branch:
                raise KernelError(
                    "INTEGRATION_BRANCH_MISMATCH",
                    "repository is not on the configured Integration branch",
                )
            if current_head not in {batch_state["base_sha"], batch_sha}:
                for state in member_states.values():
                    state.update(
                        {
                            "status": "blocked",
                            "directive": "invoke_coordinator",
                            "attempt_state": "integration_refresh_required",
                            "wait_condition": "integration_refresh",
                            "integration_target_head": current_head,
                        }
                    )
                batch_state["state"] = "blocked"
                self._write_integration_batch(
                    repository,
                    active.plan_digest,
                    batch_id,
                    batch_state,
                )
                self._write_batch_members(
                    repository,
                    active.plan_digest,
                    member_states,
                )
                return
            remote_integration = None
            if self.delivery_control is not None:
                remote_integration = self.delivery_control.integrate_serially(
                    repository,
                    batch_sha,
                    self.integration_branch,
                )
                if (
                    remote_integration.candidate_sha != batch_sha
                    or remote_integration.target_branch != self.integration_branch
                ):
                    raise KernelError(
                        "REMOTE_INTEGRATION_READBACK_FAILED",
                        "remote Integration changed Batch SHA or target branch",
                    )
            if current_head != batch_sha:
                _git(self.repository_path, "merge", "--ff-only", batch_sha)
            integrated_sha = _git(self.repository_path, "rev-parse", "HEAD")
            if integrated_sha != batch_sha:
                raise KernelError(
                    "INTEGRATION_READBACK_FAILED",
                    "Integration branch did not reach the Batch SHA",
                )
            for state in member_states.values():
                integration_evidence = TypedEvidence._capture(
                    kind="integration",
                    subject=integrated_sha,
                    observer_type="kernel",
                    observer_id=self.writer_generation,
                    observed_at=_now(),
                    source_ref=(
                        f"git://{repository}/{self.integration_branch}"
                        if remote_integration is None
                        else remote_integration.source_ref
                    ),
                    payload={
                        "integration_batch_id": batch_id,
                        "batch_sha": integrated_sha,
                        "candidate_sha": state["candidate_sha"],
                        "member_node_keys": batch_state["member_node_keys"],
                        "branch": self.integration_branch,
                    },
                )
                state.update(
                    {
                        "status": "complete",
                        "directive": "goal_complete",
                        "goal_state": "completed",
                        "work_item_state": "integrated",
                        "attempt_state": "verified",
                        "wait_condition": None,
                        "wait_source_ref": None,
                        "wait_event_identity": None,
                        "next_check_at": None,
                        "hosted_check_state": (
                            "passed" if hosted_definitions else None
                        ),
                        "integration_batch_hosted_check_evidence": [
                            asdict(item) for item in hosted_evidence
                        ],
                        "integrated_sha": integrated_sha,
                        "integration_evidence_digest": (
                            integration_evidence.content_digest
                        ),
                        "integration_evidence": asdict(integration_evidence),
                    }
                )
                self._record_verified_result(
                    state,
                    candidate_sha=str(state["candidate_sha"]),
                    result_digest=str(state["result_digest"]),
                )
            batch_state.update(
                {
                    "state": "integrated",
                    "integrated_sha": integrated_sha,
                    "integration_source_ref": (
                        f"git://{repository}/{self.integration_branch}"
                        if remote_integration is None
                        else remote_integration.source_ref
                    ),
                }
            )
            self._write_integration_batch(
                repository,
                active.plan_digest,
                batch_id,
                batch_state,
            )
            self._write_batch_members(
                repository,
                active.plan_digest,
                member_states,
            )
        finally:
            self._release_integration_lease(repository, lease_holder)

    def _reconcile_work_unit(
        self,
        repository: str,
        *,
        active: Any,
        plan: dict[str, Any],
        work_node: dict[str, Any],
        integration_node: dict[str, Any],
        goal: dict[str, Any],
        work_item: dict[str, Any],
        worker_turn_capacity: int,
    ) -> ReconcileOutcome:
        state = self._read_state(
            repository,
            active.plan_digest,
            work_node["node_key"],
        )
        if state is not None and state.get("attempt_state") in {
            "batch_ready",
            "batch_wait",
        }:
            return self._outcome(state)
        if state is not None and (
            state.get("status") in {"complete", "failed", "superseded"}
            or (
                state.get("status") == "blocked" and state.get("wait_condition") is None
            )
            or state.get("admission_state") == "materialization_blocked"
        ):
            return self._outcome(state)
        if state is None:
            state = self._initial_state(
                repository=repository,
                plan_digest=active.plan_digest,
                goal=goal,
                work_item=work_item,
                work_node=work_node,
            )
            state["activation_id"] = active.activation_id
            if self._adopt_verified_result(state, work_node):
                return self._outcome(state)
            self._commit_admission(
                state,
                work_node,
                activation_id=active.activation_id,
                worker_turn_capacity=worker_turn_capacity,
                dependency_keys=tuple(
                    str(edge["from_node"])
                    for edge in plan.get("edges") or ()
                    if isinstance(edge, dict)
                    and edge.get("to_node") == work_node["node_key"]
                    and isinstance(edge.get("from_node"), str)
                ),
            )
        elif (
            state.get("repository") != repository
            or state.get("plan_digest") != active.plan_digest
            or state.get("node_key") != work_node["node_key"]
        ):
            raise KernelError(
                "EXECUTION_STATE_IDENTITY_MISMATCH",
                "Store execution state does not match active Plan",
            )

        self.publication.assert_writer(
            repository=repository,
            writer_generation=self.writer_generation,
            plan_digest=active.plan_digest,
            activation_id=active.activation_id,
        )
        binding, terminal = self._adopt_or_materialize(state, work_node)
        if terminal is not None:
            return terminal
        assert binding is not None
        binding = self._begin_or_adopt_attempt(state, binding)

        try:
            observation = self._persisted_runtime_observation(state, binding)
            if observation is None:
                review_mode = (
                    (work_node.get("output_contract") or {})
                    .get("review_requirement", {})
                    .get("mode", "none")
                )
                defer_checks = getattr(
                    self.runtime,
                    "defer_repository_checks",
                    None,
                )
                if review_mode != "none" and callable(defer_checks):
                    defer_checks(binding)
                observation = self.runtime.observe(binding)
            if (
                observation.result_claim is None
                and observation.lifecycle in {"idle", "ready"}
                and not state.get("resume_sent")
            ):
                state["resume_sent"] = True
                self._write_state(repository, active.plan_digest, state)
                self.runtime.resume(binding)
                observation = self.runtime.observe(binding)
            state["runtime_observation_failures"] = 0
        except RuntimeAdapterError as error:
            if error.code in {
                "PASEO_RESULT_INVALID",
                "PASEO_NO_RESULT_INVALID",
                "PASEO_RESULT_READBACK_FAILED",
            }:
                return self._handle_semantic_rejection(
                    state,
                    work_node,
                    goal,
                    work_item,
                    binding,
                    terminal_reason="no_result",
                    findings=(f"{error.code}: {error.detail}",),
                    cause_type="runtime_result_failure",
                )
            if error.failure_class == "permanent":
                state.update(
                    {
                        "status": "blocked",
                        "directive": "request_decision",
                        "attempt_state": "runtime_configuration_blocked",
                        "wait_condition": None,
                        "last_runtime_error": _runtime_error_record(error),
                    }
                )
                self._write_state(repository, active.plan_digest, state)
                return self._outcome(state)
            failures = int(state.get("runtime_observation_failures", 0)) + 1
            state["runtime_observation_failures"] = failures
            if failures >= 3:
                return self._handle_semantic_rejection(
                    state,
                    work_node,
                    goal,
                    work_item,
                    binding,
                    terminal_reason="runtime_lost",
                    findings=(f"{error.code}: {error.detail}",),
                    cause_type="runtime_infrastructure_failure",
                )
            state.update(
                {
                    "status": "waiting",
                    "directive": "wait_for_runtime",
                    "attempt_state": "running",
                    "wait_condition": "runtime_observation",
                    "wait_source_ref": (
                        f"{self.runtime.adapter_name}://attempt/{state['attempt_id']}"
                    ),
                    "wait_event_identity": "runtime_observation",
                    "next_check_at": (
                        datetime.now(timezone.utc) + timedelta(seconds=30)
                    ).isoformat(),
                    "last_runtime_error": _runtime_error_record(error),
                }
            )
            self._write_state(repository, active.plan_digest, state)
            return self._outcome(state)

        if observation.result_claim is None:
            if observation.terminal_reason == "no_result":
                return self._handle_semantic_rejection(
                    state,
                    work_node,
                    goal,
                    work_item,
                    binding,
                    terminal_reason="no_result",
                    findings=(
                        observation.terminal_detail
                        or "Runtime returned a typed no_result",
                    ),
                    cause_type="runtime_no_result",
                )
            if state.get("resume_sent") and observation.lifecycle in {
                "idle",
                "ready",
                "completed",
            }:
                return self._handle_semantic_rejection(
                    state,
                    work_node,
                    goal,
                    work_item,
                    binding,
                    terminal_reason="no_result",
                    findings=(
                        "runtime reached a terminal lifecycle without a Result Claim",
                    ),
                    cause_type="runtime_no_result",
                )
            state.update(
                {
                    "status": "waiting",
                    "directive": "wait_for_runtime",
                    "attempt_state": "running",
                    "wait_condition": "runtime_result",
                    "wait_source_ref": (
                        f"{self.runtime.adapter_name}://attempt/{state['attempt_id']}"
                    ),
                    "wait_event_identity": "runtime_result",
                    "next_check_at": (
                        datetime.now(timezone.utc) + timedelta(seconds=30)
                    ).isoformat(),
                }
            )
            self._write_state(repository, active.plan_digest, state)
            return self._outcome(state)

        prior_context = state.get("prior_review_context") or {}
        if (
            int(state.get("repair_rounds_used", 0)) > 0
            and prior_context.get("candidate_sha")
            == observation.result_claim.candidate_sha
        ):
            return self._handle_semantic_rejection(
                state,
                work_node,
                goal,
                work_item,
                binding,
                terminal_reason="no_result",
                findings=("Repair Round returned the unchanged rejected Candidate",),
                cause_type="repair_no_change",
            )
        state.update(
            {
                "candidate_sha": observation.result_claim.candidate_sha,
                "attempt_state": "result_submitted",
                "wait_condition": None,
                "wait_source_ref": None,
                "wait_event_identity": None,
                "next_check_at": None,
            }
        )
        self._persist_runtime_observation(state, observation)
        self._write_state(repository, active.plan_digest, state)
        review_mode = (
            (work_node.get("output_contract") or {})
            .get("review_requirement", {})
            .get("mode", "none")
        )
        if review_mode != "none":
            pre_review = self._verify_pre_review_checks(
                work_node,
                observation,
            )
            if pre_review.status != "accepted":
                return self._handle_semantic_rejection(
                    state,
                    work_node,
                    goal,
                    work_item,
                    binding,
                    terminal_reason="rejected",
                    findings=(
                        *pre_review.findings,
                        *(
                            f"missing pre-Review Evidence: {item}"
                            for item in pre_review.missing_evidence
                        ),
                    ),
                    cause_type="local_pre_review_failure",
                )
        try:
            review_decision = self._review_convergence.converge(
                state,
                work_node,
                goal,
                work_item,
                binding,
                observation,
            )
        except ReviewConvergenceError as error:
            raise KernelError(error.code, error.detail) from error
        observation = review_decision.observation
        if review_decision.capture_deferred_checks:
            capture_checks = getattr(
                self.runtime,
                "capture_deferred_checks",
                None,
            )
            if callable(capture_checks):
                observation = capture_checks(binding, observation)
                self._persist_runtime_observation(state, observation)
                self._write_state(repository, active.plan_digest, state)
        if review_decision.status == "rejected":
            return self._handle_semantic_rejection(
                state,
                work_node,
                goal,
                work_item,
                binding,
                terminal_reason="rejected",
                findings=review_decision.findings,
                cause_type="candidate_verification_failure",
            )
        if review_decision.status != "accepted":
            return self._outcome(state)
        decision = self.verifier.verify(
            observation.result_claim,
            work_node["output_contract"],
            observation,
        )
        if decision.status != "accepted" or decision.result is None:
            if decision.status == "rejected":
                return self._handle_semantic_rejection(
                    state,
                    work_node,
                    goal,
                    work_item,
                    binding,
                    terminal_reason="rejected",
                    findings=decision.findings,
                    cause_type="candidate_verification_failure",
                )
            state.update(
                {
                    "status": decision.status,
                    "attempt_state": (
                        "result_submitted"
                        if decision.status == "waiting"
                        else "candidate_rejected"
                    ),
                    "directive": (
                        "wait_for_evidence"
                        if decision.status == "waiting"
                        else "invoke_coordinator"
                    ),
                    "wait_condition": (
                        "evidence_source" if decision.status == "waiting" else None
                    ),
                    "wait_source_ref": (
                        f"{self.runtime.adapter_name}://"
                        f"attempt/{state['attempt_id']}/evidence"
                        if decision.status == "waiting"
                        else None
                    ),
                    "wait_event_identity": (
                        f"evidence:{observation.result_claim.candidate_sha}"
                        if decision.status == "waiting"
                        else None
                    ),
                    "next_check_at": (
                        (datetime.now(timezone.utc) + timedelta(seconds=30)).isoformat()
                        if decision.status == "waiting"
                        else None
                    ),
                }
            )
            self._write_state(repository, active.plan_digest, state)
            return self._outcome(state)

        eligibility = self.verifier.publication_eligibility(
            observation.result_claim,
            work_node["output_contract"],
            observation,
        )
        if not eligibility.eligible:
            raise KernelError(
                "INTEGRATION_BATCH_NOT_ELIGIBLE",
                "Candidate cannot enter an Integration Batch before local acceptance",
            )
        state["result_digest"] = decision.result.result_digest
        state["publication_eligible"] = True
        state["candidate_evidence_manifest_digest"] = digest_value(
            {
                "candidate_sha": observation.result_claim.candidate_sha,
                "check_evidence_digests": list(
                    eligibility.check_evidence_digests
                ),
                "review_evidence_digest": eligibility.review_evidence_digest,
            }
        )
        state.update(
            {
                "status": "waiting",
                "directive": "reconcile_again",
                "attempt_state": "batch_ready",
                "wait_condition": "integration_batch",
                "wait_source_ref": (
                    f"git://{repository}/{observation.result_claim.candidate_sha}"
                ),
                "wait_event_identity": "integration-batch-frontier",
                "next_check_at": (
                    datetime.now(timezone.utc) + timedelta(seconds=1)
                ).isoformat(),
            }
        )
        self._release_attempt_claims(state)
        self.runtime.retire(binding)
        self._write_state(repository, active.plan_digest, state)
        return self._outcome(state)

    def reconcile_once(self, repository: str) -> ReconcileOutcome:
        with self.publication.pin_durable_activation(repository):
            return self._reconcile_once(repository)

    def _reconcile_once(self, repository: str) -> ReconcileOutcome:
        planned = self.plan_reconciliation(repository)
        active = self.publication.read_active(repository)
        if active is None:
            raise KernelError(
                "PLAN_NOT_ACTIVE",
                "repository has no active Plan Revision",
            )
        if active.writer_generation != self.writer_generation:
            raise KernelError(
                "WRITER_GENERATION_MISMATCH",
                "Kernel does not own the active writer generation",
            )
        plan = self._validate_plan(repository, active.canonical_bytes)
        units = self._work_units(plan)
        worker_capacity, coordinator_capacity = self._turn_capacities(repository)
        existing = {
            str(state["node_key"]): state
            for state in self._read_states(repository, active.plan_digest)
            if isinstance(state.get("node_key"), str)
        }
        self._reacquire_waiting_worker_turns(
            existing,
            worker_turn_capacity=worker_capacity,
        )
        active_turns = sum(
            1 for state in existing.values() if self._state_holds_worker_turn(state)
        )
        available = max(0, worker_capacity - active_turns)
        admissible = set(planned.admissible_node_keys)
        planned_actions = {
            action.node_key: action
            for action in planned.actions
            if action.node_key is not None
        }
        admitted_node_keys: list[str] = []
        compatible_units = []
        held_error: KernelError | None = None
        deferrable = {
            "ADMISSION_DEPENDENCY_UNSATISFIED",
            "GOAL_ON_REPLAN_HOLD",
            "REPLACEMENT_PREDECESSOR_ACTIVE",
            "RESOURCE_CLAIM_UNAVAILABLE",
            "WORKER_CAPACITY_UNAVAILABLE",
        }
        for work_node, integration_node, goal, work_item in units:
            node_key = str(work_node["node_key"])
            state = existing.get(node_key)
            if state is None:
                if node_key not in admissible or available <= 0:
                    action = planned_actions.get(node_key)
                    if (
                        action is not None
                        and action.source_ref == "store://admission/goal_hold"
                    ):
                        held_error = KernelError(
                            "GOAL_ON_REPLAN_HOLD",
                            "Goal has an active Replan Hold",
                        )
                    continue
                state = self._initial_state(
                    repository=repository,
                    plan_digest=active.plan_digest,
                    goal=goal,
                    work_item=work_item,
                    work_node=work_node,
                )
                state["activation_id"] = active.activation_id
                if not self._adopt_verified_result(state, work_node):
                    try:
                        self._commit_admission(
                            state,
                            work_node,
                            activation_id=active.activation_id,
                            worker_turn_capacity=worker_capacity,
                            dependency_keys=tuple(
                                str(edge["from_node"])
                                for edge in plan.get("edges") or ()
                                if isinstance(edge, dict)
                                and edge.get("to_node") == node_key
                                and isinstance(edge.get("from_node"), str)
                            ),
                        )
                    except KernelError as error:
                        if error.code in deferrable:
                            if error.code == "GOAL_ON_REPLAN_HOLD":
                                held_error = error
                            continue
                        raise
                    admitted_node_keys.append(node_key)
                    available -= 1
                existing[node_key] = state
            compatible_units.append((work_node, integration_node, goal, work_item))

        if not compatible_units and held_error is not None:
            raise held_error

        admitted = set(admitted_node_keys)
        self._materialize_admitted_frontier(
            repository,
            active.plan_digest,
            tuple(
                work_node
                for work_node, _integration, _goal, _work_item in compatible_units
                if work_node["node_key"] in admitted
            ),
            worker_turn_capacity=worker_capacity,
        )

        outcomes = tuple(
            self._reconcile_work_unit(
                repository,
                active=active,
                plan=plan,
                work_node=work_node,
                integration_node=integration_node,
                goal=goal,
                work_item=work_item,
                worker_turn_capacity=worker_capacity,
            )
            for work_node, integration_node, goal, work_item in compatible_units
        )
        self._advance_integration_batch(
            repository,
            active=active,
            units=units,
        )
        outcomes = tuple(
            self._outcome(state)
            for work_node, _integration, _goal, _work_item in compatible_units
            if (
                state := self._read_state(
                    repository,
                    active.plan_digest,
                    str(work_node["node_key"]),
                )
            )
            is not None
        )
        states_after = self._read_states(repository, active.plan_digest)
        active_after = sum(
            1 for state in states_after if self._state_holds_worker_turn(state)
        )
        if not outcomes:
            work_node, _integration_node, goal, work_item = units[0]
            waiting = self._initial_state(
                repository=repository,
                plan_digest=active.plan_digest,
                goal=goal,
                work_item=work_item,
                work_node=work_node,
            )
            waiting.update(
                {
                    "status": "waiting",
                    "directive": "wait_for_capacity",
                    "admission_state": "capacity_wait",
                    "wait_condition": "worker_capacity",
                }
            )
            representative = self._outcome(waiting)
        else:
            representative = self._representative_outcome(outcomes)
        pending_waits = tuple(
            outcome
            for outcome in outcomes
            if outcome.status not in {"complete", "failed", "superseded"}
            and outcome.wait_condition is not None
        )
        if self._kernel_sweep_allowed(outcomes):
            scheduled = sorted(
                str(outcome.next_check_at)
                for outcome in pending_waits
                if outcome.next_check_at is not None
            )
            wait_identity = digest_value(
                sorted(
                    (
                        outcome.node_key,
                        outcome.wait_condition,
                        outcome.wait_event_identity,
                    )
                    for outcome in pending_waits
                )
            )[:24]
            representative = replace(
                representative,
                status="waiting",
                directive="reconcile_again",
                wait_condition="kernel_sweep",
                wait_source_ref=f"store://kernel-sweep/{repository}",
                wait_event_identity=f"kernel-sweep:{wait_identity}",
                next_check_at=(
                    scheduled[0]
                    if scheduled
                    else (
                        datetime.now(timezone.utc) + timedelta(seconds=30)
                    ).isoformat()
                ),
            )
        all_complete = len(states_after) == len(units) and all(
            state.get("status") == "complete" for state in states_after
        )
        if all_complete:
            representative = replace(
                representative,
                status="complete",
                directive="goal_complete",
                goal_state="completed",
                wait_condition=None,
            )
        return replace(
            representative,
            admitted_node_keys=tuple(admitted_node_keys),
            active_worker_turns=active_after,
            worker_turn_capacity=worker_capacity,
            coordinator_turn_capacity=coordinator_capacity,
            node_outcomes=outcomes,
            completed_work_item_keys=tuple(
                sorted(
                    str(state["work_item_key"])
                    for state in states_after
                    if state.get("status") == "complete"
                    and isinstance(state.get("work_item_key"), str)
                )
            ),
        )
