from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace
from pathlib import Path
import subprocess
from typing import Callable, Literal

from gwo_v8._canonical import digest_value
from gwo_v8._batch_integrator_store import (
    HostedResultReceipt,
    SqliteBatchDeliveryJournal,
)
from gwo_v8._batch_integrator_drivers import (
    BatchPublicationReceipt,
    GitBatchDriver,
    GitCliBatchDriver,
    HostedBatchDriver,
    HostedResultObservation,
    LocalCheckReceipt,
    LocalSuiteDriver,
    PullRequestReadback,
    TargetIntegrationReadback,
)
from gwo_v8.batch_integrator import (
    AncestorReadback,
    BatchDeliveryAction,
    BatchDeliveryObservation,
    BatchDeliveryRequest,
    BatchIntegrator,
    BatchIntegratorConfiguration,
    BatchIntegratorError,
    BatchTarget,
    DeliveryAttributionAmbiguous,
    HostedSuiteDefinition,
    LocalSuiteDefinition,
    TargetDeltaReadback,
)
from gwo_v8.batch_patch_identity import PatchIdentityEntry
from gwo_v8.candidate_gate import (
    AcceptedCandidateReceipt,
    CandidateGateError,
    InteractionClassification,
    InteractionKey,
)


_REAL_TARGET_TREES: dict[str, str] = {}


def make_interaction_key(
    value: str = "api:ordinary",
    *,
    classification: InteractionClassification = InteractionClassification.ORDINARY,
) -> InteractionKey:
    return InteractionKey(namespace="test", value=value, classification=classification)


def make_accepted_candidate_receipt(
    *,
    repository: str = "owner/repo",
    campaign_key: str = "campaign:test",
    target_branch: str = "main",
    ticket_key: str = "issue:1",
    candidate_sha: str = "c" * 40,
    accepted_sequence: int = 1,
    base_sha: str = "b" * 40,
    base_tree_oid: str = "1" * 40,
    candidate_tree_oid: str | None = None,
    delivery_identity_digest: str = "d" * 64,
    evidence_digests: tuple[str, ...] = ("e" * 64,),
    diff_record_digest: str = "2" * 64,
    assurance: Literal["standard", "strict"] = "standard",
    interaction_keys: tuple[InteractionKey, ...] | None = None,
    protected_surfaces: tuple[str, ...] = (),
    gitlink_change: bool = False,
) -> AcceptedCandidateReceipt:
    index = accepted_sequence
    actual_candidate_sha = (
        candidate_sha if candidate_sha != "c" * 40 else f"{index + 10:040x}"
    )
    actual_candidate_tree_oid = candidate_tree_oid or f"{index + 100:040x}"
    actual_interaction_keys = (
        interaction_keys
        if interaction_keys is not None
        else (make_interaction_key(f"api:{ticket_key}"),)
    )
    try:
        return AcceptedCandidateReceipt(
            repository=repository,
            campaign_key=campaign_key,
            plan_revision_digest="1" * 64,
            target_branch=target_branch,
            ticket_key=ticket_key,
            work_run_key=f"work-run:{index}",
            integration_node_key=f"integration:{index}",
            accepted_sequence=accepted_sequence,
            base_sha=base_sha,
            base_tree_oid=base_tree_oid,
            candidate_sha=actual_candidate_sha,
            candidate_tree_oid=actual_candidate_tree_oid,
            candidate_receipt_digest=digest_value(
                {
                    "kind": "candidate_receipt",
                    "ticket_key": ticket_key,
                    "candidate_sha": actual_candidate_sha,
                }
            ),
            diff_schema_version="CandidateDiffRecordV1",
            diff_record_digest=diff_record_digest,
            authority_subtree_digest="3" * 64,
            policy_witness_digest="4" * 64,
            review_subject_digest="5" * 64,
            assurance=assurance,
            assurance_requirement_digest=digest_value(
                {"assurance": assurance}
            ),
            check_environment_digest="6" * 64,
            delivery_identity_digest=delivery_identity_digest,
            interaction_keys=actual_interaction_keys,
            protected_surfaces=tuple(sorted(protected_surfaces)),
            gitlink_change=gitlink_change,
            evidence_digests=evidence_digests,
            review_finding_ledger_digest="7" * 64,
        )
    except CandidateGateError as error:
        raise BatchIntegratorError(
            "BATCH_CANDIDATE_INVALID",
            str(error),
        ) from error


