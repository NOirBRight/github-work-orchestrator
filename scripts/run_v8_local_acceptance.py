"""Run the deterministic V8 single-Campaign public-API acceptance.

The harness composes local Planning, Runtime, Candidate, Review, and
delivery doubles, then drives the Campaign only with ``gwo_v8.start``,
``gwo_v8.advance``, and ``gwo_v8.inspect``.  All mutable acceptance state is
created below the caller supplied temporary root.
"""

from __future__ import annotations

import argparse
import base64
from contextlib import contextmanager
from dataclasses import dataclass, replace
import gc
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills" / "orchestrator" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import gwo_v8  # noqa: E402
from gwo_v8._canonical import (  # noqa: E402
    canonical_bytes,
    digest_value,
    load_canonical_json,
)
from gwo_v8._batch_integrator_drivers import (  # noqa: E402
    BatchPublicationReceipt,
    GitCliBatchDriver,
    HostedResultObservation,
    LocalCheckReceipt,
    PullRequestReadback,
    TargetIntegrationReadback,
)
from gwo_v8._batch_integrator_store import (  # noqa: E402
    SqliteBatchDeliveryJournal,
)
from gwo_v8.batch_integrator import (  # noqa: E402
    BatchDeliveryProof,
    BatchDeliveryRequest,
    BatchIntegrator,
    BatchIntegratorConfiguration,
    BatchTarget,
    HostedSuiteDefinition,
    LocalSuiteDefinition,
)
from gwo_v8.candidate_gate import (  # noqa: E402
    AssuranceMode,
    AssuranceRequirement,
    AcceptedCandidateReceipt,
    AuditFailureKind,
    AuditFailureRoute,
    CandidateAcceptanceFacts,
    CandidateCheckEvidence,
    CandidateDiffEntryV1,
    CandidateDiffRecordV1,
    CandidateGate,
    CandidateGateParent,
    CandidateReceipt,
    DeterministicAuditFailure,
    FormalReviewFinding,
    FormalReviewResult,
    InteractionClassification,
    InteractionKey,
    RepairVerificationResult,
    ReviewFindingDisposition,
)
from gwo_v8.candidate_git import GitCandidateReader  # noqa: E402
from gwo_v8.campaign_watchdog import (  # noqa: E402
    CampaignWatchdog,
    WatchdogWake,
    WatchdogWakePage,
)
from gwo_v8.execution_kernel import (  # noqa: E402
    CampaignHandle,
    ExecutionKernelConfiguration,
    ResultIntegrityProof,
    WorkRunAction,
    WorkRunObservation,
    install_execution_kernel,
)
from gwo_v8.plan_control import (  # noqa: E402
    InMemoryPlanRepository,
    PlanControl,
    _install_start_host,
)
from gwo_v8.runtime_gateway import (  # noqa: E402
    ArtifactStore,
    CapabilityPolicy,
    CapabilityPolicyProof,
    PlanningPreflightReceipt,
    PlanningReceipt,
    WorkRunPurpose,
    WorkRunSubject,
)


PUBLIC_API_SINGLE_NODE_GO = "PUBLIC_API_SINGLE_NODE_GO"
LOCAL_ROOT_CANARY_GO = "LOCAL_ROOT_CANARY_GO"
_REPOSITORY = "local/v8-acceptance"
_TICKET_KEY = "issue:1"
_TARGET_BRANCH = "main"
_ROOT_STANDARD_TICKETS = ("issue:101", "issue:102", "issue:103")
_ROOT_STRICT_TICKET = "issue:104"
_ROOT_TICKET_KEYS = (*_ROOT_STANDARD_TICKETS, _ROOT_STRICT_TICKET)


def _campaign_key(run_id: str, scenario: str) -> str:
    return (
        "campaign:"
        + digest_value(
            {
                "kind": "gwo.v8.local-acceptance-campaign.v1",
                "run_id": run_id,
                "scenario": scenario,
            }
        )[:24]
    )


@contextmanager
def _connection(path: Path):
    connection = sqlite3.connect(path)
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


class LocalAcceptanceFailure(RuntimeError):
    """Deterministic failure used by the failure branch of the acceptance."""


def _policy() -> dict[str, Any]:
    core = {
        "schema_version": 1,
        "ref": "policy:local-acceptance",
        "replan": {
            "successor_revision_limit": 1,
            "repeated_invalidation_limit": 1,
        },
        "authority_grants": {
            "campaign": [
                {
                    "operation_id": "repository.read.v1",
                    "resource_id": "campaign.snapshot.v1",
                }
            ],
            "worker": [
                {
                    "operation_id": "workspace.write.v1",
                    "resource_id": "work-run.workspace.v1",
                }
            ],
            "recovery_worker": [
                {
                    "operation_id": "workspace.write.v1",
                    "resource_id": "work-run.workspace.v1",
                }
            ],
            "review": [
                {
                    "operation_id": "repository.read.v1",
                    "resource_id": "review.subject.v1",
                }
            ],
        },
        "allowed_capabilities": ["git", "local_check"],
        "exclusive_resources": ["repository.target.v1"],
    }
    return {**core, "digest": digest_value(core)}


def _campaign_source(
    root_repository: "_RootGitRepository | None" = None,
) -> dict[str, str]:
    core = {
        "repository": _REPOSITORY,
        "input_ref": "refs/heads/main",
        "resolved_commit_oid": (
            "a" * 40 if root_repository is None else root_repository.base_commit
        ),
        "tree_oid": (
            "b" * 40 if root_repository is None else root_repository.base_tree
        ),
    }
    return {**core, "digest": digest_value(core)}


def _ticket() -> dict[str, Any]:
    repository = {
        "full_name": _REPOSITORY,
        "url": f"https://api.github.com/repos/{_REPOSITORY}",
    }
    labels = [
        {
            "id": 1,
            "node_id": "LABEL_READY",
            "url": f"https://api.github.com/repos/{_REPOSITORY}/labels/ready-for-agent",
            "name": "ready-for-agent",
            "color": "0052cc",
            "default": False,
            "description": "ready",
        }
    ]
    contract = {
        "id": 1,
        "node_id": "ISSUE_1",
        "number": 1,
        "title": "V8 local acceptance Ticket",
        "body": "Complete the deterministic local public API acceptance.",
        "state": "open",
        "state_reason": None,
        "type": None,
        "repository": repository,
        "labels": labels,
        "comments": [],
        "updated_at": "2026-08-11T00:00:00Z",
    }
    source_projection = {
        "number": 1,
        "contract": contract,
        "labels": ["ready-for-agent"],
        "source_ref": _TICKET_KEY,
        "native_blockers": [],
    }
    return {
        "key": _TICKET_KEY,
        "labels": ["ready-for-agent"],
        "source": {
            "ref": _TICKET_KEY,
            "digest": digest_value(source_projection),
        },
        "contract": contract,
        "native_blockers": [],
    }


def _root_ticket(ticket_key: str, number: int) -> dict[str, Any]:
    value = _ticket()
    contract = value["contract"]
    contract["id"] = number
    contract["node_id"] = f"ISSUE_{number}"
    contract["number"] = number
    contract["title"] = f"V8 local root canary Ticket {number}"
    contract["body"] = "Complete one deterministic local root-canary Work Run."
    contract["labels"][0]["id"] = number
    contract["labels"][0]["node_id"] = f"LABEL_READY_{number}"
    value["key"] = ticket_key
    value["source"]["ref"] = ticket_key
    value["source"]["digest"] = digest_value(
        {
            "number": number,
            "contract": contract,
            "labels": ["ready-for-agent"],
            "source_ref": ticket_key,
            "native_blockers": [],
        }
    )
    return value


def _snapshot() -> dict[str, Any]:
    return {
        "repository": _REPOSITORY,
        "target_branch": _TARGET_BRANCH,
        "campaign_source": _campaign_source(),
        "policy": _policy(),
        "tickets": [_ticket()],
    }


def _root_snapshot(
    root_repository: "_RootGitRepository | None" = None,
) -> dict[str, Any]:
    return {
        "repository": _REPOSITORY,
        "target_branch": _TARGET_BRANCH,
        "campaign_source": _campaign_source(root_repository),
        "policy": _policy(),
        "tickets": [
            _root_ticket(ticket_key, 100 + ordinal)
            for ordinal, ticket_key in enumerate(_ROOT_TICKET_KEYS, start=1)
        ],
    }


class _LocalSnapshotSource:
    def __init__(
        self,
        ticket_keys: tuple[str, ...] = (_TICKET_KEY,),
        root_repository: "_RootGitRepository | None" = None,
    ) -> None:
        self.ticket_keys = tuple(ticket_keys)
        self.root_repository = root_repository

    def snapshot(self, repository: str, ready_refs: tuple[str, ...]) -> dict[str, Any]:
        if repository != _REPOSITORY or ready_refs != self.ticket_keys:
            raise AssertionError("local acceptance source identity changed")
        return (
            _snapshot()
            if self.ticket_keys == (_TICKET_KEY,)
            else _root_snapshot(self.root_repository)
        )


@dataclass
class _LocalPlanningGateway:
    artifacts: ArtifactStore
    calls: list[str]
    ticket_keys: tuple[str, ...] = (_TICKET_KEY,)
    exclusive_resources: dict[str, tuple[str, ...]] | None = None

    def planning_preflight(self, subject) -> PlanningPreflightReceipt:
        self.calls.append("planning_preflight")
        return PlanningPreflightReceipt(
            subject_digest=subject.digest,
            stable_action_id=subject.stable_action_id,
            receipt_digest=digest_value(
                {
                    "kind": "local-planning-preflight.v1",
                    "subject_digest": subject.digest,
                }
            ),
        )

    def progress(
        self,
        subject,
        preflight: PlanningPreflightReceipt,
    ) -> PlanningReceipt:
        self.calls.append("planning_progress")
        resources = self.exclusive_resources or {
            ticket_key: () for ticket_key in self.ticket_keys
        }
        payload = {
            "admitted_work": list(self.ticket_keys),
            "dependency_additions": [],
            "exclusive_resources": {
                ticket_key: list(resources.get(ticket_key, ()))
                for ticket_key in self.ticket_keys
            },
            "capability_requirements": {
                ticket_key: ["git", "local_check"] for ticket_key in self.ticket_keys
            },
            "decision_requirements": [],
        }
        output = self.artifacts.put_canonical(
            {
                "schema_version": "gwo.runtime.output.v1",
                "subject_digest": subject.digest,
                "stable_action_id": subject.stable_action_id,
                "authority_digest": subject.authority_digest,
                "payload": payload,
            }
        )
        return PlanningReceipt(
            subject_digest=subject.digest,
            stable_action_id=subject.stable_action_id,
            status="completed",
            receipt_digest=digest_value(
                {
                    "kind": "local-planning-receipt.v1",
                    "subject_digest": subject.digest,
                    "output_digest": output.digest,
                }
            ),
            output_artifact_digest=output.digest,
            planning_output_artifact_digest=output.digest,
        )


def _candidate_for(action: WorkRunAction) -> CandidateReceipt:
    diff = _candidate_diff_for()
    return CandidateReceipt(
        parent_digest=digest_value(
            {"kind": "local-candidate-parent.v1", "work": action.work_run_key}
        ),
        repository=action.repository,
        campaign_key=action.campaign_key,
        campaign_handle=action.campaign_key,
        plan_revision_digest=action.plan_revision_digest,
        work_run_key=action.work_run_key,
        ticket_key=action.ticket_key,
        reported_reference="refs/heads/local-candidate",
        base_commit_oid=diff.base_commit_oid,
        base_tree_oid=diff.base_tree_oid,
        candidate_commit_oid=diff.candidate_commit_oid,
        candidate_tree_oid=diff.candidate_tree_oid,
        diff_schema_version=diff.schema_version,
        diff_record_digest=diff.digest,
        authority_subtree_digest=digest_value(
            {"kind": "local-worker-authority.v1", "ticket": action.ticket_key}
        ),
        runtime_subject_digest=action.work_subject_digest,
    )


def _candidate_diff_for() -> CandidateDiffRecordV1:
    return CandidateDiffRecordV1(
        schema_version="CandidateDiffRecordV1",
        repository_object_format="sha1",
        base_commit_oid="2" * 40,
        base_tree_oid="3" * 40,
        candidate_commit_oid="4" * 40,
        candidate_tree_oid="5" * 40,
        entries=(),
    )


