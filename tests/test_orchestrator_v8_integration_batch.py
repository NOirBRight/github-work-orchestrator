from __future__ import annotations

from pathlib import Path
import subprocess
import sys


SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "orchestrator" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from gwo_v8.integration_batch import (  # noqa: E402
    GitIntegrationBatchAssembler,
    IntegrationBatchMember,
)


def _git(repository: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repository), *args],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return result.stdout.strip()


def _repository(tmp_path: Path) -> tuple[Path, str]:
    repository = tmp_path / "repository"
    repository.mkdir()
    _git(repository, "init", "-b", "main")
    _git(repository, "config", "user.name", "Integration Batch Test")
    _git(repository, "config", "user.email", "integration-batch@example.invalid")
    (repository / "README.md").write_text("base\n", encoding="utf-8")
    _git(repository, "add", "README.md")
    _git(repository, "commit", "-m", "base")
    return repository, _git(repository, "rev-parse", "HEAD")


def _candidate(
    repository: Path,
    *,
    base_sha: str,
    branch: str,
    path: str,
    content: str,
) -> str:
    _git(repository, "switch", "-c", branch, base_sha)
    (repository / path).write_text(content, encoding="utf-8")
    _git(repository, "add", path)
    _git(repository, "commit", "-m", f"build {path}")
    candidate_sha = _git(repository, "rev-parse", "HEAD")
    _git(repository, "switch", "main")
    return candidate_sha


def _member(
    *,
    ordinal: int,
    base_sha: str,
    candidate_sha: str,
) -> IntegrationBatchMember:
    return IntegrationBatchMember(
        node_key=f"node:work:{ordinal}",
        integration_node_key=f"node:integration:{ordinal}",
        candidate_sha=candidate_sha,
        base_sha=base_sha,
        result_digest=f"result-{ordinal}",
        evidence_manifest_digest=f"evidence-{ordinal}",
    )


def test_batch_assembles_disjoint_candidates_without_moving_target(tmp_path):
    repository, base_sha = _repository(tmp_path)
    alpha = _candidate(
        repository,
        base_sha=base_sha,
        branch="alpha",
        path="alpha.txt",
        content="alpha\n",
    )
    beta = _candidate(
        repository,
        base_sha=base_sha,
        branch="beta",
        path="beta.txt",
        content="beta\n",
    )

    batch = GitIntegrationBatchAssembler(repository).prepare(
        plan_digest="plan-digest",
        members=(
            _member(ordinal=2, base_sha=base_sha, candidate_sha=beta),
            _member(ordinal=1, base_sha=base_sha, candidate_sha=alpha),
        ),
    )

    assert _git(repository, "rev-parse", "main") == base_sha
    assert _git(repository, "show", f"{batch.batch_sha}:alpha.txt") == "alpha"
    assert _git(repository, "show", f"{batch.batch_sha}:beta.txt") == "beta"
    assert [member.node_key for member in batch.members] == [
        "node:work:1",
        "node:work:2",
    ]
    assert set(_git(repository, "show", "-s", "--format=%P", batch.batch_sha).split()) == {
        base_sha,
        alpha,
        beta,
    }


def test_batch_prepare_is_idempotent_and_single_member_reuses_candidate(tmp_path):
    repository, base_sha = _repository(tmp_path)
    candidate_sha = _candidate(
        repository,
        base_sha=base_sha,
        branch="alpha",
        path="alpha.txt",
        content="alpha\n",
    )
    assembler = GitIntegrationBatchAssembler(repository)
    member = _member(
        ordinal=1,
        base_sha=base_sha,
        candidate_sha=candidate_sha,
    )

    first = assembler.prepare(plan_digest="plan-digest", members=(member,))
    second = assembler.prepare(plan_digest="plan-digest", members=(member,))

    assert first == second
    assert first.batch_sha == candidate_sha