def make_batch_request(
    *,
    accepted_candidates: tuple[AcceptedCandidateReceipt, ...],
    stable_action_id: str = "delivery-action:1",
    target_head_sha: str = "7" * 40,
    target_tree_oid: str | None = None,
) -> BatchDeliveryRequest:
    resolved_target_tree_oid = target_tree_oid or _REAL_TARGET_TREES.get(
        target_head_sha, "8" * 40
    )
    return BatchDeliveryRequest(
        stable_action_id=stable_action_id,
        repository="owner/repo",
        campaign_key="campaign:test",
        plan_revision_digest="1" * 64,
        target=BatchTarget(
            repository="owner/repo",
            target_branch="main",
            target_head_sha=target_head_sha,
            target_tree_oid=resolved_target_tree_oid,
            target_facts_digest="9" * 64,
        ),
        accepted_candidates=accepted_candidates,
        local_suite=LocalSuiteDefinition(
            suite_id="local",
            definition_digest="a" * 64,
            command=("py", "-3.13", "-c", "print('batch-local-suite')"),
        ),
        hosted_suites=(
            HostedSuiteDefinition(
                suite_id="hosted",
                hosted_name="GWO Canary CI",
                definition_digest="b" * 64,
            ),
        ),
        writer_generation="writer:test",
        activation_id="activation:test",
    )


def make_batch_target(
    *,
    repository: str = "owner/repo",
    target_branch: str = "main",
    target_head_sha: str = "b" * 40,
    target_tree_oid: str = "8" * 40,
    target_facts_digest: str = "9" * 64,
) -> BatchTarget:
    return BatchTarget(
        repository=repository,
        target_branch=target_branch,
        target_head_sha=target_head_sha,
        target_tree_oid=target_tree_oid,
        target_facts_digest=target_facts_digest,
    )


def make_three_standard_receipts() -> tuple[AcceptedCandidateReceipt, ...]:
    return tuple(
        make_accepted_candidate_receipt(
            ticket_key=f"issue:{index}",
            accepted_sequence=index,
        )
        for index in range(1, 4)
    )


def make_patch_entry(
    path: str,
    *,
    old_path: str | None = None,
    new_path: str | None = None,
    change_kind: Literal["add", "delete", "modify", "type-change"] = "modify",
    old_mode: str = "100644",
    new_mode: str = "100644",
    old_oid: str = "a" * 40,
    new_oid: str = "a" * 40,
    old_object_type: Literal["blob", "gitlink"] = "blob",
    new_object_type: Literal["blob", "gitlink"] = "blob",
) -> PatchIdentityEntry:
    return PatchIdentityEntry(
        old_path=old_path if old_path is not None else path,
        new_path=new_path if new_path is not None else path,
        change_kind=change_kind,
        old_mode=old_mode,
        new_mode=new_mode,
        old_object_type=old_object_type,
        new_object_type=new_object_type,
        old_oid=old_oid,
        new_oid=new_oid,
    )


def make_ancestor_readback(
    ancestor_sha: str,
    descendant_sha: str,
    *,
    is_ancestor: bool = True,
) -> AncestorReadback:
    body = {
        "ancestor_sha": ancestor_sha,
        "descendant_sha": descendant_sha,
        "is_ancestor": is_ancestor,
    }
    return AncestorReadback(
        **body,
        readback_digest=digest_value({"kind": "ancestor-readback.v1", **body}),
    )