def _accepted_for(
    action: WorkRunAction,
    candidate: CandidateReceipt,
) -> AcceptedCandidateReceipt:
    evidence_digest = digest_value(
        {"kind": "local-review-evidence.v1", "candidate": candidate.digest}
    )
    return AcceptedCandidateReceipt(
        repository=action.repository,
        campaign_key=action.campaign_key,
        plan_revision_digest=action.plan_revision_digest,
        target_branch=_TARGET_BRANCH,
        ticket_key=action.ticket_key,
        work_run_key=action.work_run_key,
        integration_node_key=f"integration:{action.ticket_key}",
        accepted_sequence=1,
        base_sha=candidate.base_commit_oid,
        base_tree_oid=candidate.base_tree_oid,
        candidate_sha=candidate.candidate_commit_oid,
        candidate_tree_oid=candidate.candidate_tree_oid,
        candidate_receipt_digest=candidate.digest,
        diff_record_digest=candidate.diff_record_digest,
        authority_subtree_digest=candidate.authority_subtree_digest,
        policy_witness_digest=digest_value(
            {"kind": "local-policy-witness.v1", "repository": action.repository}
        ),
        review_subject_digest=digest_value(
            {"kind": "local-review-subject.v1", "candidate": candidate.digest}
        ),
        assurance="standard",
        assurance_requirement_digest=digest_value(
            {"kind": "local-assurance.v1", "mode": "standard"}
        ),
        check_environment_digest=digest_value(
            {"kind": "local-check-environment.v1", "python": "deterministic"}
        ),
        delivery_identity_digest=digest_value(
            {"kind": "local-delivery-identity.v1", "candidate": candidate.digest}
        ),
        interaction_keys=(
            InteractionKey(
                "candidate-path",
                "src/local_acceptance.py",
                InteractionClassification.ORDINARY,
            ),
        ),
        protected_surfaces=(),
        gitlink_change=False,
        evidence_digests=(evidence_digest,),
        review_finding_ledger_digest=digest_value(
            {"kind": "local-review-ledger.v1", "evidence": evidence_digest}
        ),
    )


def _root_assurance(ticket_key: str) -> str:
    return "strict" if ticket_key == _ROOT_STRICT_TICKET else "standard"


def _root_path_token(path: str) -> str:
    return base64.urlsafe_b64encode(path.encode("utf-8")).decode("ascii").rstrip("=")


def _git(repository: Path, *arguments: str, env: dict[str, str] | None = None) -> str:
    completed = subprocess.run(
        ("git", *arguments),
        cwd=repository,
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()
        raise RuntimeError(detail or f"git {' '.join(arguments)} failed")
    return completed.stdout.strip()


def _git_is_ancestor(repository: Path, ancestor: str, descendant: str) -> bool:
    completed = subprocess.run(
        ("git", "merge-base", "--is-ancestor", ancestor, descendant),
        cwd=repository,
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode not in {0, 1}:
        detail = (completed.stderr or completed.stdout).strip()
        raise LocalAcceptanceFailure(detail or "git ancestry readback failed")
    return completed.returncode == 0


@dataclass(frozen=True)
class _RootGitRepository:
    path: Path
    base_commit: str
    base_tree: str
    candidate_refs: dict[tuple[str, str], str]


def _initialize_root_git_repository(root: Path) -> _RootGitRepository:
    repository = root / "repository"
    repository.mkdir(parents=True, exist_ok=True)
    _git(repository, "init", "--quiet", "--initial-branch=main")
    _git(repository, "config", "user.email", "gwo-v8-local@example.invalid")
    _git(repository, "config", "user.name", "GWO V8 Local Acceptance")
    commit_env = os.environ.copy()
    commit_env.update(
        {
            "GIT_AUTHOR_NAME": "GWO V8 Local Acceptance",
            "GIT_AUTHOR_EMAIL": "gwo-v8-local@example.invalid",
            "GIT_AUTHOR_DATE": "2026-08-11T00:00:00Z",
            "GIT_COMMITTER_NAME": "GWO V8 Local Acceptance",
            "GIT_COMMITTER_EMAIL": "gwo-v8-local@example.invalid",
            "GIT_COMMITTER_DATE": "2026-08-11T00:00:00Z",
        }
    )
    (repository / "README.md").write_text(
        "deterministic V8 local root canary\n", encoding="utf-8"
    )
    _git(repository, "add", "--", "README.md")
    _git(repository, "commit", "--quiet", "-m", "root canary base", env=commit_env)
    base_commit = _git(repository, "rev-parse", "refs/heads/main^{commit}")
    base_tree = _git(repository, "rev-parse", "refs/heads/main^{tree}")
    definitions = {
        ("issue:101", "accepted"): (
            "src/root/issue-101.py",
            "STANDARD_101 = True\n",
            "issue 101 candidate",
        ),
        ("issue:102", "initial"): (
            "src/root/issue-102.py",
            "REPAIR_REQUIRED = True\n",
            "issue 102 initial candidate",
        ),
        ("issue:102", "repaired"): (
            "src/root/issue-102.py",
            "REPAIR_REQUIRED = False\n",
            "issue 102 repaired candidate",
        ),
        ("issue:103", "rejected"): (
            "src/root/issue-103.py",
            "REJECTED = True\n",
            "issue 103 rejected candidate",
        ),
        ("issue:103", "replacement"): (
            "src/root/issue-103.py",
            "REJECTED = False\n",
            "issue 103 replacement candidate",
        ),
        ("issue:104", "accepted"): (
            "src/root/strict-surface.py",
            "STRICT_104 = True\n",
            "issue 104 strict candidate",
        ),
    }
    candidate_refs: dict[tuple[str, str], str] = {}
    for ordinal, (
        (ticket_key, variant),
        (relative_path, content, message),
    ) in enumerate(definitions.items(), start=1):
        branch = f"candidate-{ticket_key.replace(':', '-')}-{variant}"
        _git(repository, "switch", "--quiet", "-c", branch, base_commit)
        path = repository / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        _git(repository, "add", "--", relative_path)
        _git(
            repository,
            "commit",
            "--quiet",
            "-m",
            f"{message} {ordinal}",
            env=commit_env,
        )
        candidate_refs[(ticket_key, variant)] = f"refs/heads/{branch}"
    _git(repository, "switch", "--quiet", "main")
    return _RootGitRepository(repository, base_commit, base_tree, candidate_refs)


class _RootBaseReader:
    def __init__(self, repository: _RootGitRepository):
        self.repository = repository

    def read_base(self, _repository: str) -> tuple[str, str]:
        return self.repository.base_commit, self.repository.base_tree


class _RootCandidateStore:
    def __init__(self, store_path: Path):
        self.store_path = Path(store_path)
        self.records: dict[str, CandidateDiffRecordV1] = {}
        with _connection(self.store_path) as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS root_candidate_diffs "
                "(record_digest TEXT PRIMARY KEY, record_json BLOB NOT NULL)"
            )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS root_candidate_transitions "
                "(sequence INTEGER PRIMARY KEY AUTOINCREMENT, ticket_key TEXT NOT NULL, "
                "stage TEXT NOT NULL, transition_json BLOB NOT NULL)"
            )

    def put(self, record: CandidateDiffRecordV1) -> str:
        self.records[record.digest] = record
        with _connection(self.store_path) as connection:
            connection.execute(
                "INSERT OR REPLACE INTO root_candidate_diffs(record_digest, record_json) "
                "VALUES (?, ?)",
                (record.digest, canonical_bytes(record.canonical())),
            )
        return record.digest

    def read(self, digest: str) -> CandidateDiffRecordV1 | None:
        record = self.records.get(digest)
        if record is not None:
            return record
        with _connection(self.store_path) as connection:
            row = connection.execute(
                "SELECT record_json FROM root_candidate_diffs WHERE record_digest = ?",
                (digest,),
            ).fetchone()
        if row is None:
            return None
        value = load_canonical_json(row[0])
        record = CandidateDiffRecordV1(
            schema_version=value["schema_version"],
            repository_object_format=value["repository_object_format"],
            base_commit_oid=value["base"]["commit_oid"],
            base_tree_oid=value["base"]["tree_oid"],
            candidate_commit_oid=value["candidate"]["commit_oid"],
            candidate_tree_oid=value["candidate"]["tree_oid"],
            entries=tuple(CandidateDiffEntryV1(**entry) for entry in value["entries"]),
            record_digest=value["record_digest"],
        )
        self.records[digest] = record
        return record

    def persist_transition(
        self,
        ticket_key: str,
        stage: str,
        result: object,
    ) -> None:
        transition = {
            "ticket_key": ticket_key,
            "stage": stage,
            "status": result.status.value,
            "candidate_receipt": (
                None
                if result.candidate_receipt is None
                else result.candidate_receipt.canonical()
            ),
            "candidate_diff": (
                None
                if result.candidate_diff_record is None
                else result.candidate_diff_record.canonical()
            ),
            "accepted_candidate_receipt": (
                None
                if result.accepted_candidate_receipt is None
                else result.accepted_candidate_receipt.canonical()
            ),
            "assurance_requirement": (
                None
                if result.assurance_requirement is None
                else result.assurance_requirement.canonical()
            ),
            "review_subject": (
                None
                if result.review_subject is None
                else result.review_subject.canonical()
            ),
            "repair_packet": (
                None
                if result.repair_packet is None
                else result.repair_packet.canonical()
            ),
            "review_finding_ledger_digest": result.review_finding_ledger_digest,
            "evidence": [
                item.canonical()
                for item in result.evidence
                if callable(getattr(item, "canonical", None))
            ],
        }
        with _connection(self.store_path) as connection:
            connection.execute(
                "INSERT INTO root_candidate_transitions(ticket_key, stage, transition_json) "
                "VALUES (?, ?, ?)",
                (ticket_key, stage, canonical_bytes(transition)),
            )

    def transitions(self) -> list[dict[str, Any]]:
        with _connection(self.store_path) as connection:
            rows = connection.execute(
                "SELECT ticket_key, stage, transition_json FROM root_candidate_transitions "
                "ORDER BY sequence"
            ).fetchall()
        transitions = []
        for ticket_key, stage, value in rows:
            transition = load_canonical_json(value)
            transition["ticket_key"] = ticket_key
            transition["stage"] = stage
            transition["persisted"] = True
            transitions.append(transition)
        return transitions


class _RootCandidateChecks:
    def __init__(self):
        self.calls: list[str] = []

    def run(self, _parent: CandidateGateParent, readback):
        self.calls.append(readback.candidate.reported_reference)
        failed = readback.candidate.reported_reference.endswith("issue-103-rejected")
        failure = (
            DeterministicAuditFailure(
                kind=AuditFailureKind.AFFECTED_CHECK,
                route=AuditFailureRoute.ORDINARY_UNAUTHORIZED,
                code="ROOT_CANDIDATE_CHECK_FAILED",
                detail="The rejected root Candidate did not pass its deterministic check.",
            )
            if failed
            else None
        )
        outcome = "failed" if failure is not None else "passed"
        observation_digest = digest_value(
            {
                "kind": "candidate_check_observation.v1",
                "check_id": "check:root-candidate",
                "candidate_tree_oid": readback.candidate.candidate_tree_oid,
                "diff_record_digest": readback.diff_record.digest,
                "outcome": outcome,
                "failure_digest": None if failure is None else failure.digest,
            }
        )
        return (
            CandidateCheckEvidence(
                check_id="check:root-candidate",
                candidate_tree_oid=readback.candidate.candidate_tree_oid,
                outcome=outcome,
                definition_digest=digest_value(
                    {"kind": "root-candidate-check.v1", "check": "git-tree"}
                ),
                observation_digest=observation_digest,
                failure=failure,
            ),
        )


class _RootAssurancePolicy:
    def derive(self, parent, _readback, _checks) -> AssuranceRequirement:
        strict = parent.runtime_subject.ticket_key == _ROOT_STRICT_TICKET
        return AssuranceRequirement(
            policy_id="policy:local-root-candidate",
            policy_version="1",
            mode=AssuranceMode.STRICT if strict else AssuranceMode.STANDARD,
            required_check_ids=("check:root-candidate",),
            standards=("standard:local-root",),
            specialist_policy_id="policy:local-root-specialist" if strict else None,
        )


class _RootFormalReviewer:
    capability_policy_proof = CapabilityPolicyProof(
        capability_policy=CapabilityPolicy(worker_can_edit_issues=False),
        authority_record_digest="9" * 64,
    )

    def __init__(self, repository: _RootGitRepository):
        self.repository = repository
        self.calls: list[dict[str, str | None]] = []

    def review(self, action):
        self.calls.append(
            {
                "candidate_commit_oid": action.subject.candidate_commit_oid,
                "kind": action.kind,
                "specialist_policy_id": action.specialist_policy_id,
            }
        )
        initial = _git(
            self.repository.path,
            "rev-parse",
            f"{self.repository.candidate_refs[('issue:102', 'initial')]}^{{commit}}",
        )
        if action.subject.candidate_commit_oid == initial:
            finding = FormalReviewFinding(
                parent_digest=action.subject.parent_digest,
                candidate_digest=action.subject.candidate_digest,
                review_subject_digest=action.subject.digest,
                finding_id="root-issue-102-repair",
                severity="hard",
                code="ROOT_REPAIR_REQUIRED",
                message="repair the flagged root candidate before acceptance",
            )
            return FormalReviewResult(
                subject_digest=action.subject.digest,
                findings=(finding,),
            )
        return FormalReviewResult(subject_digest=action.subject.digest)


class _RootRepairVerifier:
    capability_policy_proof = CapabilityPolicyProof(
        capability_policy=CapabilityPolicy(worker_can_edit_issues=False),
        authority_record_digest="8" * 64,
    )

    def __init__(self):
        self.calls: list[str] = []

    def verify(self, request):
        self.calls.append(request.candidate.candidate_commit_oid)
        return RepairVerificationResult(
            request_digest=request.digest,
            accepted=True,
            details=("the repaired root Candidate passed bounded verification",),
        )


class _RootNoopInvalidationReporter:
    def report_plan_invalidation(self, _subject, _evidence, _report):
        raise AssertionError("root CandidateGate must not report Plan Invalidation")


