"""Deterministic V8 Kernel reconciliation over a private SQLite Store."""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
import subprocess
import tempfile
from typing import Any, Protocol
from urllib.parse import quote

from ._canonical import canonical_bytes, digest_bytes, digest_value
from .activation import LocalPlanPublication
from .evidence import EvidenceVerifier, ResultClaim, TypedEvidence
from .runtime import (
    ReviewAxisBinding,
    ReviewAxisObservation,
    ReviewAxisRequest,
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
    resolve_review_profile,
)


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
        self._publication: CandidatePublication | None = None
        self.publication_count = 0
        self.hosted_retry_count = 0
        self.hosted_read_candidates: list[str] = []
        self.integrated_candidates: list[str] = []

    @property
    def published_candidate_sha(self) -> str | None:
        return None if self._publication is None else self._publication.candidate_sha

    def read_publication(
        self,
        repository: str,
        candidate_sha: str,
    ) -> CandidatePublication | None:
        if self._publication is None:
            return None
        if (
            self._publication.repository != repository
            or self._publication.candidate_sha != candidate_sha
        ):
            raise DeliveryControlError(
                "PUBLICATION_IDENTITY_CONFLICT",
                "another Candidate already owns the publication action",
            )
        return self._publication

    def publish_once(
        self,
        repository: str,
        candidate_sha: str,
        evidence_manifest_digest: str,
    ) -> CandidatePublication:
        existing = self.read_publication(repository, candidate_sha)
        if existing is not None:
            if existing.evidence_manifest_digest != evidence_manifest_digest:
                raise DeliveryControlError(
                    "PUBLICATION_EVIDENCE_CONFLICT",
                    "published Candidate has another Evidence Manifest",
                )
            return existing
        self.publication_count += 1
        self._publication = CandidatePublication(
            repository=repository,
            candidate_sha=candidate_sha,
            evidence_manifest_digest=evidence_manifest_digest,
            source_ref=f"memory://publication/{candidate_sha}",
        )
        return self._publication

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
    def _branch(candidate_sha: str) -> str:
        return f"gwo/candidates/{candidate_sha}"

    def _evidence_digest(self, repository: str, candidate_sha: str) -> str | None:
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
                if isinstance(description, str) and description:
                    return description
        return None

    def read_publication(
        self,
        repository: str,
        candidate_sha: str,
    ) -> CandidatePublication | None:
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
            return None
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
        manifest_digest = self._evidence_digest(repository, candidate_sha)
        if manifest_digest is None:
            return None
        return CandidatePublication(
            repository=repository,
            candidate_sha=candidate_sha,
            evidence_manifest_digest=manifest_digest,
            source_ref=(
                f"https://github.com/{repository}/tree/{quote(branch, safe='/')}"
            ),
        )

    def publish_once(
        self,
        repository: str,
        candidate_sha: str,
        evidence_manifest_digest: str,
    ) -> CandidatePublication:
        existing = self.read_publication(repository, candidate_sha)
        if existing is not None:
            if existing.evidence_manifest_digest != evidence_manifest_digest:
                raise DeliveryControlError(
                    "PUBLICATION_EVIDENCE_CONFLICT",
                    "published Candidate has another Evidence Manifest",
                )
            return existing
        branch = self._branch(candidate_sha)
        self._checked(
            [
                "git",
                "push",
                self.remote,
                f"{candidate_sha}:refs/heads/{branch}",
            ]
        )
        self._checked(
            [
                self.executable,
                "api",
                "--method",
                "POST",
                f"repos/{repository}/statuses/{candidate_sha}",
                "-f",
                "state=pending",
                "-f",
                f"context={self.evidence_context}",
                "-f",
                f"description={evidence_manifest_digest}",
            ]
        )
        receipt = self.read_publication(repository, candidate_sha)
        if receipt is None:
            raise DeliveryControlError(
                "PUBLICATION_READBACK_FAILED",
                "Candidate publication was not visible after push",
            )
        return receipt

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
            matched = [
                run
                for run in runs
                if run.get("name") in expected or run.get("workflowName") in expected
            ]
            matched_names = {
                str(run.get("name")) for run in matched if run.get("name") in expected
            } | {
                str(run.get("workflowName"))
                for run in matched
                if run.get("workflowName") in expected
            }
            if matched_names != set(expected):
                return HostedCheckReadback(
                    candidate_sha,
                    "pending",
                    source_ref,
                    tuple(sorted(expected.values())),
                )
            runs = matched
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
        goal: str,
        acceptance: list[str],
        candidate_sha: str,
        changed_files: list[str],
        findings: list[str],
        attempted_approaches: list[str],
        durable_references: list[str],
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

        packet = {
            "schema_version": 1,
            "goal": str(goal)[:2_048],
            "acceptance": bounded(acceptance, each=1_024, total=3_072),
            "candidate_sha": candidate_sha,
            "changed_files": bounded(
                changed_files,
                each=256,
                total=2_048,
            ),
            "findings": bounded(findings, each=2_048, total=4_096),
            "attempted_approaches": bounded(
                attempted_approaches,
                each=1_024,
                total=2_048,
            ),
            "durable_references": bounded(
                durable_references,
                each=512,
                total=1_024,
            ),
        }
        rendered = canonical_bytes(packet).decode("utf-8")
        if len(rendered.encode("utf-8")) > 16_384:
            raise KernelError(
                "RECOVERY_PACKET_TOO_LARGE",
                "bounded Recovery Packet exceeds 16k bytes",
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
        self.repository_path = Path(repository_path).resolve()
        self.integration_branch = integration_branch
        self.writer_generation = writer_generation
        self.runtime_profile = runtime_profile
        self.frontier_runtime_profile = frontier_runtime_profile
        self.runtime_config = runtime_config
        self.delivery_control = delivery_control
        self.parent_agent_id = parent_agent_id
        self.skill_catalog = skill_catalog
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS v8_execution_state (
                    repository TEXT NOT NULL,
                    plan_digest TEXT NOT NULL,
                    state_json TEXT NOT NULL,
                    PRIMARY KEY (repository, plan_digest)
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

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.store_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _read_state(self, repository: str, plan_digest: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT state_json
                FROM v8_execution_state
                WHERE repository = ? AND plan_digest = ?
                """,
                (repository, plan_digest),
            ).fetchone()
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
        state = self._read_state(repository, active.plan_digest)
        if (
            state is None
            or state.get("candidate_sha") != candidate_sha
            or state.get("wait_condition") != "human_decision"
            or not isinstance(approved, bool)
            or not source_ref
        ):
            raise KernelError(
                "HUMAN_DECISION_INVALID",
                "decision does not bind the waiting exact Candidate",
            )
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
        state = self._read_state(repository, plan_digest)
        if (
            state is None
            or state.get("attempt_id") != attempt_id
            or state.get("attempt_state") in {"terminal", "verified", "superseded"}
        ):
            raise KernelError(
                "SUPERSESSION_INVALID",
                "supersession does not identify one non-terminal Attempt",
            )
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
                SELECT state_json FROM v8_execution_state
                WHERE repository = ? AND plan_digest = ?
                """,
                (repository, plan_digest),
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
        connection.execute(
            """
            INSERT INTO v8_execution_state (
                repository,
                plan_digest,
                state_json
            ) VALUES (?, ?, ?)
            ON CONFLICT(repository, plan_digest) DO UPDATE SET
                state_json = excluded.state_json
            """,
            (repository, plan_digest, rendered),
        )

    def _commit_admission(
        self,
        state: dict[str, Any],
        work_node: dict[str, Any],
        *,
        activation_id: str,
        dependency_keys: tuple[str, ...] = (),
    ) -> None:
        claims = work_node.get("resource_claims") or []
        if not isinstance(claims, list) or any(
            not isinstance(claim, str) or not claim for claim in claims
        ):
            raise KernelError(
                "RESOURCE_CLAIMS_INVALID",
                "Plan Node Resource Claims are invalid",
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
                SELECT 1
                FROM v8_pending_activations
                WHERE repository = ?
                """,
                (state["repository"],),
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
            prior_states = connection.execute(
                """
                SELECT plan_digest, state_json
                FROM v8_execution_state
                WHERE repository = ? AND plan_digest != ?
                """,
                (state["repository"], state["plan_digest"]),
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

    def _acquire_integration_lease(self, repository: str, holder: str) -> None:
        try:
            with self._connect() as connection:
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
                field: state.get(field)
                for field in ReconcileOutcome.__dataclass_fields__
            }
        )

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
        state["last_runtime_error"] = {
            "code": error.code,
            "failure_class": error.failure_class,
        }
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
        return {
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
            "admission_id": f"admission:{plan_digest[:20]}",
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
            },
            "resume_sent": False,
        }

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
    def _nodes(
        plan: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
        work_nodes = [
            node for node in plan.get("nodes") or [] if node.get("kind") == "work"
        ]
        integration_nodes = [
            node
            for node in plan.get("nodes") or []
            if node.get("kind") == "integration"
        ]
        if len(work_nodes) != 1 or len(integration_nodes) != 1:
            raise KernelError(
                "ACTIVE_PLAN_UNSUPPORTED",
                "the walking skeleton requires one work and one integration Plan Node",
            )
        goal = (plan.get("goals") or [None])[0]
        work_item = (plan.get("work_items") or [None])[0]
        if not isinstance(goal, dict) or not isinstance(work_item, dict):
            raise KernelError("ACTIVE_PLAN_INVALID", "Goal or Work Item is missing")
        return work_nodes[0], integration_nodes[0], goal, work_item

    def _adopt_or_materialize(
        self,
        state: dict[str, Any],
        work_node: dict[str, Any],
    ):
        prompt = self._prompt_from_state(state)
        selected_profile = (
            self.frontier_runtime_profile
            if int(state.get("attempt_ordinal", 1)) > 1
            else self.runtime_profile
        )
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
            if int(actions.get("prompt", 0)) >= 3:
                blocked = RuntimeAdapterError(
                    "PROMPT_DELIVERY_RETRIES_EXHAUSTED",
                    "three unchanged Prompt executions were exhausted",
                )
                return None, self._materialization_failure(
                    state,
                    blocked,
                    operation="prompt",
                )
            circuit_outcome = self._prepare_runtime_operation(
                state,
                "prompt",
            )
            if circuit_outcome is not None:
                return None, circuit_outcome
            actions["prompt"] = int(actions.get("prompt", 0)) + 1
            state["materialization_executions"] = sum(
                int(value) for value in actions.values()
            )
            self._write_state(state["repository"], state["plan_digest"], state)
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
        attempt_id = f"attempt:{state['plan_digest'][:20]}:{attempt_ordinal}"
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
        terminal_reason: str,
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
                (f"terminal:{terminal_reason}", old_attempt_id),
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
        *,
        terminal_reason: str,
    ) -> None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                UPDATE v8_attempts SET state = ?
                WHERE attempt_id = ? AND state = 'running'
                """,
                (
                    f"terminal:{terminal_reason}",
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
                        "Produce a changed clean Candidate, then return the "
                        "same bounded GWO_RESULT schema."
                    ),
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
        )

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
        prior_findings: list[dict[str, str]] = []
        for captured in (state.get("review_observations") or {}).values():
            if not isinstance(captured, dict):
                continue
            for finding in captured.get("findings") or ():
                if isinstance(finding, dict):
                    prior_findings.append(
                        {
                            str(key): str(value)[:1000]
                            for key, value in finding.items()
                            if isinstance(key, str)
                        }
                    )
        prior_review_context = {
            "candidate_sha": state.get("candidate_sha"),
            "findings": prior_findings[:32],
        }
        packet = ladder.recovery_packet(
            goal=str(goal.get("objective") or ""),
            acceptance=[str(item) for item in goal.get("acceptance") or ()],
            candidate_sha=str(state.get("candidate_sha") or ""),
            changed_files=[
                str(change.get("path"))
                for change in (work_node.get("inputs") or {}).get("file_changes") or ()
                if isinstance(change, dict)
            ],
            findings=[str(finding) for finding in findings],
            attempted_approaches=[
                (f"Attempt {attempt_ordinal} used {repair_rounds_used} Repair Rounds")
            ],
            durable_references=[
                str(work_item.get("source_ref") or ""),
                str(state.get("publication_ref") or ""),
            ],
        )
        if directive.action == "repair_same_attempt":
            prompt = self._recovery_prompt(
                work_node,
                packet,
                same_attempt=True,
            )
            self.runtime.repair(binding, prompt)
            state.update(
                {
                    "status": "waiting",
                    "directive": "wait_for_runtime",
                    "attempt_state": "repairing",
                    "repair_rounds_used": repair_rounds_used + 1,
                    "attempt_terminal_reason": None,
                    "candidate_sha": None,
                    "publication_eligible": None,
                    "publication_state": None,
                    "publication_ref": None,
                    "hosted_check_state": None,
                    "hosted_retry_count": 0,
                    "review_candidate_sha": None,
                    "review_bindings": {},
                    "review_observations": {},
                    "review_children_retired": False,
                    "review_evidence": None,
                    "candidate_observation": None,
                    "prior_review_context": prior_review_context,
                    "worker_parked_for_ci": False,
                    "wait_condition": "runtime_result",
                    "wait_source_ref": (
                        f"{self.runtime.adapter_name}://attempt/"
                        f"{state['attempt_id']}/repair"
                    ),
                    "wait_event_identity": (
                        f"repair:{state['attempt_id']}:{repair_rounds_used + 1}"
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
            if self.frontier_runtime_profile is None:
                state.update(
                    {
                        "status": "blocked",
                        "directive": "request_decision",
                        "attempt_state": "recovery_profile_blocked",
                        "wait_condition": None,
                    }
                )
                self._write_state(
                    state["repository"],
                    state["plan_digest"],
                    state,
                )
                return self._outcome(state)
            old_attempt_id = str(state["attempt_id"])
            prompt = self._recovery_prompt(
                work_node,
                packet,
                same_attempt=False,
            )
            next_ordinal = attempt_ordinal + 1
            state.update(
                {
                    "status": "running",
                    "directive": "run_again",
                    "admission_id": (
                        f"admission:{state['plan_digest'][:20]}:{next_ordinal}"
                    ),
                    "admission_state": "materializing",
                    "attempt_id": None,
                    "attempt_state": None,
                    "attempt_ordinal": next_ordinal,
                    "repair_rounds_used": 0,
                    "attempt_terminal_reason": None,
                    "candidate_sha": None,
                    "publication_eligible": None,
                    "publication_state": None,
                    "publication_ref": None,
                    "hosted_check_state": None,
                    "hosted_retry_count": 0,
                    "review_candidate_sha": None,
                    "review_bindings": {},
                    "review_observations": {},
                    "review_children_retired": False,
                    "review_evidence": None,
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
                    },
                }
            )
            self._replace_with_frontier_admission(
                state,
                work_node,
                old_attempt_id=old_attempt_id,
                terminal_reason=terminal_reason,
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
        self._mark_plan_node_failed(
            state,
            terminal_reason=terminal_reason,
        )
        self.runtime.retire(binding)
        return self._outcome(state)

    @staticmethod
    def _review_binding_from_state(value: dict[str, Any]) -> ReviewAxisBinding:
        return ReviewAxisBinding(**value)

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

    @staticmethod
    def _review_observation_from_state(
        value: dict[str, Any],
    ) -> ReviewAxisObservation:
        body = dict(value)
        body["findings"] = tuple(
            dict(finding) for finding in body.get("findings") or ()
        )
        return ReviewAxisObservation(**body)

    @staticmethod
    def _review_request(
        *,
        state: dict[str, Any],
        goal: dict[str, Any],
        work_item: dict[str, Any],
        binding,
        observation,
        axis: str,
        recovery_ordinal: int,
    ) -> ReviewAxisRequest:
        assert observation.result_claim is not None
        check_manifest_digest = state.get("review_check_manifest_digest")
        if not isinstance(check_manifest_digest, str):
            check_manifest_digest = digest_value(
                sorted(
                    evidence.content_digest
                    for evidence in observation.evidence
                    if isinstance(evidence, TypedEvidence)
                    and evidence.kind == "check"
                    and evidence.has_valid_digest()
                )
            )
        workspace = Path(binding.workspace).resolve()
        commit_output = _git(
            workspace,
            "log",
            "--format=%H %s",
            f"{state['base_sha']}..{observation.result_claim.candidate_sha}",
        )
        spec_text = canonical_bytes(
            {
                "goal_acceptance": goal.get("acceptance") or [],
                "outcome_contract": work_item.get("outcome_contract") or {},
            }
        ).decode("utf-8")
        prior_context = state.get("prior_review_context") or {}
        prior_candidate = prior_context.get("candidate_sha")
        candidate_delta = None
        if (
            isinstance(prior_candidate, str)
            and prior_candidate
            and prior_candidate != observation.result_claim.candidate_sha
        ):
            candidate_delta = _git(
                workspace,
                "diff",
                "--stat",
                prior_candidate,
                observation.result_claim.candidate_sha,
            )[:4000]
        return ReviewAxisRequest(
            repository=state["repository"],
            attempt_id=observation.result_claim.attempt_id,
            candidate_sha=observation.result_claim.candidate_sha,
            base_sha=state["base_sha"],
            axis=axis,
            recovery_ordinal=recovery_ordinal,
            workspace=workspace,
            diff_command=(
                "git",
                "diff",
                f"{state['base_sha']}...{observation.result_claim.candidate_sha}",
            ),
            commit_list=tuple(commit_output.splitlines()),
            spec_source_ref=str(work_item.get("source_ref") or ""),
            spec_text=spec_text,
            standards_sources=("AGENTS.md", "CONTEXT.md"),
            check_manifest_digest=check_manifest_digest,
            prior_findings=tuple(
                dict(finding)
                for finding in prior_context.get("findings") or ()
                if isinstance(finding, dict)
            ),
            candidate_delta=candidate_delta,
        )

    def _review_wait(
        self,
        state: dict[str, Any],
        *,
        candidate_sha: str,
    ) -> ReconcileOutcome:
        state.update(
            {
                "status": "waiting",
                "directive": "wait_for_review",
                "attempt_state": "reviewing",
                "wait_condition": "review_axis",
                "wait_source_ref": (
                    f"{self.runtime.adapter_name}://review/{candidate_sha}"
                ),
                "wait_event_identity": f"review:{candidate_sha}",
                "next_check_at": (
                    datetime.now(timezone.utc) + timedelta(seconds=30)
                ).isoformat(),
            }
        )
        self._write_state(state["repository"], state["plan_digest"], state)
        return self._outcome(state)

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

    def _ensure_review_evidence(
        self,
        state: dict[str, Any],
        work_node: dict[str, Any],
        goal: dict[str, Any],
        work_item: dict[str, Any],
        binding,
        observation,
    ):
        requirement = (work_node.get("output_contract") or {}).get(
            "review_requirement"
        ) or {
            "mode": "none",
            "axes": [],
            "specialist_requirements": [],
            "human_decision_required": False,
        }
        if requirement.get("mode") == "none":
            return observation, None
        if observation.result_claim is None:
            raise KernelError(
                "REVIEW_CANDIDATE_MISSING",
                "Review cannot begin before one Result Claim",
            )
        if self.runtime_config is None:
            raise KernelError(
                "REVIEW_RUNTIME_CONFIG_MISSING",
                "reviewed work requires host-local Review Profiles",
            )
        materialize = getattr(self.runtime, "materialize_review_axis", None)
        observe_axis = getattr(self.runtime, "observe_review_axis", None)
        if not callable(materialize) or not callable(observe_axis):
            raise KernelError(
                "REVIEW_RUNTIME_UNSUPPORTED",
                "Runtime Adapter does not support Review Internal Subagents",
            )
        candidate_sha = observation.result_claim.candidate_sha
        if state.get("review_candidate_sha") not in {None, candidate_sha}:
            state["review_bindings"] = {}
            state["review_observations"] = {}
            state["review_children_retired"] = False
            state.pop("review_evidence", None)
        state["review_candidate_sha"] = candidate_sha
        bindings = state.setdefault("review_bindings", {})
        observations = state.setdefault("review_observations", {})

        def collect(axis: str, recovery_ordinal: int):
            key = f"{axis}:{recovery_ordinal}"
            request = self._review_request(
                state=state,
                goal=goal,
                work_item=work_item,
                binding=binding,
                observation=observation,
                axis=axis,
                recovery_ordinal=recovery_ordinal,
            )
            saved_observation = observations.get(key)
            if isinstance(saved_observation, dict):
                captured = self._review_observation_from_state(saved_observation)
                if captured.lifecycle == "completed":
                    return request, captured
            selector = (
                "recovery_axis"
                if recovery_ordinal > 0
                else (
                    "strict_specialist"
                    if axis.startswith("specialist:")
                    else "standard_axis"
                )
            )
            profile = resolve_review_profile(
                self.runtime_config,
                repository=state["repository"],
                selector=selector,
            )
            saved_binding = bindings.get(key)
            if isinstance(saved_binding, dict):
                child_binding = self._review_binding_from_state(saved_binding)
            else:
                self.publication.assert_writer(
                    repository=state["repository"],
                    writer_generation=self.writer_generation,
                    plan_digest=state["plan_digest"],
                    activation_id=state["activation_id"],
                )
                child_binding = materialize(
                    request,
                    profile,
                    parent_agent_id=binding.agent_id,
                )
                bindings[key] = asdict(child_binding)
                self._write_state(
                    state["repository"],
                    state["plan_digest"],
                    state,
                )
            captured = observe_axis(request, child_binding)
            observations[key] = asdict(captured)
            self._write_state(
                state["repository"],
                state["plan_digest"],
                state,
            )
            return request, captured

        required_axes = (
            *tuple(requirement.get("axes") or ()),
            *tuple(
                f"specialist:{item}"
                for item in requirement.get("specialist_requirements") or ()
            ),
        )
        primary: dict[str, ReviewAxisObservation] = {}
        requests: dict[str, ReviewAxisRequest] = {}
        running = False
        try:
            for axis in required_axes:
                try:
                    request, captured = collect(str(axis), 0)
                except RuntimeAdapterError as error:
                    if error.code not in {
                        "REVIEW_AXIS_OUTPUT_MISSING",
                        "REVIEW_AXIS_OUTPUT_INVALID",
                    }:
                        raise
                    request = self._review_request(
                        state=state,
                        goal=goal,
                        work_item=work_item,
                        binding=binding,
                        observation=observation,
                        axis=str(axis),
                        recovery_ordinal=0,
                    )
                    requests[str(axis)] = request
                    state.setdefault("review_axis_errors", {})[str(axis)] = error.code
                    continue
                requests[str(axis)] = request
                if captured.lifecycle == "completed":
                    primary[str(axis)] = captured
                else:
                    running = True
        except RuntimeAdapterError as error:
            state.update(
                {
                    "status": "blocked",
                    "directive": "request_decision",
                    "attempt_state": "review_runtime_blocked",
                    "wait_condition": None,
                    "wait_source_ref": None,
                    "wait_event_identity": None,
                    "next_check_at": None,
                    "last_runtime_error": {
                        "code": error.code,
                        "failure_class": error.failure_class,
                    },
                }
            )
            self._write_state(
                state["repository"],
                state["plan_digest"],
                state,
            )
            return observation, self._outcome(state)
        if running:
            return observation, self._review_wait(
                state,
                candidate_sha=candidate_sha,
            )

        first_request = requests[required_axes[0]]
        gate = self.verifier.assemble_review_evidence(
            observation.result_claim,
            requirement,
            tuple(primary[axis] for axis in required_axes if axis in primary),
            acceptance_digest=first_request.spec_digest,
            check_manifest_digest=first_request.check_manifest_digest,
            observer_id=binding.runtime_id,
        )
        if gate.missing_axes:
            recovered = dict(primary)
            recovery_running = False
            try:
                for axis in gate.missing_axes:
                    request, captured = collect(axis, 1)
                    requests[axis] = request
                    if captured.lifecycle == "completed":
                        recovered[axis] = captured
                    else:
                        recovery_running = True
            except RuntimeAdapterError as error:
                state.update(
                    {
                        "status": "blocked",
                        "directive": "request_decision",
                        "attempt_state": "review_recovery_blocked",
                        "wait_condition": None,
                        "last_runtime_error": {
                            "code": error.code,
                            "failure_class": error.failure_class,
                        },
                    }
                )
                self._write_state(
                    state["repository"],
                    state["plan_digest"],
                    state,
                )
                return observation, self._outcome(state)
            if recovery_running:
                return observation, self._review_wait(
                    state,
                    candidate_sha=candidate_sha,
                )
            gate = self.verifier.assemble_review_evidence(
                observation.result_claim,
                requirement,
                tuple(recovered[axis] for axis in required_axes if axis in recovered),
                acceptance_digest=first_request.spec_digest,
                check_manifest_digest=first_request.check_manifest_digest,
                observer_id=binding.runtime_id,
            )
        if gate.evidence is None:
            state.update(
                {
                    "status": "blocked",
                    "directive": "request_decision",
                    "attempt_state": "review_evidence_invalid",
                    "wait_condition": None,
                }
            )
            self._write_state(
                state["repository"],
                state["plan_digest"],
                state,
            )
            return observation, self._outcome(state)
        state["review_evidence"] = asdict(gate.evidence)
        state["review_gate_status"] = gate.status
        state["wait_condition"] = None
        state["wait_source_ref"] = None
        state["wait_event_identity"] = None
        state["next_check_at"] = None
        self._write_state(state["repository"], state["plan_digest"], state)
        retire_review = getattr(self.runtime, "retire_review_axis", None)
        if callable(retire_review) and not state.get("review_children_retired"):
            self.publication.assert_writer(
                repository=state["repository"],
                writer_generation=self.writer_generation,
                plan_digest=state["plan_digest"],
                activation_id=state["activation_id"],
            )
            try:
                for saved in bindings.values():
                    if isinstance(saved, dict):
                        retire_review(self._review_binding_from_state(saved))
            except RuntimeAdapterError as error:
                state.update(
                    {
                        "status": "waiting",
                        "directive": "wait_for_review_retirement",
                        "attempt_state": "reviewing",
                        "wait_condition": "review_retirement",
                        "wait_source_ref": (
                            f"{self.runtime.adapter_name}://review/"
                            f"{candidate_sha}/retirement"
                        ),
                        "wait_event_identity": (f"review-retirement:{candidate_sha}"),
                        "next_check_at": (
                            datetime.now(timezone.utc) + timedelta(seconds=30)
                        ).isoformat(),
                        "last_runtime_error": {
                            "code": error.code,
                            "failure_class": error.failure_class,
                        },
                    }
                )
                self._write_state(
                    state["repository"],
                    state["plan_digest"],
                    state,
                )
                return observation, self._outcome(state)
            state["review_children_retired"] = True
            self._write_state(
                state["repository"],
                state["plan_digest"],
                state,
            )
        if requirement.get("human_decision_required") is True:
            decision = state.get("human_decision")
            if (
                not isinstance(decision, dict)
                or decision.get("candidate_sha") != candidate_sha
            ):
                state.update(
                    {
                        "status": "waiting",
                        "directive": "wait_for_decision",
                        "attempt_state": "reviewing",
                        "wait_condition": "human_decision",
                        "wait_source_ref": str(work_item.get("source_ref") or ""),
                        "wait_event_identity": (f"human-decision:{candidate_sha}"),
                        "next_check_at": None,
                    }
                )
                self._write_state(
                    state["repository"],
                    state["plan_digest"],
                    state,
                )
                return observation, self._outcome(state)
            if decision.get("approved") is not True:
                state.update(
                    {
                        "status": "blocked",
                        "directive": "request_decision",
                        "attempt_state": "candidate_rejected",
                        "wait_condition": None,
                    }
                )
                self._write_state(
                    state["repository"],
                    state["plan_digest"],
                    state,
                )
                return observation, self._outcome(state)
            decision_evidence = TypedEvidence._capture(
                kind="decision",
                subject=candidate_sha,
                observer_type="human",
                observer_id=str(decision["source_ref"]),
                observed_at=_now(),
                source_ref=str(decision["source_ref"]),
                payload={
                    "candidate_sha": candidate_sha,
                    "approved": True,
                    "decision_kind": "strict_review_human",
                },
            )
            state["human_decision_evidence"] = asdict(decision_evidence)
            self._write_state(
                state["repository"],
                state["plan_digest"],
                state,
            )
        return replace(
            observation,
            evidence=observation.evidence
            + (gate.evidence,)
            + (
                ()
                if requirement.get("human_decision_required") is not True
                else (decision_evidence,)
            ),
        ), None

    def _ensure_publication_and_hosted_checks(
        self,
        state: dict[str, Any],
        binding,
        observation,
        eligibility,
        hosted_check_definitions: tuple[dict[str, Any], ...],
        delivery_required: bool,
    ) -> ReconcileOutcome | None:
        state["publication_eligible"] = bool(eligibility.eligible)
        if self.delivery_control is None:
            if delivery_required or hosted_check_definitions:
                state.update(
                    {
                        "status": "blocked",
                        "directive": "request_decision",
                        "attempt_state": "delivery_control_missing",
                        "hosted_check_state": "unavailable",
                        "wait_condition": None,
                    }
                )
                self._write_state(
                    state["repository"],
                    state["plan_digest"],
                    state,
                )
                return self._outcome(state)
            return None
        self.publication.assert_writer(
            repository=state["repository"],
            writer_generation=self.writer_generation,
            plan_digest=state["plan_digest"],
            activation_id=state["activation_id"],
        )
        if not eligibility.eligible or observation.result_claim is None:
            raise KernelError(
                "PUBLICATION_NOT_ELIGIBLE",
                "Candidate cannot publish before derived eligibility is true",
            )
        candidate_sha = observation.result_claim.candidate_sha
        manifest_digest = digest_value(
            {
                "candidate_sha": candidate_sha,
                "check_evidence_digests": list(eligibility.check_evidence_digests),
                "review_evidence_digest": (eligibility.review_evidence_digest),
            }
        )
        try:
            receipt = self.delivery_control.read_publication(
                state["repository"],
                candidate_sha,
            )
            if receipt is None:
                self.delivery_control.publish_once(
                    state["repository"],
                    candidate_sha,
                    manifest_digest,
                )
                receipt = self.delivery_control.read_publication(
                    state["repository"],
                    candidate_sha,
                )
            if (
                receipt is None
                or receipt.candidate_sha != candidate_sha
                or receipt.evidence_manifest_digest != manifest_digest
            ):
                raise DeliveryControlError(
                    "PUBLICATION_READBACK_FAILED",
                    "Candidate publication did not round-trip exact identity",
                )
        except DeliveryControlError as error:
            state.update(
                {
                    "status": "blocked",
                    "directive": "request_decision",
                    "attempt_state": "publication_blocked",
                    "publication_state": "blocked",
                    "wait_condition": None,
                    "last_delivery_error": {
                        "code": error.code,
                    },
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
                "publication_state": "published",
                "publication_ref": receipt.source_ref,
                "evidence_manifest_digest": manifest_digest,
            }
        )
        if not state.get("worker_parked_for_ci"):
            self.runtime.interrupt(binding)
            state["worker_parked_for_ci"] = True
        try:
            hosted = self.delivery_control.read_hosted_checks(
                state["repository"],
                candidate_sha,
                hosted_check_definitions,
            )
        except DeliveryControlError as error:
            state.update(
                {
                    "status": "waiting",
                    "directive": "wait_for_hosted_ci",
                    "attempt_state": "parked",
                    "hosted_check_state": "unavailable",
                    "wait_condition": "hosted_ci",
                    "wait_source_ref": receipt.source_ref,
                    "wait_event_identity": f"hosted-ci:{candidate_sha}",
                    "next_check_at": (
                        datetime.now(timezone.utc) + timedelta(seconds=30)
                    ).isoformat(),
                    "last_delivery_error": {"code": error.code},
                }
            )
            self._write_state(
                state["repository"],
                state["plan_digest"],
                state,
            )
            return self._outcome(state)
        if hosted.candidate_sha != candidate_sha:
            raise KernelError(
                "HOSTED_CHECK_IDENTITY_MISMATCH",
                "hosted check readback changed Candidate identity",
            )
        expected_definition_digests = tuple(
            sorted(
                str(check["definition_digest"]) for check in hosted_check_definitions
            )
        )
        if hosted.definition_digests != expected_definition_digests:
            raise KernelError(
                "HOSTED_CHECK_DEFINITION_MISMATCH",
                "hosted check readback changed the compiled Check Definitions",
            )
        state["hosted_check_state"] = hosted.status
        if hosted.status == "passed":
            hosted_evidence = tuple(
                TypedEvidence._capture(
                    kind="check",
                    subject=candidate_sha,
                    observer_type="github",
                    observer_id=self.delivery_control.__class__.__name__,
                    observed_at=_now(),
                    source_ref=hosted.source_ref,
                    payload={
                        "check_id": check["check_id"],
                        "definition_digest": check["definition_digest"],
                        "hosted_name": check["hosted_name"],
                        "candidate_sha": candidate_sha,
                        "outcome": "passed",
                    },
                )
                for check in hosted_check_definitions
            )
            hosted_findings = self.verifier.verify_hosted_checks(
                candidate_sha,
                hosted_check_definitions,
                hosted_evidence,
            )
            if hosted_findings:
                raise KernelError(
                    "HOSTED_CHECK_EVIDENCE_INVALID",
                    "; ".join(hosted_findings),
                )
            state["hosted_check_evidence"] = [asdict(item) for item in hosted_evidence]
            state["attempt_state"] = "result_submitted"
            state["wait_condition"] = None
            state["wait_source_ref"] = None
            state["wait_event_identity"] = None
            state["next_check_at"] = None
            self._write_state(
                state["repository"],
                state["plan_digest"],
                state,
            )
            return None
        if hosted.status == "infrastructure_failure":
            retries = int(state.get("hosted_retry_count", 0))
            if retries >= 2:
                state.update(
                    {
                        "status": "blocked",
                        "directive": "request_decision",
                        "attempt_state": "parked",
                        "wait_condition": None,
                    }
                )
                self._write_state(
                    state["repository"],
                    state["plan_digest"],
                    state,
                )
                return self._outcome(state)
            self.delivery_control.retry_hosted_checks(
                state["repository"],
                candidate_sha,
            )
            state["hosted_retry_count"] = retries + 1
        elif hosted.status == "cancelled":
            state.update(
                {
                    "status": "waiting",
                    "directive": "request_decision",
                    "attempt_state": "parked",
                    "wait_condition": "hosted_ci_cancelled",
                    "wait_source_ref": hosted.source_ref,
                    "wait_event_identity": f"hosted-ci-cancelled:{candidate_sha}",
                    "next_check_at": None,
                }
            )
            self._write_state(
                state["repository"],
                state["plan_digest"],
                state,
            )
            return self._outcome(state)
        elif hosted.status in {"failed", "code_failure", "contract_failure"}:
            state.update(
                {
                    "status": "rejected",
                    "directive": "invoke_coordinator",
                    "attempt_state": "candidate_rejected",
                    "wait_condition": None,
                    "wait_source_ref": hosted.source_ref,
                    "wait_event_identity": None,
                    "next_check_at": None,
                }
            )
            self._write_state(
                state["repository"],
                state["plan_digest"],
                state,
            )
            return self._outcome(state)
        elif hosted.status != "pending":
            raise KernelError(
                "HOSTED_CHECK_STATUS_INVALID",
                f"unknown hosted check status: {hosted.status}",
            )
        state.update(
            {
                "status": "waiting",
                "directive": "wait_for_hosted_ci",
                "attempt_state": "parked",
                "wait_condition": "hosted_ci",
                "wait_source_ref": hosted.source_ref,
                "wait_event_identity": f"hosted-ci:{candidate_sha}",
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

    def reconcile_once(self, repository: str) -> ReconcileOutcome:
        active = self.publication.read_active(repository)
        if active is None:
            raise KernelError(
                "PLAN_NOT_ACTIVE", "repository has no active Plan Revision"
            )
        if active.writer_generation != self.writer_generation:
            raise KernelError(
                "WRITER_GENERATION_MISMATCH",
                "Kernel does not own the active writer generation",
            )
        plan = self._validate_plan(repository, active.canonical_bytes)
        work_node, integration_node, goal, work_item = self._nodes(plan)

        state = self._read_state(repository, active.plan_digest)
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
                )
            if error.failure_class == "permanent":
                state.update(
                    {
                        "status": "blocked",
                        "directive": "request_decision",
                        "attempt_state": "runtime_configuration_blocked",
                        "wait_condition": None,
                        "last_runtime_error": {
                            "code": error.code,
                            "failure_class": error.failure_class,
                        },
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
                    "last_runtime_error": {
                        "code": error.code,
                        "failure_class": error.failure_class,
                    },
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
            )
        state.update(
            {
                "candidate_sha": observation.result_claim.candidate_sha,
                "attempt_state": "result_submitted",
                "wait_condition": None,
                "wait_source_ref": None,
                "wait_event_identity": None,
                "next_check_at": None,
                "review_check_manifest_digest": digest_value(
                    {
                        "candidate_sha": observation.result_claim.candidate_sha,
                        "definitions": sorted(
                            str(check.get("definition_digest"))
                            for check in (work_node.get("output_contract") or {}).get(
                                "checks"
                            )
                            or ()
                            if isinstance(check, dict)
                            and check.get("hosted_only") is not True
                        ),
                    }
                ),
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
                )
            affected_ids = {
                str(check["check_id"])
                for check in (work_node.get("output_contract") or {}).get("checks")
                or ()
                if isinstance(check, dict)
                and check.get("suite") == "affected"
                and check.get("hosted_only") is not True
            }
            state["review_check_manifest_digest"] = digest_value(
                sorted(
                    item.content_digest
                    for item in observation.evidence
                    if item.kind == "check"
                    and item.payload.get("check_id") in affected_ids
                    and item.has_valid_digest()
                )
            )
            self._write_state(repository, active.plan_digest, state)
        observation, review_terminal = self._ensure_review_evidence(
            state,
            work_node,
            goal,
            work_item,
            binding,
            observation,
        )
        if review_terminal is not None:
            if review_terminal.wait_condition == "review_axis":
                capture_checks = getattr(
                    self.runtime,
                    "capture_deferred_checks",
                    None,
                )
                if callable(capture_checks):
                    observation = capture_checks(binding, observation)
                    self._persist_runtime_observation(state, observation)
                    self._write_state(repository, active.plan_digest, state)
            return review_terminal
        capture_checks = getattr(
            self.runtime,
            "capture_deferred_checks",
            None,
        )
        if callable(capture_checks):
            observation = capture_checks(binding, observation)
            self._persist_runtime_observation(state, observation)
            self._write_state(repository, active.plan_digest, state)
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
        delivery_terminal = self._ensure_publication_and_hosted_checks(
            state,
            binding,
            observation,
            eligibility,
            tuple(
                check
                for check in (work_node.get("output_contract") or {}).get("checks")
                or ()
                if isinstance(check, dict) and check.get("hosted_only") is True
            ),
            bool((work_node.get("output_contract") or {}).get("delivery_required")),
        )
        if delivery_terminal is not None:
            if delivery_terminal.status == "rejected":
                return self._handle_semantic_rejection(
                    state,
                    work_node,
                    goal,
                    work_item,
                    binding,
                    terminal_reason="rejected",
                    findings=(
                        (
                            "hosted exact-SHA checks rejected "
                            f"{observation.result_claim.candidate_sha}"
                        ),
                    ),
                )
            return delivery_terminal
        state["result_digest"] = decision.result.result_digest
        state["status"] = "verified"
        state["attempt_state"] = "verified"
        self._release_attempt_claims(state)

        lease_holder = integration_node["node_key"]
        self._acquire_integration_lease(repository, lease_holder)
        try:
            self.publication.assert_writer(
                repository=repository,
                writer_generation=self.writer_generation,
                plan_digest=active.plan_digest,
                activation_id=active.activation_id,
            )
            remote_integration = None
            if self.delivery_control is not None:
                remote_integration = self.delivery_control.integrate_serially(
                    repository,
                    observation.result_claim.candidate_sha,
                    self.integration_branch,
                )
                if (
                    remote_integration.candidate_sha
                    != observation.result_claim.candidate_sha
                    or remote_integration.target_branch != self.integration_branch
                ):
                    raise KernelError(
                        "REMOTE_INTEGRATION_READBACK_FAILED",
                        "remote Integration changed Candidate or target branch",
                    )
            current_branch = _git(
                self.repository_path, "rev-parse", "--abbrev-ref", "HEAD"
            )
            if current_branch != self.integration_branch:
                raise KernelError(
                    "INTEGRATION_BRANCH_MISMATCH",
                    "repository is not on the configured Integration branch",
                )
            current_head = _git(self.repository_path, "rev-parse", "HEAD")
            if current_head != observation.result_claim.candidate_sha:
                _git(
                    self.repository_path,
                    "merge",
                    "--ff-only",
                    observation.result_claim.candidate_sha,
                )
            integrated_sha = _git(self.repository_path, "rev-parse", "HEAD")
            if integrated_sha != observation.result_claim.candidate_sha:
                raise KernelError(
                    "INTEGRATION_READBACK_FAILED",
                    "Integration branch did not reach the Candidate",
                )
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
                    "integration_node": integration_node["node_key"],
                    "branch": self.integration_branch,
                    "head": integrated_sha,
                    "remote_readback": (
                        None
                        if remote_integration is None
                        else remote_integration.candidate_sha
                    ),
                },
            )
            state.update(
                {
                    "status": "complete",
                    "directive": "goal_complete",
                    "goal_state": "completed",
                    "work_item_state": "integrated",
                    "wait_condition": None,
                    "wait_source_ref": None,
                    "wait_event_identity": None,
                    "next_check_at": None,
                    "integration_evidence_digest": (
                        integration_evidence.content_digest
                    ),
                }
            )
            self._record_verified_result(
                state,
                candidate_sha=observation.result_claim.candidate_sha,
                result_digest=decision.result.result_digest,
            )
            self._write_state(repository, active.plan_digest, state)
        finally:
            self._release_integration_lease(repository, lease_holder)

        self.runtime.retire(binding)
        return self._outcome(state)