def make_target_delta(
    base_sha: str,
    target_head_sha: str,
    *,
    interaction_keys: tuple[InteractionKey, ...] = (),
) -> TargetDeltaReadback:
    protected = tuple(key for key in interaction_keys if key.requires_singleton)
    body = {
        "base_sha": base_sha,
        "target_head_sha": target_head_sha,
        "interaction_keys": [key.canonical() for key in interaction_keys],
        "protected_interaction_keys": [key.canonical() for key in protected],
    }
    return TargetDeltaReadback(
        base_sha=base_sha,
        target_head_sha=target_head_sha,
        interaction_keys=interaction_keys,
        protected_interaction_keys=protected,
        facts_digest=digest_value(body),
        readback_digest=digest_value({"kind": "target-delta-readback.v1", **body}),
    )


def make_batch_action(
    *,
    stable_action_id: str = "delivery-action:1",
    request_digest: str = "a" * 64,
    batch_id: str = "b" * 64,
    batch_sha: str = "c" * 40,
    member_ticket_keys: tuple[str, ...] = ("issue:1",),
) -> BatchDeliveryAction:
    return BatchDeliveryAction(
        stable_action_id=stable_action_id,
        request_digest=request_digest,
        batch_id=batch_id,
        batch_sha=batch_sha,
        member_ticket_keys=member_ticket_keys,
    )


def make_hosted_result_receipt(
    *,
    stable_action_id: str = "delivery-action:1",
    batch_sha: str = "c" * 40,
    suite_id: str = "hosted",
    provider_check_id: str = "check:1",
    outcome: Literal["passed", "code_failure", "infrastructure_failure"] = "passed",
    observation_digest: str = "e" * 64,
) -> HostedResultReceipt:
    body = {
        "stable_action_id": stable_action_id,
        "batch_sha": batch_sha,
        "suite_id": suite_id,
        "provider_check_id": provider_check_id,
        "outcome": outcome,
        "observation_digest": observation_digest,
        "source_ref": "checks:hosted",
    }
    return HostedResultReceipt(
        **body,
        receipt_digest=digest_value(
            {"kind": "hosted_result_receipt.v1", **body}
        ),
    )


class CrashInjected(RuntimeError):
    def __init__(self, boundary: str) -> None:
        self.boundary = boundary
        super().__init__(f"crash injected at {boundary}")


def crash_hook_for(boundary: str | None) -> Callable[[str], None]:
    def hook(observed_boundary: str) -> None:
        if boundary is not None and observed_boundary == boundary:
            raise CrashInjected(observed_boundary)

    return hook


def _run_git(
    repository: Path, *arguments: str, env: dict[str, str] | None = None
) -> str:
    completed = subprocess.run(
        ("git", *arguments),
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )
    return completed.stdout.strip()


def make_disjoint_git_candidates(
    root: Path, *, count: int
) -> tuple[Path, str, tuple[AcceptedCandidateReceipt, ...]]:
    repository = root / "disjoint-repository"
    repository.mkdir(parents=True, exist_ok=True)
    _run_git(repository, "init", "--quiet", "--initial-branch=main")
    _run_git(repository, "config", "user.email", "gwo-tests@example.invalid")
    _run_git(repository, "config", "user.name", "GWO Test Builder")
    (repository / "README.md").write_text("GWO test base\n", encoding="utf-8")
    _run_git(repository, "add", "README.md")
    _run_git(repository, "commit", "--quiet", "-m", "test base")
    target_sha = _run_git(repository, "rev-parse", "HEAD")
    target_tree_oid = _run_git(repository, "rev-parse", "HEAD^{tree}")
    _REAL_TARGET_TREES[target_sha] = target_tree_oid
    candidates: list[AcceptedCandidateReceipt] = []
    for index in range(1, count + 1):
        branch = f"candidate-{index}"
        _run_git(repository, "switch", "--quiet", "-c", branch, target_sha)
        path = repository / f"module-{index}.py"
        path.write_text(f"VALUE = {index}\n", encoding="utf-8")
        _run_git(repository, "add", path.name)
        _run_git(repository, "commit", "--quiet", "-m", f"candidate {index}")
        candidate_sha = _run_git(repository, "rev-parse", "HEAD")
        candidate_tree_oid = _run_git(repository, "rev-parse", "HEAD^{tree}")
        candidates.append(
            make_accepted_candidate_receipt(
                repository="owner/repo",
                ticket_key=f"issue:{index}",
                accepted_sequence=index,
                base_sha=target_sha,
                base_tree_oid=target_tree_oid,
                candidate_sha=candidate_sha,
                candidate_tree_oid=candidate_tree_oid,
            )
        )
    _run_git(repository, "switch", "--quiet", "main")
    return repository, target_sha, tuple(candidates)