class _RootCandidateGateRunner:
    def __init__(self, repository: _RootGitRepository, store: _RootCandidateStore):
        self.repository = repository
        self.store = store
        self.reader = GitCandidateReader(
            repository_path=repository.path,
            base_reader=_RootBaseReader(repository),
        )
        self.checks = _RootCandidateChecks()
        self.policy = _RootAssurancePolicy()
        self.reviewer = _RootFormalReviewer(repository)
        self.verifier = _RootRepairVerifier()
        self.results: dict[str, object] = {}

    def _parent(self, action: WorkRunAction) -> CandidateGateParent:
        standard = action.ticket_key in _ROOT_STANDARD_TICKETS
        authority = digest_value(
            {
                "kind": "root-worker-authority.v1",
                "mode": "standard" if standard else "strict",
            }
        )
        subject = WorkRunSubject(
            repository=action.repository,
            campaign_key=action.campaign_key,
            campaign_handle=action.campaign_key,
            plan_revision_digest=action.plan_revision_digest,
            work_run_key=action.work_run_key,
            ticket_key=action.ticket_key,
            purpose=WorkRunPurpose.implementation(),
            prompt_artifact_digest=digest_value(
                {"kind": "root-worker-prompt.v1", "ticket": action.ticket_key}
            ),
            authority_subtree_digest=authority,
            stable_action_id=action.runtime_binding_id
            or f"binding:root:{action.ticket_key}",
        )
        number = int(action.ticket_key.split(":", 1)[1])
        return CandidateGateParent(
            runtime_subject=subject,
            ticket_contract_digest=_root_ticket(action.ticket_key, number)["source"][
                "digest"
            ],
            policy_witness_digest=_policy()["digest"],
            workspace_identity=f"workspace:root:{action.ticket_key}",
        )

    def _gate(self, action: WorkRunAction) -> CandidateGate:
        strict = action.ticket_key == _ROOT_STRICT_TICKET
        protected = (_root_path_token("src/root/strict-surface.py"),) if strict else ()
        mode = "strict" if strict else "standard"
        return CandidateGate(
            invalidation_reporter=_RootNoopInvalidationReporter(),
            candidate_reader=self.reader,
            formal_reviewer=self.reviewer,
            repair_verifier=self.verifier,
            check_runner=self.checks,
            assurance_policy=self.policy,
            acceptance_facts=CandidateAcceptanceFacts(
                target_branch=_TARGET_BRANCH,
                integration_node_key=f"integration:{action.ticket_key}",
                accepted_sequence=100 + int(action.ticket_key.split(":", 1)[1]),
                check_environment_digest=digest_value(
                    {"kind": "root-check-environment.v1", "mode": mode}
                ),
                delivery_identity_digest=digest_value(
                    {"kind": "root-delivery-identity.v1", "mode": mode}
                ),
                protected_surfaces=protected,
            ),
            diff_artifacts=self.store,
        )

    def _persist(self, action: WorkRunAction, stage: str, result: object) -> None:
        self.store.persist_transition(action.ticket_key, stage, result)
        self.results[action.ticket_key] = result

    @staticmethod
    def _bind_result_to_action(action: WorkRunAction, result: object) -> object:
        candidate = result.candidate_receipt
        if candidate is None:
            return result
        if candidate.runtime_subject_digest == action.work_subject_digest:
            return result
        candidate = replace(
            candidate,
            runtime_subject_digest=action.work_subject_digest,
            receipt_digest=None,
        )
        accepted = result.accepted_candidate_receipt
        if accepted is not None:
            accepted = replace(
                accepted,
                candidate_receipt_digest=candidate.digest,
            )
        return replace(
            result,
            candidate_receipt=candidate,
            accepted_candidate_receipt=accepted,
        )

    def run(self, action: WorkRunAction) -> object:
        existing = self.results.get(action.ticket_key)
        if existing is not None:
            return existing
        parent = self._parent(action)
        gate = self._gate(action)
        ticket_key = action.ticket_key
        if ticket_key == "issue:102":
            initial_ref = self.repository.candidate_refs[(ticket_key, "initial")]
            reviewed = gate.gate_candidate(parent, initial_ref)
            self._persist(action, "initial", reviewed)
            assert reviewed.repair_packet is not None
            assert reviewed.repair_packet.finding_ledger is not None
            ledger = reviewed.repair_packet.finding_ledger.with_disposition(
                finding_id=reviewed.repair_packet.finding_ledger.entries[
                    0
                ].finding.finding_id,
                disposition=ReviewFindingDisposition.FIXED,
                reason="the local root repair changed only the approved path",
            )
            packet = reviewed.repair_packet.with_ledger(ledger.entries)
            repaired_ref = self.repository.candidate_refs[(ticket_key, "repaired")]
            repaired = self.reader.read_candidate(_REPOSITORY, repaired_ref)
            result = gate.verify_repair(parent, packet, repaired.candidate)
            result = self._bind_result_to_action(action, result)
            self._persist(action, "repair", result)
            return result
        if ticket_key == "issue:103":
            rejected_ref = self.repository.candidate_refs[(ticket_key, "rejected")]
            rejected = gate.gate_candidate(parent, rejected_ref)
            self._persist(action, "initial", rejected)
            replacement_ref = self.repository.candidate_refs[
                (ticket_key, "replacement")
            ]
            result = gate.gate_candidate(parent, replacement_ref)
            result = self._bind_result_to_action(action, result)
            self._persist(action, "replacement", result)
            return result
        variant = "accepted"
        reference = self.repository.candidate_refs[(ticket_key, variant)]
        result = gate.gate_candidate(parent, reference)
        result = self._bind_result_to_action(action, result)
        self._persist(action, "initial", result)
        return result


