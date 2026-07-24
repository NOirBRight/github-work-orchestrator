from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import shutil
import subprocess
import tempfile

from ._canonical import digest_value


class IntegrationBatchError(RuntimeError):
    def __init__(self, code: str, detail: str):
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class IntegrationBatchMember:
    node_key: str
    integration_node_key: str
    candidate_sha: str
    base_sha: str
    result_digest: str
    evidence_manifest_digest: str


@dataclass(frozen=True)
class IntegrationBatch:
    batch_id: str
    base_sha: str
    batch_sha: str
    members: tuple[IntegrationBatchMember, ...]
    source_ref: str


def _git(repository: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repository), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if result.returncode != 0:
        raise IntegrationBatchError(
            "INTEGRATION_BATCH_GIT_FAILED",
            result.stderr.strip()
            or result.stdout.strip()
            or f"git {' '.join(args)} failed",
        )
    return result.stdout.strip()


class GitIntegrationBatchAssembler:
    """Compose reviewed Candidates behind one deterministic Kernel interface."""

    def __init__(self, repository_path: Path):
        self.repository_path = Path(repository_path).resolve()

    def prepare(
        self,
        *,
        plan_digest: str,
        members: tuple[IntegrationBatchMember, ...],
    ) -> IntegrationBatch:
        ordered = tuple(sorted(members, key=lambda item: item.node_key))
        if not ordered:
            raise IntegrationBatchError(
                "INTEGRATION_BATCH_EMPTY",
                "an Integration Batch requires at least one Candidate",
            )
        base_shas = {member.base_sha for member in ordered}
        if len(base_shas) != 1:
            raise IntegrationBatchError(
                "INTEGRATION_BATCH_BASE_MISMATCH",
                "all Candidates in one Integration Batch must share one base SHA",
            )
        base_sha = ordered[0].base_sha
        for member in ordered:
            ancestry = subprocess.run(
                [
                    "git",
                    "-C",
                    str(self.repository_path),
                    "merge-base",
                    "--is-ancestor",
                    base_sha,
                    member.candidate_sha,
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
            if ancestry.returncode != 0:
                raise IntegrationBatchError(
                    "INTEGRATION_BATCH_CANDIDATE_DIVERGED",
                    (
                        f"Candidate {member.candidate_sha} is not based on "
                        f"{base_sha}"
                    ),
                )
        batch_id = digest_value(
            {
                "plan_digest": plan_digest,
                "base_sha": base_sha,
                "members": [
                    {
                        "node_key": member.node_key,
                        "integration_node_key": member.integration_node_key,
                        "candidate_sha": member.candidate_sha,
                        "result_digest": member.result_digest,
                        "evidence_manifest_digest": (
                            member.evidence_manifest_digest
                        ),
                    }
                    for member in ordered
                ],
            }
        )
        ref = f"refs/gwo-v8/integration-batches/{batch_id}"
        existing = subprocess.run(
            [
                "git",
                "-C",
                str(self.repository_path),
                "rev-parse",
                "--verify",
                "--quiet",
                ref,
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        if existing.returncode == 0:
            batch_sha = existing.stdout.strip()
            if not batch_sha:
                raise IntegrationBatchError(
                    "INTEGRATION_BATCH_REF_INVALID",
                    f"Integration Batch ref has no SHA: {ref}",
                )
            return IntegrationBatch(
                batch_id=batch_id,
                base_sha=base_sha,
                batch_sha=batch_sha,
                members=ordered,
                source_ref=f"git://{ref}",
            )

        if len(ordered) == 1:
            batch_sha = ordered[0].candidate_sha
        else:
            worktree = Path(
                tempfile.mkdtemp(
                    prefix=".gwo-v8-batch-",
                    dir=self.repository_path.parent,
                )
            )
            try:
                _git(
                    self.repository_path,
                    "worktree",
                    "add",
                    "--detach",
                    str(worktree),
                    base_sha,
                )
                _git(
                    worktree,
                    "-c",
                    "user.name=GWO V8 Kernel",
                    "-c",
                    "user.email=gwo-v8-kernel@example.invalid",
                    "merge",
                    "--no-ff",
                    "--no-edit",
                    *(member.candidate_sha for member in ordered),
                )
                batch_sha = _git(worktree, "rev-parse", "HEAD")
            finally:
                subprocess.run(
                    [
                        "git",
                        "-C",
                        str(self.repository_path),
                        "worktree",
                        "remove",
                        "--force",
                        str(worktree),
                    ],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                )
                if worktree.exists():
                    shutil.rmtree(worktree)
        _git(self.repository_path, "update-ref", ref, batch_sha)
        return IntegrationBatch(
            batch_id=batch_id,
            base_sha=base_sha,
            batch_sha=batch_sha,
            members=ordered,
            source_ref=f"git://{ref}",
        )