def make_advanced_target_candidates(
    root: Path, *, count: int
) -> tuple[Path, str, tuple[AcceptedCandidateReceipt, ...]]:
    repository, original_target_sha, candidates = make_disjoint_git_candidates(
        root, count=count
    )
    _run_git(repository, "switch", "--quiet", "main")
    (repository / "target-only.py").write_text(
        "TARGET_ONLY = True\n", encoding="utf-8"
    )
    _run_git(repository, "add", "target-only.py")
    _run_git(repository, "commit", "--quiet", "-m", "advance target")
    advanced_target_sha = _run_git(repository, "rev-parse", "HEAD")
    _REAL_TARGET_TREES[advanced_target_sha] = _run_git(
        repository, "rev-parse", "HEAD^{tree}"
    )
    assert all(member.base_sha == original_target_sha for member in candidates)
    return repository, advanced_target_sha, candidates


def drop_batch_ref(repository: Path, batch_id: str) -> None:
    _run_git(
        repository,
        "update-ref",
        "-d",
        f"refs/gwo-v8/integration-batches/{batch_id}",
    )


class RecordingGitBatchDriver:
    def __init__(
        self,
        *,
        crash_hook: Callable[[str], None],
        ancestor_is_ancestor: bool = True,
        target_delta_interaction_keys: tuple[InteractionKey, ...] = (),
    ) -> None:
        self.crash_hook = crash_hook
        self.ancestor_is_ancestor = ancestor_is_ancestor
        self.target_delta_interaction_keys = target_delta_interaction_keys
        self.refs: dict[str, str] = {}
        self.compose_calls = 0
        self.clean_base_advance_calls: list[str] = []
        self.recomputed_patch_digest: str | None = None
        self.created_batch_member_sets: list[tuple[str, ...]] = []
        self.preserved_evidence_digests: list[tuple[str, ...]] = []
        self.singleton_member_candidate_shas: list[str] = []
        self.singleton_member_evidence_digests: list[tuple[str, ...]] = []
        self.resume_directives: list[tuple[str, str]] = []
        self.tree_paths: dict[str, set[str]] = {}

    def read_target(self, target: BatchTarget) -> BatchTarget:
        return target

    def read_ancestor(self, ancestor_sha: str, descendant_sha: str) -> AncestorReadback:
        body = {
            "ancestor_sha": ancestor_sha,
            "descendant_sha": descendant_sha,
            "is_ancestor": self.ancestor_is_ancestor,
        }
        return AncestorReadback(
            **body,
            readback_digest=digest_value({"kind": "ancestor-readback.v1", **body}),
        )

    def read_target_delta(
        self, base_sha: str, target: BatchTarget
    ) -> TargetDeltaReadback:
        return make_target_delta(
            base_sha,
            target.target_head_sha,
            interaction_keys=self.target_delta_interaction_keys,
        )

    def read_ref(self, ref: str) -> str | None:
        return self.refs.get(ref)

    def update_ref_cas(self, ref: str, expected_sha: str | None, new_sha: str) -> str:
        current = self.refs.get(ref)
        if current != expected_sha:
            raise BatchIntegratorError(
                "BATCH_REF_CAS_CONFLICT", f"unexpected current SHA for {ref}"
            )
        self.refs[ref] = new_sha
        return new_sha

    def compose_batch(
        self,
        batch_id: str,
        target: BatchTarget,
        members: tuple[AcceptedCandidateReceipt, ...],
    ) -> str:
        self.created_batch_member_sets.append(
            tuple(member.ticket_key for member in members)
        )
        if len(members) > 1:
            self.preserved_evidence_digests.extend(
                member.evidence_digests for member in members
            )
        if len(members) == 1:
            self.singleton_member_candidate_shas.append(members[0].candidate_sha)
            self.singleton_member_evidence_digests.append(members[0].evidence_digests)
        for member in members:
            if (
                member.base_sha != target.target_head_sha
                or member.base_tree_oid != target.target_tree_oid
            ):
                self.clean_base_advance(batch_id, target, member)
        self.compose_calls += 1
        if len(members) == 1:
            batch_sha = members[0].candidate_sha
        else:
            batch_sha = hashlib.sha1(
                digest_value(
                    {
                        "batch_id": batch_id,
                        "target": target.target_head_sha,
                        "members": [member.digest for member in members],
                    }
                ).encode("ascii")
            ).hexdigest()
        self.refs[f"refs/gwo-v8/integration-batches/{batch_id}"] = batch_sha
        self.tree_paths[batch_sha] = {
            f"module-{index}.py" for index in range(1, len(members) + 1)
        }
        self.crash_hook("batch_ref_publication")
        return batch_sha

    def clean_base_advance(
        self,
        batch_id: str,
        target: BatchTarget,
        member: AcceptedCandidateReceipt,
    ):
        from gwo_v8.batch_patch_identity import require_clean_base_advance

        self.clean_base_advance_calls.append(member.ticket_key)
        recomputed = self.recomputed_patch_digest or member.diff_record_digest
        return require_clean_base_advance(
            member=member,
            original_patch_digest=member.diff_record_digest,
            recomputed_patch_digest=recomputed,
            ancestor=self.read_ancestor(member.base_sha, target.target_head_sha),
            target_delta=self.read_target_delta(member.base_sha, target),
            target_tree_oid=target.target_tree_oid,
            advanced_member_tree_oid=member.candidate_tree_oid,
        )

    def tree_contains(self, batch_sha: str, path: str) -> bool:
        return path in self.tree_paths.get(batch_sha, set())