class _LocalDeliveryStub:
    def __init__(self, store_path: Path, scenario: str = "single"):
        self.store_path = Path(store_path)
        self.scenario = scenario
        self.store_path.parent.mkdir(parents=True, exist_ok=True)
        with _connection(self.store_path) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS deliveries(
                    stable_action_id TEXT PRIMARY KEY,
                    observation_json BLOB NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS counters(
                    singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
                    execute_calls INTEGER NOT NULL
                )
                """
            )
            connection.execute(
                """
                INSERT INTO counters(singleton, execute_calls)
                VALUES (1, 0)
                ON CONFLICT(singleton) DO NOTHING
                """
            )

    @property
    def execute_calls(self) -> int:
        with _connection(self.store_path) as connection:
            row = connection.execute(
                "SELECT execute_calls FROM counters WHERE singleton = 1"
            ).fetchone()
        assert row is not None
        return int(row[0])

    def _observation(self, action: WorkRunAction) -> WorkRunObservation:
        candidate = _candidate_for(action)
        accepted = _accepted_for(action, candidate)
        request_digest = action.batch_delivery_request_digest
        if request_digest is None:
            raise AssertionError("Batch delivery request digest was not bound")
        batch_id = digest_value(
            {"kind": "local-batch.v1", "request_digest": request_digest}
        )
        batch_sha = candidate.candidate_commit_oid
        member_ticket_keys = (action.ticket_key,)
        target_head_sha = "6" * 40
        evidence_digests = accepted.evidence_digests
        delivery = BatchDeliveryProof.create(
            delivery_stable_action_id=action.stable_action_id,
            delivery_request_digest=request_digest,
            batch_id=batch_id,
            batch_sha=batch_sha,
            member_ticket_keys=member_ticket_keys,
            local_check_receipt_digest=digest_value(
                {"kind": "local-check.v1", "request_digest": request_digest}
            ),
            publication_receipt_digest=digest_value(
                {"kind": "local-publication.v1", "batch_id": batch_id}
            ),
            pull_request_number=1,
            pull_request_head_sha=batch_sha,
            hosted_result_receipt_digest=digest_value(
                {"kind": "local-hosted-readback.v1", "batch_id": batch_id}
            ),
            integration_lease_digest=digest_value(
                {"kind": "local-integration-lease.v1", "batch_id": batch_id}
            ),
            target_branch=_TARGET_BRANCH,
            target_head_sha=target_head_sha,
            target_readback_digest=digest_value(
                {"kind": "local-target-readback.v1", "batch_id": batch_id}
            ),
            target_contains_batch_sha=True,
            pull_request_merge_target_sha=target_head_sha,
            merge_method="merge",
        )
        delivery_receipt_digest = digest_value(
            {
                "kind": "local-batch-observation.v1",
                "action": action.stable_action_id,
                "proof": delivery.proof_digest,
            }
        )
        proof = ResultIntegrityProof(
            accepted_candidate_receipt_digest=accepted.digest,
            candidate_commit_oid=candidate.candidate_commit_oid,
            candidate_tree_oid=candidate.candidate_tree_oid,
            candidate_diff_record_digest=candidate.diff_record_digest,
            batch_delivery_receipt_digest=delivery_receipt_digest,
            batch_delivery_stable_action_id=action.stable_action_id,
            batch_delivery_request_digest=request_digest,
            batch_delivery_batch_id=delivery.batch_id,
            batch_delivery_batch_sha=delivery.batch_sha,
            batch_delivery_proof_digest=delivery.proof_digest,
            delivery_stable_action_id=delivery.delivery_stable_action_id,
            delivery_request_digest=delivery.delivery_request_digest,
            batch_id=delivery.batch_id,
            batch_sha=delivery.batch_sha,
            delivery_member_ticket_keys=delivery.member_ticket_keys,
            local_check_receipt_digest=delivery.local_check_receipt_digest,
            publication_receipt_digest=delivery.publication_receipt_digest,
            pull_request_number=delivery.pull_request_number,
            pull_request_head_sha=delivery.pull_request_head_sha,
            hosted_result_receipt_digest=delivery.hosted_result_receipt_digest,
            integration_lease_digest=delivery.integration_lease_digest,
            target_branch=delivery.target_branch,
            target_head_sha=delivery.target_head_sha,
            target_readback_digest=delivery.target_readback_digest,
            target_contains_batch_sha=delivery.target_contains_batch_sha,
            pull_request_merge_target_sha=delivery.pull_request_merge_target_sha,
            merge_method=delivery.merge_method,
            result_digest="",
            evidence_digests=evidence_digests,
        )
        proof = replace(proof, result_digest=proof.expected_result_digest())
        return WorkRunObservation(
            phase="completed",
            stable_action_id=action.stable_action_id,
            runtime_binding_id=action.runtime_binding_id,
            receipt_digest=delivery_receipt_digest,
            candidate_receipt=candidate,
            accepted_candidate_receipt_digest=accepted.digest,
            candidate_diff_record_digest=candidate.diff_record_digest,
            delivery_receipt_digest=delivery_receipt_digest,
            result_digest=proof.result_digest,
            evidence_digests=proof.evidence_digests,
            result_integrity=proof,
        )

    def readback(self, action: WorkRunAction) -> WorkRunObservation | None:
        with _connection(self.store_path) as connection:
            row = connection.execute(
                "SELECT observation_json FROM deliveries WHERE stable_action_id = ?",
                (action.stable_action_id,),
            ).fetchone()
        if row is None:
            return None
        return WorkRunObservation.from_canonical(load_canonical_json(row[0]))

    def readbacks(self) -> tuple[WorkRunObservation, ...]:
        with _connection(self.store_path) as connection:
            rows = connection.execute(
                "SELECT observation_json FROM deliveries ORDER BY stable_action_id"
            ).fetchall()
        return tuple(
            WorkRunObservation.from_canonical(load_canonical_json(row[0]))
            for row in rows
        )

    def execute(self, action: WorkRunAction) -> WorkRunObservation:
        existing = self.readback(action)
        if existing is not None:
            return existing
        observation = self._observation(action)
        rendered = canonical_bytes(observation.canonical())
        with _connection(self.store_path) as connection:
            connection.execute(
                "UPDATE counters SET execute_calls = execute_calls + 1 WHERE singleton = 1"
            )
            connection.execute(
                "INSERT INTO deliveries(stable_action_id, observation_json) VALUES (?, ?)",
                (action.stable_action_id, rendered),
            )
        return observation


class _RootLocalSuiteDriver:
    def __init__(self, repository: _RootGitRepository):
        self.repository = repository
        self.calls: list[str] = []

    def run(self, batch_sha: str, suite: LocalSuiteDefinition) -> LocalCheckReceipt:
        _git(self.repository.path, "cat-file", "-e", f"{batch_sha}^{{commit}}")
        completed = subprocess.run(
            suite.command,
            cwd=self.repository.path,
            check=False,
            capture_output=True,
            text=True,
        )
        self.calls.append(batch_sha)
        outcome = "passed" if completed.returncode == 0 else "code_failure"
        return LocalCheckReceipt.create(
            batch_sha=batch_sha,
            suite_id=suite.suite_id,
            definition_digest=suite.definition_digest,
            outcome=outcome,
            source_ref=f"refs/gwo-v8/integration-batches/{batch_sha}",
            stdout_digest=digest_value(
                {"kind": "root-local-stdout.v1", "value": completed.stdout}
            ),
            stderr_digest=digest_value(
                {"kind": "root-local-stderr.v1", "value": completed.stderr}
            ),
        )


class _RootHostedDriver:
    def __init__(self, repository: _RootGitRepository, git: GitCliBatchDriver):
        self.repository = repository
        self.git = git
        self.publication_manifests: dict[tuple[str, str], str] = {}
        self.publication_calls: list[str] = []
        self.integration_calls: list[str] = []
        self.retry_calls: list[str] = []

    @staticmethod
    def _publication(
        repository: str,
        batch_sha: str,
        manifest_digest: str,
        source_ref: str,
    ) -> BatchPublicationReceipt:
        body = {
            "repository": repository,
            "batch_sha": batch_sha,
            "branch_ref": f"refs/gwo-v8/publications/{batch_sha}",
            "evidence_manifest_digest": manifest_digest,
            "source_ref": source_ref,
        }
        return BatchPublicationReceipt(
            **body,
            receipt_digest=digest_value({"kind": "batch-publication.v1", **body}),
        )

    def read_publication(
        self, repository: str, batch_sha: str
    ) -> BatchPublicationReceipt | None:
        ref = f"refs/gwo-v8/publications/{batch_sha}"
        if self.git.read_ref(ref) is None:
            return None
        manifest = self.publication_manifests.get(
            (repository, batch_sha),
            digest_value(
                {"kind": "root-publication-manifest.v1", "batch_sha": batch_sha}
            ),
        )
        return self._publication(
            repository,
            batch_sha,
            manifest,
            "git:publication-readback",
        )

    def publish_once(
        self, repository: str, batch_sha: str, manifest_digest: str
    ) -> BatchPublicationReceipt:
        ref = f"refs/gwo-v8/publications/{batch_sha}"
        current = self.git.read_ref(ref)
        if current is None:
            self.git.update_ref_cas(ref, None, batch_sha)
        elif current != batch_sha:
            raise LocalAcceptanceFailure("publication ref changed its Batch identity")
        self.publication_manifests[(repository, batch_sha)] = manifest_digest
        self.publication_calls.append(batch_sha)
        return self._publication(
            repository, batch_sha, manifest_digest, "git:publication"
        )

    def read_pull_request(self, repository: str, batch_sha: str) -> PullRequestReadback:
        publication_ref = f"refs/gwo-v8/publications/{batch_sha}"
        if self.git.read_ref(publication_ref) != batch_sha:
            raise LocalAcceptanceFailure("published Batch ref did not read back")
        body = {
            "number": 1,
            "repository": repository,
            "head_sha": batch_sha,
            "base_branch": _TARGET_BRANCH,
            "merge_commit_sha": None,
            "merge_method": "merge",
            "source_ref": "git:pull-request-readback",
        }
        return PullRequestReadback(
            **body,
            readback_digest=digest_value({"kind": "pull-request-readback.v1", **body}),
        )

    def read_hosted_result(
        self,
        repository: str,
        batch_sha: str,
        suite: HostedSuiteDefinition,
    ) -> HostedResultObservation:
        body = {
            "repository": repository,
            "batch_sha": batch_sha,
            "suite_id": suite.suite_id,
            "provider_check_id": "check:1",
            "outcome": "passed",
            "source_ref": "git:hosted-check-readback",
        }
        return HostedResultObservation(
            **body,
            observation_digest=digest_value(
                {"kind": "root-hosted-observation.v1", **body}
            ),
        )

    def retry_hosted(
        self,
        _repository: str,
        batch_sha: str,
        _provider_check_id: str,
        _idempotency_key: str,
    ) -> None:
        self.retry_calls.append(batch_sha)

    def integrate_serially(
        self,
        repository: str,
        batch_sha: str,
        target: BatchTarget,
        pull_request: PullRequestReadback,
    ) -> TargetIntegrationReadback:
        if (
            pull_request.repository != repository
            or pull_request.head_sha != batch_sha
            or pull_request.base_branch != target.target_branch
            or pull_request.merge_method != "merge"
        ):
            raise LocalAcceptanceFailure("target integration identity changed")
        before = self.git.read_target(target)
        target_ref = f"refs/heads/{target.target_branch}"
        self.git.update_ref_cas(target_ref, before.target_head_sha, batch_sha)
        after = self.git.read_target(target, allow_advance=True)
        ancestor = self.git.read_ancestor(batch_sha, after.target_head_sha)
        if not ancestor.is_ancestor:
            raise LocalAcceptanceFailure("target readback lost Batch ancestry")
        self.integration_calls.append(batch_sha)
        body = {
            "repository": repository,
            "target_branch": after.target_branch,
            "target_head_sha": after.target_head_sha,
            "batch_sha": batch_sha,
            "pull_request_number": pull_request.number,
            "pull_request_head_sha": pull_request.head_sha,
            "merge_commit_sha": after.target_head_sha,
            "merge_method": "merge",
            "batch_is_ancestor": ancestor.is_ancestor,
            "source_ref": "git:target-readback",
        }
        return TargetIntegrationReadback(
            **body,
            readback_digest=digest_value({"kind": "target-readback.v1", **body}),
        )


class _RootBatchDelivery:
    def __init__(self, store_path: Path, repository: _RootGitRepository):
        self.store_path = Path(store_path)
        self.repository = repository
        self.store_path.parent.mkdir(parents=True, exist_ok=True)
        self.journal = SqliteBatchDeliveryJournal(self.store_path)
        self.boundaries: list[str] = []
        self.git = GitCliBatchDriver(
            repository.path,
            crash_hook=lambda boundary: self.boundaries.append(boundary),
        )
        self.local = _RootLocalSuiteDriver(repository)
        self.hosted = _RootHostedDriver(repository, self.git)
        self.integrator = BatchIntegrator(
            journal=self.journal,
            git=self.git,
            local=self.local,
            hosted=self.hosted,
            configuration=BatchIntegratorConfiguration(
                host_member_limit=4,
                repository_member_limits={_REPOSITORY: 4},
            ),
        )
        self.requests: dict[str, BatchDeliveryRequest] = {}
        self.groups: dict[str, dict[str, Any]] = {}
        self.candidates: dict[str, dict[str, CandidateReceipt]] = {}
        self.accepted: dict[str, dict[str, AcceptedCandidateReceipt]] = {}
        with _connection(self.store_path) as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS root_deliveries "
                "(stable_action_id TEXT PRIMARY KEY, observation_json BLOB NOT NULL)"
            )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS root_delivery_groups "
                "(group_key TEXT PRIMARY KEY, group_json BLOB NOT NULL)"
            )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS root_delivery_counters "
                "(singleton INTEGER PRIMARY KEY CHECK(singleton = 1), execute_calls INTEGER NOT NULL)"
            )
            connection.execute(
                "INSERT INTO root_delivery_counters(singleton, execute_calls) "
                "VALUES (1, 0) ON CONFLICT(singleton) DO NOTHING"
            )

    @property
    def execute_calls(self) -> int:
        with _connection(self.store_path) as connection:
            row = connection.execute(
                "SELECT execute_calls FROM root_delivery_counters WHERE singleton = 1"
            ).fetchone()
        assert row is not None
        return int(row[0])

    @staticmethod
    def _group_for(ticket_key: str) -> str:
        return "standard" if ticket_key in _ROOT_STANDARD_TICKETS else "strict"

    def _target(self) -> BatchTarget:
        head = _git(self.repository.path, "rev-parse", "refs/heads/main^{commit}")
        tree = _git(self.repository.path, "rev-parse", f"{head}^{{tree}}")
        facts = {
            "repository": _REPOSITORY,
            "target_branch": _TARGET_BRANCH,
            "target_head_sha": head,
            "target_tree_oid": tree,
        }
        return BatchTarget(
            repository=_REPOSITORY,
            target_branch=_TARGET_BRANCH,
            target_head_sha=head,
            target_tree_oid=tree,
            target_facts_digest=digest_value({"kind": "root-target-facts.v1", **facts}),
        )

    def bind(
        self,
        action: WorkRunAction,
        candidates: dict[str, CandidateReceipt],
        accepted: dict[str, AcceptedCandidateReceipt],
    ) -> str:
        group = self._group_for(action.ticket_key)
        prior = self.requests.get(group)
        if prior is not None:
            if action.ticket_key not in accepted:
                raise LocalAcceptanceFailure(
                    "Batch member was not CandidateGate accepted"
                )
            return prior.request_digest
        member_keys = (
            _ROOT_STANDARD_TICKETS if group == "standard" else (_ROOT_STRICT_TICKET,)
        )
        if any(ticket_key not in accepted for ticket_key in member_keys):
            raise LocalAcceptanceFailure(
                f"{group} Batch was bound before all accepted members were read back"
            )
        members = tuple(accepted[ticket_key] for ticket_key in member_keys)
        request = BatchDeliveryRequest(
            stable_action_id=action.stable_action_id,
            repository=action.repository,
            campaign_key=action.campaign_key,
            plan_revision_digest=action.plan_revision_digest,
            target=self._target(),
            accepted_candidates=members,
            local_suite=LocalSuiteDefinition(
                suite_id=f"root-local-{group}",
                definition_digest=digest_value(
                    {"kind": "root-local-suite.v1", "group": group}
                ),
                command=(
                    sys.executable,
                    "-c",
                    "print('gwo-v8 root local suite')",
                ),
            ),
            hosted_suites=(
                HostedSuiteDefinition(
                    suite_id=f"root-hosted-{group}",
                    hosted_name="GWO V8 local hosted check",
                    definition_digest=digest_value(
                        {"kind": "root-hosted-suite.v1", "group": group}
                    ),
                ),
            ),
            writer_generation="writer:local",
            activation_id=action.campaign_key,
        )
        self.requests[group] = request
        self.candidates[group] = dict(candidates)
        self.accepted[group] = dict(accepted)
        return request.request_digest

    def _proof_for(
        self,
        action: WorkRunAction,
        request: BatchDeliveryRequest,
        operation: object,
    ) -> BatchDeliveryProof:
        proofs = operation.delivery_proofs
        if len(proofs) != 1:
            raise LocalAcceptanceFailure(
                "BatchIntegrator did not produce one shared proof"
            )
        proof = proofs[0]
        return BatchDeliveryProof.create(
            delivery_stable_action_id=action.stable_action_id,
            delivery_request_digest=request.request_digest,
            batch_id=operation.batch_id,
            batch_sha=operation.batch_sha,
            member_ticket_keys=proof.member_ticket_keys,
            local_check_receipt_digest=proof.local_check_receipt_digest,
            publication_receipt_digest=proof.publication_receipt_digest,
            pull_request_number=proof.pull_request_number,
            pull_request_head_sha=proof.pull_request_head_sha,
            hosted_result_receipt_digest=proof.hosted_result_receipt_digest,
            integration_lease_digest=proof.integration_lease_digest,
            target_branch=proof.target_branch,
            target_head_sha=proof.target_head_sha,
            target_readback_digest=proof.target_readback_digest,
            target_contains_batch_sha=proof.target_contains_batch_sha,
            pull_request_merge_target_sha=proof.pull_request_merge_target_sha,
            merge_method=proof.merge_method,
        )

    def _save_observation(self, observation: WorkRunObservation) -> None:
        rendered = canonical_bytes(observation.canonical())
        with _connection(self.store_path) as connection:
            connection.execute(
                "INSERT INTO root_deliveries(stable_action_id, observation_json) "
                "VALUES (?, ?) ON CONFLICT(stable_action_id) DO NOTHING",
                (observation.stable_action_id, rendered),
            )

    def readback(self, action: WorkRunAction) -> WorkRunObservation | None:
        with _connection(self.store_path) as connection:
            row = connection.execute(
                "SELECT observation_json FROM root_deliveries "
                "WHERE stable_action_id = ?",
                (action.stable_action_id,),
            ).fetchone()
        if row is None:
            return None
        return WorkRunObservation.from_canonical(load_canonical_json(row[0]))

    def readbacks(self) -> tuple[WorkRunObservation, ...]:
        with _connection(self.store_path) as connection:
            rows = connection.execute(
                "SELECT observation_json FROM root_deliveries ORDER BY stable_action_id"
            ).fetchall()
        return tuple(
            WorkRunObservation.from_canonical(load_canonical_json(row[0]))
            for row in rows
        )

    def execute(
        self,
        action: WorkRunAction,
        candidates: dict[str, CandidateReceipt],
        accepted: dict[str, AcceptedCandidateReceipt],
    ) -> WorkRunObservation:
        existing = self.readback(action)
        if existing is not None:
            return existing
        request_digest = action.batch_delivery_request_digest
        if request_digest is None:
            raise LocalAcceptanceFailure("Batch delivery request digest was not bound")
        group = self._group_for(action.ticket_key)
        if group not in self.requests:
            self.bind(action, candidates, accepted)
        request = self.requests[group]
        if request.request_digest != request_digest:
            raise LocalAcceptanceFailure("Batch delivery request identity changed")
        operation = self.groups.get(group)
        if operation is None:
            batch_action = self.integrator.prepare(request)
            batch_observation = self.integrator.execute(batch_action)
            if batch_observation.phase != "complete":
                raise LocalAcceptanceFailure(
                    "root BatchIntegrator did not complete its real delivery"
                )
            operation = {
                "action": batch_action,
                "observation": batch_observation,
                "proof": batch_observation.delivery_proofs[0],
            }
            self.groups[group] = operation
            group_record = {
                "group": group,
                "request": request.canonical(),
                "operation": batch_observation.canonical(),
                "integration_action_id": batch_action.stable_action_id,
                "batch_ref": f"refs/gwo-v8/integration-batches/{batch_observation.batch_id}",
                "batch_ref_sha": self.git.read_ref(
                    f"refs/gwo-v8/integration-batches/{batch_observation.batch_id}"
                ),
                "formation_calls": self.integrator.formation_calls,
                "compose_calls": self.git.compose_calls,
                "integration_calls": list(self.hosted.integration_calls),
            }
            with _connection(self.store_path) as connection:
                connection.execute(
                    "INSERT OR REPLACE INTO root_delivery_groups(group_key, group_json) "
                    "VALUES (?, ?)",
                    (group, canonical_bytes(group_record)),
                )
        candidate = candidates[action.ticket_key]
        accepted_candidate = accepted[action.ticket_key]
        proof = self._proof_for(action, request, operation["observation"])
        delivery_receipt_digest = digest_value(
            {
                "kind": "root-batch-observation.v1",
                "action": action.stable_action_id,
                "proof": proof.proof_digest,
            }
        )
        result_integrity = ResultIntegrityProof(
            accepted_candidate_receipt_digest=accepted_candidate.digest,
            candidate_commit_oid=candidate.candidate_commit_oid,
            candidate_tree_oid=candidate.candidate_tree_oid,
            candidate_diff_record_digest=candidate.diff_record_digest,
            batch_delivery_receipt_digest=delivery_receipt_digest,
            batch_delivery_stable_action_id=action.stable_action_id,
            batch_delivery_request_digest=request_digest,
            batch_delivery_batch_id=proof.batch_id,
            batch_delivery_batch_sha=proof.batch_sha,
            batch_delivery_proof_digest=proof.proof_digest,
            delivery_stable_action_id=proof.delivery_stable_action_id,
            delivery_request_digest=proof.delivery_request_digest,
            batch_id=proof.batch_id,
            batch_sha=proof.batch_sha,
            delivery_member_ticket_keys=proof.member_ticket_keys,
            local_check_receipt_digest=proof.local_check_receipt_digest,
            publication_receipt_digest=proof.publication_receipt_digest,
            pull_request_number=proof.pull_request_number,
            pull_request_head_sha=proof.pull_request_head_sha,
            hosted_result_receipt_digest=proof.hosted_result_receipt_digest,
            integration_lease_digest=proof.integration_lease_digest,
            target_branch=proof.target_branch,
            target_head_sha=proof.target_head_sha,
            target_readback_digest=proof.target_readback_digest,
            target_contains_batch_sha=proof.target_contains_batch_sha,
            pull_request_merge_target_sha=proof.pull_request_merge_target_sha,
            merge_method=proof.merge_method,
            result_digest="",
            evidence_digests=accepted_candidate.evidence_digests,
        )
        result_integrity = replace(
            result_integrity,
            result_digest=result_integrity.expected_result_digest(),
        )
        observation = WorkRunObservation(
            phase="completed",
            stable_action_id=action.stable_action_id,
            receipt_digest=delivery_receipt_digest,
            candidate_identity=f"candidate:{candidate.candidate_commit_oid}",
            candidate_receipt=candidate,
            runtime_binding_id=action.runtime_binding_id,
            accepted_candidate_receipt_digest=accepted_candidate.digest,
            candidate_diff_record_digest=candidate.diff_record_digest,
            delivery_receipt_digest=delivery_receipt_digest,
            result_digest=result_integrity.result_digest,
            evidence_digests=result_integrity.evidence_digests,
            result_integrity=result_integrity,
        )
        with _connection(self.store_path) as connection:
            connection.execute(
                "UPDATE root_delivery_counters SET execute_calls = execute_calls + 1 "
                "WHERE singleton = 1"
            )
        self._save_observation(observation)
        return observation

    def group_facts(self) -> dict[str, dict[str, Any]]:
        with _connection(self.store_path) as connection:
            rows = connection.execute(
                "SELECT group_key, group_json FROM root_delivery_groups ORDER BY group_key"
            ).fetchall()
        return {group: load_canonical_json(value) for group, value in rows}


class _LocalEffects:
    def __init__(
        self,
        store_path: Path,
        delivery: object,
        scenario: str,
        root_repository: _RootGitRepository | None = None,
    ):
        self.store_path = Path(store_path)
        self.store_path.parent.mkdir(parents=True, exist_ok=True)
        self.delivery = delivery
        self.scenario = scenario
        self.root_repository = root_repository
        self.root_store = (
            _RootCandidateStore(self.store_path) if scenario == "root" else None
        )
        self.root_gate = (
            _RootCandidateGateRunner(root_repository, self.root_store)
            if scenario == "root"
            and root_repository is not None
            and self.root_store is not None
            else None
        )
        self.gate_events: list[dict[str, Any]] = []
        self._semantic_attempts: dict[str, int] = {}
        self._semantic_actions: dict[str, WorkRunAction] = {}
        with _connection(self.store_path) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS observations(
                    stable_action_id TEXT PRIMARY KEY,
                    observation_json BLOB NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS counters(
                    singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
                    execute_calls INTEGER NOT NULL
                )
                """
            )
            connection.execute(
                """
                INSERT INTO counters(singleton, execute_calls)
                VALUES (1, 0)
                ON CONFLICT(singleton) DO NOTHING
                """
            )

    def bind_batch_delivery_request_digest(self, action: WorkRunAction) -> str:
        if self.scenario == "root":
            if self.root_gate is None or not isinstance(
                self.delivery, _RootBatchDelivery
            ):
                raise LocalAcceptanceFailure("root Batch delivery is not installed")
            candidates, accepted = self._root_candidate_maps(action)
            return self.delivery.bind(action, candidates, accepted)
        return digest_value(
            {
                "kind": "local-batch-request.v1",
                "campaign": action.campaign_key,
                "ticket": action.ticket_key,
                "candidate": action.accepted_candidate_receipt_digest,
            }
        )

    @property
    def execute_calls(self) -> int:
        with _connection(self.store_path) as connection:
            row = connection.execute(
                "SELECT execute_calls FROM counters WHERE singleton = 1"
            ).fetchone()
        assert row is not None
        return int(row[0])

    def _read_semantic(self, action: WorkRunAction) -> WorkRunObservation | None:
        with _connection(self.store_path) as connection:
            row = connection.execute(
                "SELECT observation_json FROM observations WHERE stable_action_id = ?",
                (action.stable_action_id,),
            ).fetchone()
        if row is None:
            return None
        return WorkRunObservation.from_canonical(load_canonical_json(row[0]))

    def readback(self, action: WorkRunAction) -> WorkRunObservation | None:
        if action.kind == "batch_delivery":
            return self.delivery.readback(action)
        if (
            self.scenario == "root"
            and action.wake_ref is not None
            and action.wake_ref.startswith("watchdog:runtime:")
        ):
            existing = self._read_semantic(action)
            if existing is None or existing.phase == "running":
                return self._root_accepted_observation(action)
            return existing
        return self._read_semantic(action)

    def canonical_readbacks(self) -> dict[str, list[dict[str, Any]]]:
        with _connection(self.store_path) as connection:
            rows = connection.execute(
                "SELECT observation_json FROM observations ORDER BY stable_action_id"
            ).fetchall()
        semantic = [
            WorkRunObservation.from_canonical(load_canonical_json(row[0]))
            for row in rows
        ]
        return {
            "semantic": [observation.canonical() for observation in semantic],
            "delivery": [
                observation.canonical() for observation in self.delivery.readbacks()
            ],
        }

    def _save_semantic(self, action: WorkRunAction, observation: WorkRunObservation):
        rendered = canonical_bytes(observation.canonical())
        with _connection(self.store_path) as connection:
            connection.execute(
                "INSERT INTO observations(stable_action_id, observation_json) "
                "VALUES (?, ?) ON CONFLICT(stable_action_id) DO UPDATE SET "
                "observation_json = excluded.observation_json",
                (action.stable_action_id, rendered),
            )

    def _root_candidate_maps(
        self, action: WorkRunAction
    ) -> tuple[dict[str, CandidateReceipt], dict[str, AcceptedCandidateReceipt]]:
        if self.root_gate is None:
            raise LocalAcceptanceFailure("root CandidateGate is not installed")
        candidates: dict[str, CandidateReceipt] = {}
        accepted: dict[str, AcceptedCandidateReceipt] = {}
        for ticket_key, result in self.root_gate.results.items():
            candidate = result.candidate_receipt
            accepted_candidate = result.accepted_candidate_receipt
            if candidate is not None and accepted_candidate is not None:
                candidates[ticket_key] = candidate
                accepted[ticket_key] = accepted_candidate
        if action.ticket_key not in accepted:
            raise LocalAcceptanceFailure(
                f"Batch delivery member {action.ticket_key} lacks an accepted Candidate"
            )
        return candidates, accepted

    def _root_accepted_observation(self, action: WorkRunAction) -> WorkRunObservation:
        if self.root_gate is None:
            raise LocalAcceptanceFailure("root CandidateGate is not installed")
        result = self.root_gate.run(action)
        candidate = result.candidate_receipt
        accepted = result.accepted_candidate_receipt
        if candidate is None or accepted is None:
            raise LocalAcceptanceFailure(
                f"root CandidateGate did not accept {action.ticket_key}"
            )
        transitions = (
            self.root_store.transitions() if self.root_store is not None else []
        )
        ticket_transitions = [
            item for item in transitions if item["ticket_key"] == action.ticket_key
        ]
        status_values = [item["status"] for item in ticket_transitions]
        rejected_transition = next(
            (
                item
                for item in ticket_transitions
                if item["status"] == "ordinary_rejected"
            ),
            None,
        )
        event: dict[str, Any] = {
            "ticket_key": action.ticket_key,
            "assurance": accepted.assurance,
            "review": (
                "repair_required"
                if "repair_required" in status_values
                else "rejected"
                if "ordinary_rejected" in status_values
                else result.status.value
            ),
            "candidate_receipt_digest": candidate.digest,
            "accepted_candidate_receipt_digest": accepted.digest,
        }
        if action.ticket_key == "issue:102":
            event["repair"] = "repair_verify"
        elif action.ticket_key == "issue:103":
            if rejected_transition is None:
                raise LocalAcceptanceFailure(
                    "replacement Candidate was accepted without a persisted rejection"
                )
            event["rejected_candidate_receipt_digest"] = rejected_transition[
                "candidate_receipt"
            ]["receipt_digest"]
            event["repair"] = "replacement_candidate"
        elif action.ticket_key == _ROOT_STRICT_TICKET:
            event["specialist_review"] = "accepted"
        self.gate_events.append(event)
        observation = WorkRunObservation(
            phase="accepted_awaiting_delivery",
            stable_action_id=action.stable_action_id,
            receipt_digest=candidate.digest,
            candidate_identity=f"candidate:{candidate.candidate_commit_oid}",
            candidate_receipt=candidate,
            runtime_binding_id=action.runtime_binding_id,
            accepted_candidate_receipt_digest=accepted.digest,
            candidate_diff_record_digest=candidate.diff_record_digest,
        )
        self._save_semantic(action, observation)
        return observation

    def execute(self, action: WorkRunAction) -> WorkRunObservation:
        existing = self.readback(action)
        if existing is not None:
            return existing
        if self.scenario == "failure":
            raise LocalAcceptanceFailure("deterministic local Runtime failure")
        if action.kind == "batch_delivery":
            if self.scenario == "root" and isinstance(
                self.delivery, _RootBatchDelivery
            ):
                candidates, accepted = self._root_candidate_maps(action)
                return self.delivery.execute(action, candidates, accepted)
            return self.delivery.execute(action)
        with _connection(self.store_path) as connection:
            connection.execute(
                "UPDATE counters SET execute_calls = execute_calls + 1 WHERE singleton = 1"
            )
        if self.scenario == "root":
            self._semantic_actions[action.ticket_key] = action
            attempt = self._semantic_attempts.get(action.stable_action_id, 0) + 1
            self._semantic_attempts[action.stable_action_id] = attempt
            if attempt == 1:
                return WorkRunObservation(
                    phase="running",
                    stable_action_id=action.stable_action_id,
                    receipt_digest=digest_value(
                        {
                            "kind": "local-root-running.v1",
                            "action": action.stable_action_id,
                        }
                    ),
                    reason="LocalRootCandidateCallbackLost",
                    next_check_at="2026-08-11T00:00:00+00:00",
                    runtime_binding_id=f"binding:root:{action.ticket_key}",
                )
            return self._root_accepted_observation(action)
        if self.scenario == "wait":
            observation = WorkRunObservation(
                phase="wait",
                stable_action_id=action.stable_action_id,
                receipt_digest=digest_value(
                    {"kind": "local-wait.v1", "action": action.stable_action_id}
                ),
                reason="LocalRuntimeWait",
                next_check_at="2026-08-11T00:00:00+00:00",
                binding_established=False,
            )
        elif self.scenario == "blocked":
            observation = WorkRunObservation(
                phase="blocked",
                stable_action_id=action.stable_action_id,
                receipt_digest=digest_value(
                    {"kind": "local-blocked.v1", "action": action.stable_action_id}
                ),
                reason="LocalRuntimeBlocked",
                binding_established=False,
            )
        elif action.kind == "semantic_execution":
            candidate = _candidate_for(action)
            accepted = _accepted_for(action, candidate)
            observation = WorkRunObservation(
                phase="accepted_awaiting_delivery",
                stable_action_id=action.stable_action_id,
                receipt_digest=candidate.digest,
                candidate_identity=f"candidate:{candidate.candidate_commit_oid}",
                candidate_receipt=candidate,
                runtime_binding_id="binding:local",
                accepted_candidate_receipt_digest=accepted.digest,
                candidate_diff_record_digest=candidate.diff_record_digest,
            )
        else:  # pragma: no cover - the Kernel owns the closed action union.
            raise LocalAcceptanceFailure(f"unsupported local action: {action.kind}")
        self._save_semantic(action, observation)
        return observation