class RecordingLocalSuiteDriver:
    def __init__(self) -> None:
        self.batch_shas: list[str] = []

    def run(self, batch_sha: str, suite: LocalSuiteDefinition):
        from gwo_v8._batch_integrator_drivers import LocalCheckReceipt

        self.batch_shas.append(batch_sha)
        body = {
            "batch_sha": batch_sha,
            "suite_id": suite.suite_id,
            "definition_digest": suite.definition_digest,
            "outcome": "passed",
            "source_ref": f"refs/gwo-v8/integration-batches/{batch_sha}",
        }
        observation_digest = digest_value({"kind": "local-observation.v1", **body})
        return LocalCheckReceipt(
            **body,
            observation_digest=observation_digest,
            receipt_digest=digest_value(
                {
                    "kind": "local-check-receipt.v1",
                    **body,
                    "observation_digest": observation_digest,
                }
            ),
        )


class NoopHostedDriver:
    def read_publication(self, repository, batch_sha):
        return None

    def publish_once(self, repository, batch_sha, manifest_digest):
        raise AssertionError("hosted driver is not part of Task 5")

    def read_pull_request(self, repository, batch_sha):
        raise AssertionError("hosted driver is not part of Task 5")

    def read_hosted_result(self, repository, batch_sha, suite):
        raise AssertionError("hosted driver is not part of Task 5")

    def retry_hosted(self, repository, batch_sha, provider_check_id):
        raise AssertionError("hosted driver is not part of Task 5")

    def integrate_serially(self, repository, batch_sha, target, pull_request):
        raise AssertionError("hosted driver is not part of Task 5")


class RecordingHostedBatchDriver:
    """Deterministic hosted/publication boundary for exact delivery tests."""

    def __init__(
        self,
        *,
        outcomes: tuple[Literal["passed", "code_failure", "infrastructure_failure"], ...],
        publication_batch_sha: str | None,
        identity_mismatch: Literal["suite", "provider"] | None,
        target_merge_method: Literal["merge", "squash", "rebase", "unknown"],
        target_contains_batch: bool,
        delivery_failure: Literal[
            "wrong_batch_sha", "wrong_merge_target", "ambiguous_provider"
        ]
        | None,
    ) -> None:
        self.outcomes = outcomes
        self.publication_batch_sha = publication_batch_sha
        self.identity_mismatch = identity_mismatch
        self.target_merge_method = target_merge_method
        self.target_contains_batch = target_contains_batch
        self.delivery_failure = delivery_failure
        self.publish_calls = 0
        self.hosted_read_calls = 0
        self.integrate_calls = 0
        self.retry_calls = 0
        self.published_shas: list[str] = []
        self.hosted_read_shas: list[str] = []
        self.pull_request_heads: list[str] = []
        self.integrated_shas: list[str] = []
        self.retry_shas: list[str] = []
        self.target_mutations: list[str] = []

    @staticmethod
    def _publication(
        repository: str,
        batch_sha: str,
        evidence_manifest_digest: str,
        source_ref: str,
    ) -> BatchPublicationReceipt:
        body = {
            "repository": repository,
            "batch_sha": batch_sha,
            "branch_ref": "refs/heads/gwo/batches/test",
            "evidence_manifest_digest": evidence_manifest_digest,
            "source_ref": source_ref,
        }
        return BatchPublicationReceipt(
            **body,
            receipt_digest=digest_value({"kind": "batch-publication.v1", **body}),
        )

    def read_publication(
        self, repository: str, batch_sha: str
    ) -> BatchPublicationReceipt | None:
        if self.publication_batch_sha is None:
            return None
        return self._publication(
            repository,
            self.publication_batch_sha,
            "a" * 64,
            "github:publication-readback",
        )

    def publish_once(
        self, repository: str, batch_sha: str, manifest_digest: str
    ) -> BatchPublicationReceipt:
        self.publish_calls += 1
        self.published_shas.append(batch_sha)
        return self._publication(
            repository, batch_sha, manifest_digest, "github:publication"
        )

    def read_pull_request(
        self, repository: str, batch_sha: str
    ) -> PullRequestReadback:
        self.pull_request_heads.append(batch_sha)
        body = {
            "number": 1,
            "repository": repository,
            "head_sha": batch_sha,
            "base_branch": "main",
            "merge_commit_sha": "e" * 40,
            "merge_method": self.target_merge_method,
            "source_ref": "github:pull-request",
        }
        return PullRequestReadback(
            **body,
            readback_digest=digest_value(
                {"kind": "pull-request-readback.v1", **body}
            ),
        )

    def read_hosted_result(
        self,
        repository: str,
        batch_sha: str,
        suite: HostedSuiteDefinition,
    ) -> HostedResultObservation:
        if self.delivery_failure == "ambiguous_provider":
            raise DeliveryAttributionAmbiguous(
                "two provider checks matched the exact hosted suite"
            )
        self.hosted_read_calls += 1
        self.hosted_read_shas.append(batch_sha)
        outcome = self.outcomes[
            min(self.hosted_read_calls - 1, len(self.outcomes) - 1)
        ]
        suite_id = (
            "wrong-suite" if self.identity_mismatch == "suite" else suite.suite_id
        )
        provider_check_id = (
            "wrong-check"
            if self.identity_mismatch == "provider"
            else "check:1"
        )
        return HostedResultObservation(
            repository=repository,
            batch_sha=batch_sha,
            suite_id=suite_id,
            provider_check_id=provider_check_id,
            outcome=outcome,
            observation_digest="e" * 64,
            source_ref="checks:hosted",
        )

    def retry_hosted(
        self, repository: str, batch_sha: str, provider_check_id: str
    ) -> None:
        self.retry_calls += 1
        self.retry_shas.append(batch_sha)

    def integrate_serially(
        self,
        repository: str,
        batch_sha: str,
        target: BatchTarget,
        pull_request: PullRequestReadback,
    ) -> TargetIntegrationReadback:
        self.integrate_calls += 1
        self.integrated_shas.append(batch_sha)
        self.target_mutations.append(batch_sha)
        observed_batch_sha = (
            "f" * 40 if self.delivery_failure == "wrong_batch_sha" else batch_sha
        )
        observed_merge_commit_sha = (
            "d" * 40
            if self.delivery_failure == "wrong_merge_target"
            else "e" * 40
        )
        body = {
            "repository": repository,
            "target_branch": target.target_branch,
            "target_head_sha": "e" * 40,
            "batch_sha": observed_batch_sha,
            "pull_request_number": pull_request.number,
            "pull_request_head_sha": pull_request.head_sha,
            "merge_commit_sha": observed_merge_commit_sha,
            "merge_method": self.target_merge_method,
            "batch_is_ancestor": self.target_contains_batch,
            "source_ref": "github:target-readback",
        }
        return TargetIntegrationReadback(
            **body,
            readback_digest=digest_value({"kind": "target-readback.v1", **body}),
        )