@dataclass
class _Harness:
    root: Path
    scenario: str
    run_id: str
    handle: CampaignHandle
    root_repository: _RootGitRepository | None
    effects: _LocalEffects
    delivery: object
    control: PlanControl
    configuration: ExecutionKernelConfiguration
    kernel: object | None = None


@dataclass(frozen=True)
class _LocalStartHost:
    control: PlanControl
    campaign_key: str

    def start(
        self,
        repository: str,
        ready_refs: tuple[str, ...],
        options: object = None,
    ) -> CampaignHandle:
        return self.control.start(
            repository,
            ready_refs,
            options,
            campaign_key=self.campaign_key,
        )


@dataclass
class _RootWatchdogEventSource:
    handle: CampaignHandle
    emit_callback: bool = True
    emitted_wakes: list[WatchdogWake] | None = None

    def read(self, after_cursor: str | None) -> WatchdogWakePage:
        if self.emit_callback and after_cursor is None:
            wake = WatchdogWake(
                "1",
                self.handle,
                "runtime",
                "local-root-candidate-callback",
            )
            if self.emitted_wakes is None:
                self.emitted_wakes = []
            self.emitted_wakes.append(wake)
            return WatchdogWakePage(events=(wake,), next_cursor="1")
        return WatchdogWakePage(events=(), next_cursor=after_cursor)