@dataclass
class RecordingDriverSet:
    git: GitBatchDriver
    local: LocalSuiteDriver
    hosted: HostedBatchDriver
    forbidden_boundary_calls: int = 0
    candidategate_calls: int = 0
    review_calls: int = 0
    integrator: BatchIntegrator | None = None

    @property
    def formation_calls(self) -> int:
        return 0 if self.integrator is None else self.integrator.formation_calls

    @property
    def composition_calls(self) -> int:
        return self.git.compose_calls

    @property
    def batch_shas(self) -> list[str]:
        return self.local.batch_shas  # type: ignore[attr-defined]

    @property
    def target_mutations(self) -> list[str]:
        return self.hosted.target_mutations  # type: ignore[attr-defined]

    @property
    def created_batch_member_sets(self) -> list[tuple[str, ...]]:
        return self.git.created_batch_member_sets  # type: ignore[attr-defined]

    @property
    def preserved_evidence_digests(self) -> list[tuple[str, ...]]:
        return self.git.preserved_evidence_digests  # type: ignore[attr-defined]

    @property
    def singleton_member_candidate_shas(self) -> list[str]:
        return self.git.singleton_member_candidate_shas  # type: ignore[attr-defined]

    @property
    def singleton_member_evidence_digests(self) -> list[tuple[str, ...]]:
        return self.git.singleton_member_evidence_digests  # type: ignore[attr-defined]

    @property
    def resume_directives(self) -> list[tuple[str, str]]:
        return self.git.resume_directives  # type: ignore[attr-defined]