@dataclass(frozen=True)
class _PublicAdvance:
    wake_refs: list[str | None] | None = None

    def advance(
        self,
        handle: CampaignHandle,
        wake_ref: str | None = None,
    ):
        if self.wake_refs is not None:
            self.wake_refs.append(wake_ref)
        return gwo_v8.advance(handle, wake_ref)


def _canonical_readback(effects: _LocalEffects) -> dict[str, Any]:
    observations = effects.canonical_readbacks()
    all_observations = [
        WorkRunObservation.from_canonical(value)
        for group in observations.values()
        for value in group
    ]
    candidate_observation = next(
        (
            observation
            for observation in all_observations
            if observation.candidate_receipt is not None
        ),
        None,
    )
    candidate = (
        candidate_observation.candidate_receipt
        if candidate_observation is not None
        else None
    )
    if candidate is None or candidate_observation is None:
        return {
            "observations": observations,
            "candidate_receipt": None,
            "candidate_diff": None,
            "accepted_candidate_receipt": None,
            "delivery_proof": None,
            "result_integrity": None,
        }

    action = WorkRunAction(
        stable_action_id=candidate_observation.stable_action_id,
        repository=candidate.repository,
        campaign_key=candidate.campaign_key,
        plan_revision_digest=candidate.plan_revision_digest,
        ticket_key=candidate.ticket_key,
        kind="semantic_execution",
        semantic_action_id=candidate_observation.stable_action_id,
        work_run_key=candidate.work_run_key,
        work_subject_digest=candidate.runtime_subject_digest,
        runtime_binding_id=candidate_observation.runtime_binding_id,
    )
    diff = _candidate_diff_for()
    accepted = _accepted_for(action, candidate)
    result_observation = next(
        (
            observation
            for observation in all_observations
            if observation.result_integrity is not None
        ),
        None,
    )
    proof = (
        result_observation.result_integrity if result_observation is not None else None
    )
    delivery = None
    if proof is not None:
        delivery = BatchDeliveryProof.create(
            delivery_stable_action_id=proof.delivery_stable_action_id,
            delivery_request_digest=proof.delivery_request_digest,
            batch_id=proof.batch_id,
            batch_sha=proof.batch_sha,
            member_ticket_keys=proof.delivery_member_ticket_keys,
            local_check_receipt_digest=proof.local_check_receipt_digest,
            publication_receipt_digest=proof.publication_receipt_digest,
            pull_request_number=proof.pull_request_number,
            pull_request_head_sha=proof.pull_request_head_sha,
            hosted_result_receipt_digest=proof.hosted_result_receipt_digest,
            integration_lease_digest=proof.integration_lease_digest,
            target_branch=proof.target_branch,
            target_head_sha=proof.target_head_sha,
            target_readback_digest=proof.target_readback_digest,
            target_contains_batch_sha=proof.target_contains_batch_sha,
            pull_request_merge_target_sha=proof.pull_request_merge_target_sha,
            merge_method=proof.merge_method,
        )
    return {
        "observations": observations,
        "candidate_receipt": candidate.canonical(),
        "candidate_diff": diff.canonical(),
        "accepted_candidate_receipt": accepted.canonical(),
        "delivery_proof": None if delivery is None else delivery.canonical(),
        "result_integrity": None if proof is None else proof.canonical(),
    }


def _delivery_from_result_proof(proof: ResultIntegrityProof) -> BatchDeliveryProof:
    return BatchDeliveryProof.create(
        delivery_stable_action_id=proof.delivery_stable_action_id,
        delivery_request_digest=proof.delivery_request_digest,
        batch_id=proof.batch_id,
        batch_sha=proof.batch_sha,
        member_ticket_keys=proof.delivery_member_ticket_keys,
        local_check_receipt_digest=proof.local_check_receipt_digest,
        publication_receipt_digest=proof.publication_receipt_digest,
        pull_request_number=proof.pull_request_number,
        pull_request_head_sha=proof.pull_request_head_sha,
        hosted_result_receipt_digest=proof.hosted_result_receipt_digest,
        integration_lease_digest=proof.integration_lease_digest,
        target_branch=proof.target_branch,
        target_head_sha=proof.target_head_sha,
        target_readback_digest=proof.target_readback_digest,
        target_contains_batch_sha=proof.target_contains_batch_sha,
        pull_request_merge_target_sha=proof.pull_request_merge_target_sha,
        merge_method=proof.merge_method,
    )


def _canonical_root_readback(effects: _LocalEffects) -> dict[str, Any]:
    if effects.root_repository is None or effects.root_store is None:
        raise LocalAcceptanceFailure("root readback requires the real root repository")
    if not isinstance(effects.delivery, _RootBatchDelivery):
        raise LocalAcceptanceFailure("root readback requires BatchIntegrator delivery")

    observations = effects.canonical_readbacks()
    semantic_observations = [
        WorkRunObservation.from_canonical(value) for value in observations["semantic"]
    ]
    delivery_observations = [
        WorkRunObservation.from_canonical(value) for value in observations["delivery"]
    ]
    candidates_by_ticket = {
        observation.candidate_receipt.ticket_key: observation.candidate_receipt
        for observation in semantic_observations
        if observation.candidate_receipt is not None
    }
    transitions = effects.root_store.transitions()
    accepted_by_ticket: dict[str, dict[str, Any]] = {}
    diff_by_digest: dict[str, dict[str, Any]] = {}
    for ticket_key in _ROOT_TICKET_KEYS:
        ticket_transitions = [
            item for item in transitions if item["ticket_key"] == ticket_key
        ]
        accepted_transition = next(
            (
                item
                for item in reversed(ticket_transitions)
                if item["status"] in {"review_accepted", "repair_accepted"}
            ),
            None,
        )
        if accepted_transition is None:
            raise LocalAcceptanceFailure(
                f"no persisted accepted Candidate transition for {ticket_key}"
            )
        accepted_candidate = accepted_transition["accepted_candidate_receipt"]
        candidate = accepted_transition["candidate_receipt"]
        if candidate is None or accepted_candidate is None:
            raise LocalAcceptanceFailure(
                f"accepted Candidate transition for {ticket_key} is incomplete"
            )
        if ticket_key not in candidates_by_ticket:
            raise LocalAcceptanceFailure(
                f"semantic readback omitted accepted Candidate {ticket_key}"
            )
        if candidates_by_ticket[ticket_key].digest != candidate["receipt_digest"]:
            raise LocalAcceptanceFailure(
                f"semantic Candidate readback differs from persisted gate {ticket_key}"
            )
        accepted_by_ticket[ticket_key] = accepted_candidate
        diff_digest = candidate["diff_record_digest"]
        diff = effects.root_store.read(diff_digest)
        if diff is None:
            raise LocalAcceptanceFailure(
                f"persisted Candidate diff {diff_digest} is not readable"
            )
        diff_by_digest[diff_digest] = diff.canonical()

    result_observations = sorted(
        (
            observation
            for observation in delivery_observations
            if observation.result_integrity is not None
        ),
        key=lambda observation: (
            observation.result_integrity.accepted_candidate_receipt_digest
        ),
    )
    if len(result_observations) != len(_ROOT_TICKET_KEYS):
        raise LocalAcceptanceFailure("root delivery readback omitted a Work Run")
    proofs = [
        _delivery_from_result_proof(observation.result_integrity)
        for observation in result_observations
    ]
    proof_by_ticket = {
        observation.candidate_receipt.ticket_key: proof
        for observation, proof in zip(result_observations, proofs)
        if observation.candidate_receipt is not None
    }
    group_records = effects.delivery.group_facts()
    ordered_batches: list[dict[str, Any]] = []
    for group, expected_members in (
        ("standard", _ROOT_STANDARD_TICKETS),
        ("strict", (_ROOT_STRICT_TICKET,)),
    ):
        member_proofs = [proof_by_ticket[ticket_key] for ticket_key in expected_members]
        identities = {
            (
                proof.batch_id,
                proof.batch_sha,
                proof.target_branch,
                proof.target_head_sha,
                proof.member_ticket_keys,
            )
            for proof in member_proofs
        }
        if (
            len(identities) != 1
            or member_proofs[0].member_ticket_keys != expected_members
        ):
            raise LocalAcceptanceFailure(
                f"{group} delivery did not preserve one exact member partition"
            )
        group_record = group_records.get(group)
        if group_record is None:
            raise LocalAcceptanceFailure(f"{group} Batch operation was not persisted")
        operation = group_record["operation"]
        if (
            tuple(item["ticket_key"] for item in operation["members"])
            != expected_members
            or len(operation["delivery_proofs"]) != 1
            or tuple(operation["delivery_proofs"][0]["member_ticket_keys"])
            != expected_members
        ):
            raise LocalAcceptanceFailure(
                f"BatchIntegrator selected the wrong {group} members"
            )
        batch_id, batch_sha, target_branch, target_head_sha, _members = next(
            iter(identities)
        )
        batch_ref = f"refs/gwo-v8/integration-batches/{batch_id}"
        batch_ref_sha = _git(
            effects.root_repository.path, "show-ref", "--hash", "--verify", batch_ref
        )
        target_tree_oid = _git(
            effects.root_repository.path,
            "rev-parse",
            f"{target_head_sha}^{{tree}}",
        )
        target_contains = _git_is_ancestor(
            effects.root_repository.path,
            batch_sha,
            target_head_sha,
        )
        candidate_commit_oids = [
            accepted_by_ticket[ticket_key]["candidate_sha"]
            for ticket_key in expected_members
        ]
        candidate_ancestry = {
            ticket_key: _git_is_ancestor(
                effects.root_repository.path,
                accepted_by_ticket[ticket_key]["candidate_sha"],
                batch_sha,
            )
            for ticket_key in expected_members
        }
        if (
            batch_ref_sha != batch_sha
            or not target_contains
            or not all(candidate_ancestry.values())
            or any(not proof.target_contains_batch_sha for proof in member_proofs)
        ):
            raise LocalAcceptanceFailure(
                f"independent Git readback rejected the {group} Batch evidence"
            )
        ordered_batches.append(
            {
                "group": group,
                "batch_id": batch_id,
                "batch_sha": batch_sha,
                "batch_ref": batch_ref,
                "batch_ref_sha": batch_ref_sha,
                "member_ticket_keys": list(expected_members),
                "candidate_commit_oids": candidate_commit_oids,
                "candidate_ancestry": candidate_ancestry,
                "target_branch": target_branch,
                "target_head_sha": target_head_sha,
                "target_tree_oid": target_tree_oid,
                "target_contains_batch_sha": target_contains,
                "integration_action_id": group_record["integration_action_id"],
                "formation_calls": group_record["formation_calls"],
                "compose_calls": group_record["compose_calls"],
                "integration_calls": group_record["integration_calls"],
            }
        )

    target_ref = f"refs/heads/{_TARGET_BRANCH}"
    target_head_sha = _git(
        effects.root_repository.path,
        "show-ref",
        "--hash",
        "--verify",
        target_ref,
    )
    target_tree_oid = _git(
        effects.root_repository.path,
        "rev-parse",
        f"{target_head_sha}^{{tree}}",
    )
    candidate_objects = []
    final_variants = {
        "issue:101": "accepted",
        "issue:102": "repaired",
        "issue:103": "replacement",
        "issue:104": "accepted",
    }
    for ticket_key in _ROOT_TICKET_KEYS:
        reference = effects.root_repository.candidate_refs[
            (ticket_key, final_variants[ticket_key])
        ]
        commit_sha = _git(
            effects.root_repository.path,
            "rev-parse",
            f"{reference}^{{commit}}",
        )
        tree_sha = _git(
            effects.root_repository.path,
            "rev-parse",
            f"{reference}^{{tree}}",
        )
        candidate = candidates_by_ticket[ticket_key]
        if (
            commit_sha != candidate.candidate_commit_oid
            or tree_sha != candidate.candidate_tree_oid
            or commit_sha != accepted_by_ticket[ticket_key]["candidate_sha"]
        ):
            raise LocalAcceptanceFailure(
                f"Candidate Git ref readback changed {ticket_key} identity"
            )
        candidate_objects.append(
            {
                "ticket_key": ticket_key,
                "reference": reference,
                "commit_sha": commit_sha,
                "tree_sha": tree_sha,
                "diff_record_digest": candidate.diff_record_digest,
            }
        )
    return {
        "observations": observations,
        "candidate_receipts": [
            candidates_by_ticket[ticket_key].canonical()
            for ticket_key in _ROOT_TICKET_KEYS
        ],
        "candidate_diffs": [
            diff_by_digest[candidates_by_ticket[ticket_key].diff_record_digest]
            for ticket_key in _ROOT_TICKET_KEYS
        ],
        "accepted_candidate_receipts": [
            accepted_by_ticket[ticket_key] for ticket_key in _ROOT_TICKET_KEYS
        ],
        "delivery_proofs": [proof.canonical() for proof in proofs],
        "result_integrities": [
            observation.result_integrity.canonical()
            for observation in result_observations
        ],
        "git_readback": {
            "target_branch": _TARGET_BRANCH,
            "target_ref": target_ref,
            "target_head_sha": target_head_sha,
            "target_tree_oid": target_tree_oid,
            "candidate_objects": candidate_objects,
            "batches": ordered_batches,
            "target_contains_batches": {
                batch["group"]: _git_is_ancestor(
                    effects.root_repository.path,
                    batch["batch_sha"],
                    target_head_sha,
                )
                for batch in ordered_batches
            },
        },
    }


def _diagnostics_record(
    diagnostics: object,
    *,
    root: bool = False,
) -> dict[str, Any]:
    value = {
        "status": diagnostics.status.value,
        "reason": diagnostics.reason,
        "plan_revision_digest": diagnostics.plan_revision_digest,
        "worker_slots": dict(diagnostics.worker_slots),
        "work_runs": [
            {
                "ticket_key": run.ticket_key,
                "work_run_key": run.work_run_key,
                "phase": run.phase,
                "claim_state": run.claim_state,
                "slot_held": run.slot_held,
                "candidate_identity": run.candidate_identity,
                "accepted_candidate_receipt_digest": run.accepted_candidate_receipt_digest,
                "candidate_diff_record_digest": run.candidate_diff_record_digest,
                "delivery_receipt_digest": run.delivery_receipt_digest,
                "result_digest": run.result_digest,
                "evidence_digests": list(run.evidence_digests),
                "reason": run.reason,
                "next_check_at": run.next_check_at,
                **(
                    {"exclusive_resources": list(run.exclusive_resources)}
                    if root
                    else {}
                ),
            }
            for run in diagnostics.work_runs
        ],
        "outstanding_effect_ids": list(diagnostics.outstanding_effect_ids),
    }
    return value


def _install_harness(
    root: Path, run_id: str, scenario: str
) -> tuple[_Harness, CampaignHandle]:
    root = Path(root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    root_repository = (
        _initialize_root_git_repository(root) if scenario == "root" else None
    )
    if root_repository is None:
        repository_root = root / "repository"
        repository_root.mkdir(parents=True, exist_ok=True)
        (repository_root / "README.md").write_text(
            "deterministic V8 local acceptance\n", encoding="utf-8"
        )
    sqlite_root = root / "sqlite"
    sqlite_root.mkdir(parents=True, exist_ok=True)
    artifacts = ArtifactStore(root / "artifacts")
    ticket_keys = _ROOT_TICKET_KEYS if scenario == "root" else (_TICKET_KEY,)
    planning_gateway = _LocalPlanningGateway(
        artifacts,
        [],
        ticket_keys=ticket_keys,
        exclusive_resources=(
            {ticket_key: () for ticket_key in _ROOT_STANDARD_TICKETS}
            | {_ROOT_STRICT_TICKET: ("repository.target.v1",)}
            if scenario == "root"
            else None
        ),
    )
    control = PlanControl(
        source=_LocalSnapshotSource(ticket_keys, root_repository),
        artifacts=artifacts,
        gateway=planning_gateway,
        repository=InMemoryPlanRepository(writer_generation="writer:local"),
    )
    _install_start_host(_LocalStartHost(control, _campaign_key(run_id, scenario)))
    delivery = (
        _RootBatchDelivery(sqlite_root / "delivery.sqlite3", root_repository)
        if scenario == "root" and root_repository is not None
        else _LocalDeliveryStub(sqlite_root / "delivery.sqlite3", scenario)
    )
    effects = _LocalEffects(
        sqlite_root / "effects.sqlite3",
        delivery,
        scenario,
        root_repository,
    )
    configuration = ExecutionKernelConfiguration(
        host_worker_slots=4 if scenario == "root" else 1,
        repository_worker_slots={_REPOSITORY: 4 if scenario == "root" else 1},
        host_stale_after_seconds=1800,
        repository_stale_after_seconds={_REPOSITORY: 1800},
    )
    kernel = install_execution_kernel(
        store_path=sqlite_root / "execution.sqlite3",
        plan_control=control,
        effects=effects,
        configuration=configuration,
    )
    handle = gwo_v8.start(_REPOSITORY, ticket_keys)
    return (
        _Harness(
            root=root,
            scenario=scenario,
            run_id=run_id,
            handle=handle,
            root_repository=root_repository,
            effects=effects,
            delivery=delivery,
            control=control,
            configuration=configuration,
            kernel=kernel,
        ),
        handle,
    )


def _install_restart(harness: _Harness) -> None:
    delivery = (
        _RootBatchDelivery(
            harness.root / "sqlite" / "delivery.sqlite3",
            harness.root_repository,
        )
        if harness.scenario == "root" and harness.root_repository is not None
        else _LocalDeliveryStub(
            harness.root / "sqlite" / "delivery.sqlite3", harness.scenario
        )
    )
    effects = _LocalEffects(
        harness.root / "sqlite" / "effects.sqlite3",
        delivery,
        harness.scenario,
        harness.root_repository,
    )
    harness.effects = effects
    harness.delivery = effects.delivery
    harness.kernel = install_execution_kernel(
        store_path=harness.root / "sqlite" / "execution.sqlite3",
        plan_control=harness.control,
        effects=effects,
        configuration=harness.configuration,
    )


def _outcome_record(outcome: object) -> dict[str, str]:
    return {
        "status": outcome.status.value,
        "reason": outcome.reason,
    }


def _record_digest(record: dict[str, Any]) -> dict[str, Any]:
    return {**record, "record_digest": digest_value(record)}


def _root_batch_facts(
    readback: dict[str, Any],
    work_runs: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    proof_by_ticket = {
        run["ticket_key"]: next(
            proof
            for proof in readback["result_integrities"]
            if proof["accepted_candidate_receipt_digest"]
            == run["accepted_candidate_receipt_digest"]
        )
        for run in work_runs
    }
    batch_by_group = {
        item["group"]: item for item in readback["git_readback"]["batches"]
    }
    batches: list[dict[str, Any]] = []
    for group, member_ticket_keys, singleton in (
        ("standard", _ROOT_STANDARD_TICKETS, False),
        ("strict", (_ROOT_STRICT_TICKET,), True),
    ):
        member_proofs = [
            proof_by_ticket[ticket_key] for ticket_key in member_ticket_keys
        ]
        identities = {
            (
                proof["batch_id"],
                proof["batch_sha"],
                tuple(proof["delivery_member_ticket_keys"]),
                proof["target_branch"],
                proof["target_head_sha"],
            )
            for proof in member_proofs
        }
        if len(identities) != 1 or next(iter(identities))[2] != member_ticket_keys:
            raise LocalAcceptanceFailure(
                f"{group} result proofs disagree about their shared Batch"
            )
        batch = batch_by_group[group]
        if (
            batch["member_ticket_keys"] != list(member_ticket_keys)
            or batch["batch_id"] != member_proofs[0]["batch_id"]
            or batch["batch_sha"] != member_proofs[0]["batch_sha"]
            or batch["target_head_sha"] != member_proofs[0]["target_head_sha"]
        ):
            raise LocalAcceptanceFailure(
                f"{group} independent Batch readback disagrees with result proofs"
            )
        members = [
            next(run for run in work_runs if run["ticket_key"] == ticket_key)
            for ticket_key in member_ticket_keys
        ]
        batches.append(
            {
                "group": group,
                "batch_id": member_proofs[0]["batch_id"],
                "batch_sha": member_proofs[0]["batch_sha"],
                "member_ticket_keys": list(member_ticket_keys),
                "singleton": singleton,
                "candidate_receipt_digests": [
                    member["candidate_receipt_digest"] for member in members
                ],
                "delivery_receipt_digests": [
                    member["delivery_receipt_digest"] for member in members
                ],
                "result_digests": [member["result_digest"] for member in members],
                "evidence_digests": sorted(
                    {
                        digest
                        for member in members
                        for digest in member["evidence_digests"]
                    }
                ),
                "target_branch": member_proofs[0]["target_branch"],
                "target_head_sha": member_proofs[0]["target_head_sha"],
                "target_contains_batch_sha": batch["target_contains_batch_sha"],
                "batch_ref": batch["batch_ref"],
                "batch_ref_sha": batch["batch_ref_sha"],
                "candidate_ancestry": batch["candidate_ancestry"],
                "integration_action_id": batch["integration_action_id"],
                "formation_calls": batch["formation_calls"],
                "compose_calls": batch["compose_calls"],
                "integration_calls": batch["integration_calls"],
            }
        )
    return batches


def _run_root_acceptance_in_root(
    *,
    root: Path,
    run_id: str,
) -> dict[str, Any]:
    harness, handle = _install_harness(Path(root), run_id, "root")
    initial = gwo_v8.inspect(handle)
    transcript: list[dict[str, Any]] = [
        {"operation": "start", "campaign_key": handle.campaign_key},
        {
            "operation": "inspect",
            "readback": _diagnostics_record(initial, root=True),
        },
    ]

    def advance_with_readback(wake_ref: str) -> dict[str, str]:
        outcome = gwo_v8.advance(handle, wake_ref)
        result = _outcome_record(outcome)
        transcript.append(
            {
                "operation": "advance",
                "wake_ref": wake_ref,
                "outcome": result,
            }
        )
        return result

    def inspect_with_readback() -> object:
        diagnostics = gwo_v8.inspect(handle)
        transcript.append(
            {
                "operation": "inspect",
                "readback": _diagnostics_record(diagnostics, root=True),
            }
        )
        return diagnostics

    initial_outcome = advance_with_readback("root:initial")
    initial_after_advance = inspect_with_readback()

    public_advancer_wake_refs: list[str | None] = []
    public_advancer = _PublicAdvance(public_advancer_wake_refs)
    watchdog_source = _RootWatchdogEventSource(handle)
    watchdog = CampaignWatchdog(
        store_path=Path(root) / "sqlite" / "watchdog.sqlite3",
        event_sources={
            "runtime": watchdog_source,
        },
        campaign_source=harness.kernel,
        advancer=public_advancer,
    )
    lost_wake_outcomes = watchdog.run_once("2026-08-11T00:00:01+00:00")
    lost_wake = _outcome_record(lost_wake_outcomes[-1])
    transcript.append(
        {
            "operation": "watchdog",
            "kind": "lost_wake",
            "outcomes": [_outcome_record(item) for item in lost_wake_outcomes],
        }
    )
    after_watchdog = inspect_with_readback()

    if not watchdog_source.emitted_wakes:
        raise LocalAcceptanceFailure("watchdog did not emit a runtime callback")
    emitted_callback_ref = watchdog_source.emitted_wakes[0].wake_ref
    duplicate_callback_ref = emitted_callback_ref
    duplicate_callback = advance_with_readback(duplicate_callback_ref)
    inspect_with_readback()

    gate_events = list(harness.effects.gate_events)
    candidate_transitions = (
        harness.effects.root_store.transitions()
        if harness.effects.root_store is not None
        else []
    )
    formal_reviewer_calls = (
        list(harness.effects.root_gate.reviewer.calls)
        if harness.effects.root_gate is not None
        else []
    )
    before_restart_readback = _canonical_root_readback(harness.effects)
    replay: dict[str, Any] = {
        "initial_advance": initial_outcome,
        "initial_work_runs": len(initial_after_advance.work_runs),
        "initial_worker_slots": dict(initial_after_advance.worker_slots),
        "watchdog_progressed": bool(lost_wake_outcomes),
        "callback_emitted": emitted_callback_ref,
        "public_advancer_wake_refs": list(public_advancer_wake_refs),
        "lost_wake": lost_wake,
        "duplicate_callback_ref": duplicate_callback_ref,
        "duplicate_callback": duplicate_callback,
        "watchdog_status_after_progress": after_watchdog.status.value,
        "semantic_execute_calls_before_restart": harness.effects.execute_calls,
        "delivery_execute_calls_before_restart": harness.delivery.execute_calls,
        "readback_digest_before_restart": digest_value(before_restart_readback),
    }

    _install_restart(harness)
    restarted = inspect_with_readback()
    replay["restart_inspect"] = _diagnostics_record(restarted, root=True)
    replay["restart_advance"] = advance_with_readback("root:restart")
    restarted_after_advance = inspect_with_readback()
    replay["restart_advance_inspect"] = _diagnostics_record(
        restarted_after_advance,
        root=True,
    )
    replay["repeated_restart_advance"] = advance_with_readback("root:restart")
    final = inspect_with_readback()
    replay["final_inspect"] = _diagnostics_record(final, root=True)
    after_replay_readback = _canonical_root_readback(harness.effects)
    replay["semantic_execute_calls_after_replay"] = harness.effects.execute_calls
    replay["delivery_execute_calls_after_replay"] = harness.delivery.execute_calls
    replay["readback_digest_after_replay"] = digest_value(after_replay_readback)
    replay["readback_unchanged"] = before_restart_readback == after_replay_readback
    replay["idempotent_delivery"] = (
        replay["delivery_execute_calls_before_restart"]
        == replay["delivery_execute_calls_after_replay"]
    )
    replay["idempotent_effects"] = (
        replay["semantic_execute_calls_before_restart"]
        == replay["semantic_execute_calls_after_replay"]
        and replay["idempotent_delivery"]
    )

    readback = after_replay_readback
    proof_by_accepted = {
        proof["accepted_candidate_receipt_digest"]: proof
        for proof in readback["result_integrities"]
    }
    batch_by_id = {
        batch["batch_id"]: batch for batch in readback["git_readback"]["batches"]
    }
    work_runs: list[dict[str, Any]] = []
    for run in final.work_runs:
        proof = proof_by_accepted[run.accepted_candidate_receipt_digest]
        batch_facts = batch_by_id[proof["batch_id"]]
        work_runs.append(
            {
                "ticket_key": run.ticket_key,
                "work_run_key": run.work_run_key,
                "phase": run.phase,
                "claim_state": run.claim_state,
                "slot_held": run.slot_held,
                "exclusive_resources": list(run.exclusive_resources),
                "candidate_identity": run.candidate_identity,
                "candidate_receipt_digest": next(
                    receipt["receipt_digest"]
                    for receipt in readback["candidate_receipts"]
                    if receipt["ticket_key"] == run.ticket_key
                ),
                "accepted_candidate_receipt_digest": run.accepted_candidate_receipt_digest,
                "candidate_diff_record_digest": run.candidate_diff_record_digest,
                "delivery_receipt_digest": run.delivery_receipt_digest,
                "batch_id": proof["batch_id"],
                "batch_sha": proof["batch_sha"],
                "result_digest": run.result_digest,
                "evidence_digests": list(run.evidence_digests),
                "git_readback": {
                    "target_branch": proof["target_branch"],
                    "target_head_sha": proof["target_head_sha"],
                    "batch_id": proof["batch_id"],
                    "batch_sha": proof["batch_sha"],
                    "target_contains_batch_sha": proof["target_contains_batch_sha"],
                    "batch_ref": batch_facts["batch_ref"],
                    "batch_ref_sha": batch_facts["batch_ref_sha"],
                    "candidate_ancestry": batch_facts["candidate_ancestry"],
                },
            }
        )

    facts = {
        "tickets": [
            {
                "ticket_key": ticket_key,
                "assurance": _root_assurance(ticket_key),
            }
            for ticket_key in _ROOT_TICKET_KEYS
        ],
        "campaign": {"campaign_key": handle.campaign_key},
        "plan_revision": {"digest": final.plan_revision_digest},
        "concurrency": {
            "worker_slot_limit": initial_after_advance.worker_slots["limit"],
            "max_held": max(
                snapshot["readback"]["worker_slots"]["held"]
                for snapshot in transcript
                if snapshot["operation"] == "inspect"
            ),
            "final_held": final.worker_slots["held"],
            "final_available": final.worker_slots["available"],
            "work_run_count": len(work_runs),
        },
        "exclusive_resources": {
            run["ticket_key"]: run["exclusive_resources"] for run in work_runs
        },
        "work_runs": work_runs,
        "candidate_gate": {
            "reviewed": list(_ROOT_TICKET_KEYS),
            "repair_required": [
                event["ticket_key"]
                for event in gate_events
                if event["review"] == "repair_required"
            ],
            "rejected": [
                event["ticket_key"]
                for event in gate_events
                if event["review"] == "rejected"
            ],
            "strict_specialist_review": [
                event["ticket_key"]
                for event in gate_events
                if event.get("specialist_review") == "accepted"
            ],
            "accepted": list(_ROOT_TICKET_KEYS),
            "events": gate_events,
            "transitions": candidate_transitions,
            "formal_reviewer_calls": formal_reviewer_calls,
        },
        "batches": _root_batch_facts(readback, work_runs),
        "git_readback": readback["git_readback"],
        "readback": readback,
    }
    record = {
        "schema_version": "gwo.v8.local-root-canary.v1",
        "gate": LOCAL_ROOT_CANARY_GO,
        "scenario": "root",
        "run_id": run_id,
        "status": final.status.value,
        "public_status": final.status.value,
        "reason": final.reason,
        "public_reason": final.reason,
        "campaign": {
            "repository": handle.repository,
            "campaign_key": handle.campaign_key,
        },
        "facts": facts,
        "transcript": transcript,
        "replay": replay,
        "failure": None,
    }
    return _record_digest(record)


def _run_local_acceptance_in_root(
    *,
    root: Path,
    run_id: str = "phase1-single",
    scenario: str = "single",
) -> dict[str, Any]:
    """Run one local acceptance scenario and return its JSON record."""

    if type(run_id) is not str or not run_id:
        raise ValueError("run_id must be non-empty text")
    if scenario == "root":
        return _run_root_acceptance_in_root(root=Path(root), run_id=run_id)
    if scenario not in {"single", "wait", "blocked", "failure"}:
        raise ValueError("scenario must be single, wait, blocked, or failure")

    harness, handle = _install_harness(Path(root), run_id, scenario)
    initial = gwo_v8.inspect(handle)
    transcript: list[dict[str, Any]] = [
        {"operation": "start", "campaign_key": handle.campaign_key},
        {"operation": "inspect", "readback": _diagnostics_record(initial)},
    ]

    def advance_with_readback(
        wake_ref: str,
    ) -> tuple[dict[str, str], dict[str, str] | None]:
        try:
            outcome = gwo_v8.advance(handle, wake_ref)
        except LocalAcceptanceFailure as error:
            failure_record = {"type": type(error).__name__, "message": str(error)}
            return {"status": "Failure", **failure_record}, failure_record
        return _outcome_record(outcome), None

    def inspect_with_readback() -> object:
        diagnostics = gwo_v8.inspect(handle)
        transcript.append(
            {"operation": "inspect", "readback": _diagnostics_record(diagnostics)}
        )
        return diagnostics

    first_outcome, failure = advance_with_readback("local:initial")
    transcript.append(
        {
            "operation": "advance",
            "wake_ref": "local:initial",
            "outcome": first_outcome,
        }
    )
    inspect_with_readback()

    same_wake_ref = (
        "local:initial" if failure is None else "local:replay-before-restart"
    )
    same_wake, _same_wake_failure = advance_with_readback(same_wake_ref)
    transcript.append(
        {
            "operation": "advance",
            "wake_ref": same_wake_ref,
            "outcome": same_wake,
        }
    )
    same_wake_inspect = inspect_with_readback()
    before_restart_readback = _canonical_readback(harness.effects)
    replay: dict[str, Any] = {
        "same_wake": same_wake,
        "same_wake_ref": same_wake_ref,
        "same_wake_inspect": _diagnostics_record(same_wake_inspect),
        "semantic_execute_calls_before_restart": harness.effects.execute_calls,
        "delivery_execute_calls_before_restart": harness.delivery.execute_calls,
        "readback_digest_before_restart": digest_value(before_restart_readback),
    }

    _install_restart(harness)
    restarted = inspect_with_readback()
    replay["restart_inspect"] = _diagnostics_record(restarted)

    restart_outcome, _restart_failure = advance_with_readback("local:restart")
    replay["restart_advance"] = restart_outcome
    restarted_after_advance = inspect_with_readback()
    replay["restart_advance_inspect"] = _diagnostics_record(restarted_after_advance)

    repeated_restart_ref = (
        "local:restart" if failure is None else "local:replay-after-restart"
    )
    repeated_restart, _repeated_restart_failure = advance_with_readback(
        repeated_restart_ref
    )
    replay["repeated_restart_advance_ref"] = repeated_restart_ref
    replay["repeated_restart_advance"] = repeated_restart
    final = inspect_with_readback()
    replay["final_inspect"] = _diagnostics_record(final)
    after_replay_readback = _canonical_readback(harness.effects)
    replay["semantic_execute_calls_after_replay"] = harness.effects.execute_calls
    replay["delivery_execute_calls_after_replay"] = harness.delivery.execute_calls
    replay["readback_digest_after_replay"] = digest_value(after_replay_readback)
    replay["readback_unchanged"] = before_restart_readback == after_replay_readback
    replay["idempotent_delivery"] = (
        replay["delivery_execute_calls_before_restart"]
        == replay["delivery_execute_calls_after_replay"]
    )
    replay["idempotent_effects"] = (
        replay["semantic_execute_calls_before_restart"]
        == replay["semantic_execute_calls_after_replay"]
        and replay["idempotent_delivery"]
    )

    run = final.work_runs[0] if final.work_runs else None
    status = "Failure" if failure is not None else final.status.value
    record = {
        "schema_version": "gwo.v8.local-acceptance.v1",
        "gate": PUBLIC_API_SINGLE_NODE_GO,
        "scenario": scenario,
        "run_id": run_id,
        "status": status,
        "public_status": final.status.value,
        "reason": failure["message"] if failure is not None else final.reason,
        "public_reason": final.reason,
        "campaign": {
            "repository": handle.repository,
            "campaign_key": handle.campaign_key,
        },
        "facts": {
            "ticket": {"key": _TICKET_KEY, "ready_ref": _TICKET_KEY},
            "campaign": {"campaign_key": handle.campaign_key},
            "plan_revision": {"digest": final.plan_revision_digest},
            "worker": (
                {
                    "work_run_key": run.work_run_key,
                    "phase": run.phase,
                }
                if run is not None
                else None
            ),
            "candidate_gate": (
                {
                    "candidate_identity": run.candidate_identity,
                    "accepted_candidate_receipt_digest": run.accepted_candidate_receipt_digest,
                    "candidate_diff_record_digest": run.candidate_diff_record_digest,
                }
                if run is not None
                else None
            ),
            "review": (
                {
                    "status": "accepted",
                    "evidence_digests": list(run.evidence_digests),
                }
                if run is not None and run.evidence_digests
                else None
            ),
            "batch": (
                {"delivery_receipt_digest": run.delivery_receipt_digest}
                if run is not None and run.delivery_receipt_digest
                else None
            ),
            "result": (
                {"result_digest": run.result_digest}
                if run is not None and run.result_digest
                else None
            ),
            "evidence": (
                {"digests": list(run.evidence_digests)}
                if run is not None and run.evidence_digests
                else None
            ),
            "readback": after_replay_readback,
        },
        "transcript": transcript,
        "replay": replay,
        "failure": failure,
    }
    return _record_digest(record)


def run_local_acceptance(
    *,
    root: Path,
    run_id: str = "phase1-single",
    scenario: str = "single",
) -> dict[str, Any]:
    """Run one isolated local acceptance scenario and return its JSON record."""

    caller_root = Path(root).resolve()
    caller_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix="gwo-v8-local-run-",
        dir=str(caller_root),
    ) as isolated_root:
        try:
            return _run_local_acceptance_in_root(
                root=Path(isolated_root),
                run_id=run_id,
                scenario=scenario,
            )
        finally:
            gc.collect()


def canonical_json(record: dict[str, Any]) -> str:
    """Render one acceptance record as canonical JSON plus no extra spacing."""

    return canonical_bytes(record).decode("utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, help="temporary acceptance root")
    parser.add_argument("--run-id", default="phase1-single")
    parser.add_argument(
        "--scenario",
        choices=("single", "wait", "blocked", "failure", "root"),
        default="single",
    )
    args = parser.parse_args(argv)
    if args.root is None:
        import tempfile

        with tempfile.TemporaryDirectory(prefix="gwo-v8-local-") as temporary:
            record = run_local_acceptance(
                root=Path(temporary),
                run_id=args.run_id,
                scenario=args.scenario,
            )
            gc.collect()
    else:
        record = run_local_acceptance(
            root=args.root,
            run_id=args.run_id,
            scenario=args.scenario,
        )
    sys.stdout.write(canonical_json(record) + "\n")
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised by the CLI gate.
    raise SystemExit(main())