def make_integrator(
    repository: Path,
    *,
    hosted_outcomes: tuple[
        Literal["passed", "code_failure", "infrastructure_failure"], ...
    ] = (),
    publication_batch_sha: str | None = None,
    hosted_identity_mismatch: Literal["suite", "provider"] | None = None,
    target_merge_method: Literal["merge", "squash", "rebase", "unknown"] = "merge",
    target_contains_batch: bool = True,
    crash_after: str | None = None,
    delivery_failure: Literal[
        "wrong_batch_sha", "wrong_merge_target", "ambiguous_provider"
    ]
    | None = None,
) -> tuple[BatchIntegrator, RecordingDriverSet]:
    root = Path(repository)
    root.mkdir(parents=True, exist_ok=True)
    crash_hook = crash_hook_for(crash_after)
    if (root / ".git").is_dir():
        git: GitBatchDriver = GitCliBatchDriver(root, crash_hook=crash_hook)
    else:
        git = RecordingGitBatchDriver(crash_hook=crash_hook)
    local = RecordingLocalSuiteDriver()
    hosted = RecordingHostedBatchDriver(
        outcomes=hosted_outcomes or ("passed",),
        publication_batch_sha=publication_batch_sha,
        identity_mismatch=hosted_identity_mismatch,
        target_merge_method=target_merge_method,
        target_contains_batch=target_contains_batch,
        delivery_failure=delivery_failure,
    )
    drivers = RecordingDriverSet(git=git, local=local, hosted=hosted)
    integrator = BatchIntegrator(
        journal=SqliteBatchDeliveryJournal(
            root / "v8.sqlite3", crash_hook=crash_hook
        ),
        git=git,
        local=local,
        hosted=hosted,
        configuration=BatchIntegratorConfiguration(),
    )
    drivers.integrator = integrator
    return integrator, drivers


@dataclass
class CompositionDriverSet:
    git: object
    local: RecordingLocalSuiteDriver


def make_composition_integrator(
    repository: Path,
    *,
    ancestor_is_ancestor: bool = True,
    target_delta_interaction_keys: tuple[InteractionKey, ...] = (),
    crash_after: str | None = None,
):
    root = Path(repository)
    root.mkdir(parents=True, exist_ok=True)
    crash_hook = crash_hook_for(crash_after)
    if (root / ".git").is_dir() and ancestor_is_ancestor and not target_delta_interaction_keys:
        from gwo_v8._batch_integrator_drivers import GitCliBatchDriver

        git = GitCliBatchDriver(root, crash_hook=crash_hook)
    else:
        git = RecordingGitBatchDriver(
            crash_hook=crash_hook,
            ancestor_is_ancestor=ancestor_is_ancestor,
            target_delta_interaction_keys=target_delta_interaction_keys,
        )
    local = RecordingLocalSuiteDriver()
    integrator = BatchIntegrator(
        journal=SqliteBatchDeliveryJournal(root / "v8.sqlite3"),
        git=git,
        local=local,
        hosted=NoopHostedDriver(),
        configuration=BatchIntegratorConfiguration(),
    )
    return integrator, CompositionDriverSet(git=git, local=local)


class BatchRecoveryHarness:
    """Small deterministic owner harness for the #117 recovery contract."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.integrator: BatchIntegrator | None = None
        self.drivers: RecordingDriverSet | None = None
        self.action: BatchDeliveryAction | None = None

    @property
    def retry_shas(self) -> tuple[str, ...]:
        if self.drivers is None:
            return ()
        return tuple(self.drivers.hosted.retry_shas)  # type: ignore[attr-defined]

    def run_outcomes(
        self, *outcomes: str
    ) -> tuple[BatchDeliveryObservation, ...]:
        integrator, drivers = make_integrator(
            self.root,
            hosted_outcomes=tuple(outcomes),  # type: ignore[arg-type]
        )
        action = integrator.prepare(
            make_batch_request(accepted_candidates=make_three_standard_receipts())
        )
        observations = tuple(integrator.execute(action) for _ in outcomes)
        self.integrator = integrator
        self.drivers = drivers
        self.action = action
        return observations

    def run_successful_singleton_fallback(self) -> BatchDeliveryObservation:
        integrator, drivers = make_integrator(
            self.root,
            hosted_outcomes=(
                "code_failure",
                "passed",
                "passed",
                "passed",
            ),
        )
        action = integrator.prepare(
            make_batch_request(accepted_candidates=make_three_standard_receipts())
        )
        observations = tuple(integrator.execute(action) for _ in range(4))
        self.integrator = integrator
        self.drivers = drivers
        self.action = action
        return observations[-1]
